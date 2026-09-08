// Package app wires together and runs the iris HTTP API: it
// opens the database pool, runs migrations, builds the router
// with its middleware, and serves requests with graceful shutdown.
package app

import (
	"context"
	"log/slog"
	"os"
	"sync"

	"github.com/idsproject/iris/internal/data"

	"github.com/jackc/pgx/v5/pgxpool"
)

// Application holds the shared dependencies of the running service:
// the logger, the data-access models, and a wait group for
// tracking background tasks during shutdown.
type Application struct {
	Logger *slog.Logger
	Models data.Models
	Wg     sync.WaitGroup
}

// GetLogger returns the application logger.
func (app *Application) GetLogger() *slog.Logger {
	return app.Logger
}

// GetModels returns the data-access models.
func (app *Application) GetModels() data.Models {
	return app.Models
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

	app := &Application{
		Logger: logger,
		Models: data.NewModels(dbpool),
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

	from, to, dirty, err := data.RunMigrations(os.Getenv("MIGRATIONS_DIR"), os.Getenv("DB_URL"))
	if err != nil {
		logger.Error("Failed migrations", "err", err)
		return err
	}
	logger.Info("Migrations success", "fromVersion", from, "toVersion", to, "dirty", dirty)

	return nil
}
