CREATE TABLE IF NOT EXISTS logs (
    id SERIAL PRIMARY KEY,
    libraryid TEXT,
    transactionid TEXT,
    message TEXT
)
