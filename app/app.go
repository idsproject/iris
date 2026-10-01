// Package app wires together and runs the iris HTTP API: it
// opens the database pool, runs migrations, builds the router
// with its middleware, and serves requests with graceful shutdown.
package app

import (
	"context"
	"errors"
	"log/slog"
	"os"
	"sync"

	"github.com/idsproject/iris/data"

	"github.com/golang-migrate/migrate/v4"
	_ "github.com/golang-migrate/migrate/v4/database/postgres"
	_ "github.com/golang-migrate/migrate/v4/source/file"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Application holds the shared dependencies of the running service:
// the logger, the data-access models, and a wait group for
// tracking background tasks during shutdown.
type Application struct {
	Logger  *slog.Logger
	Queries *data.Queries
	Wg      sync.WaitGroup
}

// Run starts the service: it opens the database pool,
// applies migrations, and serves HTTP until the process is signalled to stop.
// It returns the first error that prevents startup or clean shutdown.
func Run(ctx context.Context) error {
	logger := slog.New(slog.NewTextHandler(os.Stdout, nil))

	dbpool, err := pgxpool.New(context.Background(), os.Getenv("DB_URL"))
	if err != nil {
		logger.Error(err.Error())
		return err
	}
	defer dbpool.Close()

	queries := data.New(dbpool)

	app := &Application{
		Logger:  logger,
		Queries: queries,
	}

	err = app.serve()
	if err != nil {
		logger.Error(err.Error())
		return err
	}

	return nil
}

func RunMigrations() error {
	logger := slog.New(slog.NewTextHandler(os.Stdout, nil))
	migrationDir := os.Getenv("MIGRATIONS_DIR")
	connectionStr := os.Getenv("DB_URL")

	logger.Info("What", "DB_URL", connectionStr)

	var from, to uint

	migration, err := migrate.New(migrationDir, connectionStr)
	if err != nil {
		logger.Error("failed to create migration", "err", err)
		return err
	}

	from, _, err = migration.Version()
	if err != nil && !errors.Is(err, migrate.ErrNilVersion) {
		logger.Error("failed to get from version", "err", err)
		return err
	}

	err = migration.Up()
	if err != nil && !errors.Is(err, migrate.ErrNoChange) {
		logger.Error("failed to run migrations", "err", err)
	}

	to, _, err = migration.Version()
	if err != nil && !errors.Is(err, migrate.ErrNilVersion) {
		logger.Error("failed to get to version", "err", err)
		return err
	}

	logger.Info("Migrations success", "fromVersion", from, "toVersion", to)

	return nil
}
