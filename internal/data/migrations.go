package data

import (
	"errors"
	"fmt"

	"github.com/golang-migrate/migrate/v4"
	_ "github.com/golang-migrate/migrate/v4/database/postgres"
	_ "github.com/golang-migrate/migrate/v4/source/file"
)

// RunMigrations applies all pending up migrations from migrationDir to the
// database at connectionStr. It returns the schema version before and after,
// whether the schema is left in a dirty state, and any error. A missing initial
// version or an already up-to-date schema is not treated as an error.
func RunMigrations(migrationDir, connectionStr string) (uint, uint, bool, error) {
	var from, to uint
	var dirty bool

	migration, err := migrate.New(migrationDir, connectionStr)
	if err != nil {
		return from, to, dirty, fmt.Errorf("failed to create migration: %w", err)
	}

	from, dirty, err = migration.Version()
	if err != nil && !errors.Is(err, migrate.ErrNilVersion) {
		return from, to, dirty, fmt.Errorf("failed to get from version: %w", err)
	}

	err = migration.Up()
	if err != nil && !errors.Is(err, migrate.ErrNoChange) {
		return from, to, dirty, fmt.Errorf("failed to run migrations: %w", err)
	}

	to, dirty, err = migration.Version()
	if err != nil && !errors.Is(err, migrate.ErrNilVersion) {
		return from, to, dirty, fmt.Errorf("failed to get to version: %w", err)
	}

	return from, to, dirty, nil
}
