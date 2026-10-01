-- name: InsertTracking :one
INSERT INTO tracking (libraryid, transactionid, pagecount, paid, processed)
VALUES ($1, $2, $3, false, NOW())
RETURNING *;

-- name: GetReportFromRange :many
SELECT * FROM tracking
WHERE libraryid = $1
AND processed >= $2
AND processed <= $3;

-- name: InsertLog :one
INSERT INTO logs (libraryid, transactionid, message)
VALUES ($1, $2, $3)
RETURNING *;
