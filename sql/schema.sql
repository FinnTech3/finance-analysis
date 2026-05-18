CREATE SEQUENCE IF NOT EXISTS transactions_seq START 1;

CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER DEFAULT nextval('transactions_seq') PRIMARY KEY,
    date        DATE NOT NULL,
    amount      DECIMAL(10,2) NOT NULL,
    category    VARCHAR,
    description VARCHAR,
    account     VARCHAR,
    source_file VARCHAR
);

CREATE TABLE IF NOT EXISTS budgets (
    category      VARCHAR PRIMARY KEY,
    monthly_limit DECIMAL(10,2) NOT NULL
);
