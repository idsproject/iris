CREATE TABLE IF NOT EXISTS tracking (
    id SERIAL PRIMARY KEY,
    libraryid text NOT NULL,
    transactionid text NOT NULL,
    pagecount int NOT NULL,
    paid boolean NOT NULL,
    processed timestamp NOT NULL
);
