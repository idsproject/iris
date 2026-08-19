package data

import (
	"context"
	"fmt"

	"github.com/jackc/pgx/v5/pgxpool"
)

// LogsModel provides access to the logs table.
type LogsModel struct {
	DB *pgxpool.Pool
}

// Log is a single row of the logs table.
type Log struct {
	LibraryID     string
	TransactionID string
	Message       string
	ID            int
}

// InsertLog records a log message for the given library and function.
func (model LogsModel) InsertLog(libraryid string, transactionid string, message string) error {
	query := "insert into logs (libraryid, transactionid, message) values ($1, $2, $3)"

	_, err := model.DB.Exec(context.Background(), query, libraryid, transactionid, message)
	if err != nil {
		return fmt.Errorf("unable to insert %s; %s into logs for %s: %w",
			transactionid, message, libraryid, err)
	}

	return nil
}
