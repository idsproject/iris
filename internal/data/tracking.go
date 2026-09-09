package data

import (
	"context"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type TrackingModel struct {
	DB *pgxpool.Pool
}

type Tracking struct {
	Processed     time.Time
	LibraryID     string
	TransactionID string
    PageCount int
	Paid          bool
	ID            int
}

func (model TrackingModel) InsertTracking(libraryid string, transactionid string, pageCount int, paid bool) error {
	query := "insert into tracking (libraryid, transactionid, pagecount, paid, processed) values ($1, $2, $3, $4, $5)"

	_, err := model.DB.Exec(context.Background(), query, libraryid, transactionid, pageCount, false, time.Now())
	if err != nil {
		return fmt.Errorf("unable to insert %s; into tracking for %s: %w",
			transactionid, libraryid, err)
	}

	return nil
}

func (model TrackingModel) GetReportFromRange(libraryid string, start time.Time, end time.Time) ([]Tracking, error) {
	query := `
    select * from tracking
    where libraryid = '$1'
    and processed >= $2
    and processed <= $3`

	rows, err := model.DB.Query(context.Background(), query, libraryid, start, end)
	if err != nil {
		return nil, fmt.Errorf("unable to query tracking: %w", err)
	}
	defer rows.Close()

	return pgx.CollectRows(rows, pgx.RowToStructByName[Tracking])
}
