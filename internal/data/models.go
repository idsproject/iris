// Package data is the PostgreSQL data-access layer for the iris API.
//
// Each database table is served by a model type holding a pgx connection pool
// and exposing the queries the application needs. [Models] aggregates all of
// those models behind a single value constructed with [NewModels].
package data

import (
	"github.com/jackc/pgx/v5/pgxpool"
)

// Models aggregates every data-access model, giving handlers a single value
// through which to reach the database.
type Models struct {
	Logs LogsModel
}

// NewModels returns a [Models] whose every model shares the given connection
// pool.
func NewModels(db *pgxpool.Pool) Models {
	return Models{
		Logs: LogsModel{DB: db},
	}
}
