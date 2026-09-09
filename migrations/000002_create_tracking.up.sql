CREATE TABLE IF NOT EXISTS tracking (
    id SERIAL PRIMARY KEY,
    libraryid text,
    transactionid text,
    pagecount int,
    paid boolean,
    processed timestamp
);
