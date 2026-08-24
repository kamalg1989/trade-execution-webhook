-- Paper-trading tables for the POSITIONAL momentum strategy (config #823).
-- ADDITIVE ONLY: new tables in trading_platform. Nothing existing is touched,
-- and the live screener/webhook never read these.
--   DROP TABLE paper_fills, paper_positions, paper_equity, paper_rebalance;

CREATE TABLE IF NOT EXISTS paper_rebalance (
    id            serial PRIMARY KEY,
    rebalance_date date NOT NULL UNIQUE,
    n_candidates  integer,
    n_held        integer,
    n_bought      integer,
    n_sold        integer,
    capital       numeric(16,2),
    notes         text,
    created_at    timestamptz DEFAULT now()
);

-- One row per position currently held in the paper book.
CREATE TABLE IF NOT EXISTS paper_positions (
    id            serial PRIMARY KEY,
    symbol        text NOT NULL,
    entry_date    date NOT NULL,
    entry_price   numeric(12,2) NOT NULL,
    quantity      integer NOT NULL,
    entry_rank    integer,
    entry_score   numeric(10,4),
    atr_pct_entry numeric(8,3),
    exit_date     date,
    exit_price    numeric(12,2),
    exit_reason   text,
    realized_pnl  numeric(16,2),
    status        text NOT NULL DEFAULT 'OPEN',
    UNIQUE (symbol, entry_date)
);
CREATE INDEX IF NOT EXISTS idx_paper_pos_status ON paper_positions(status);

-- Daily mark-to-market of the whole paper book.
CREATE TABLE IF NOT EXISTS paper_equity (
    d             date PRIMARY KEY,
    cash          numeric(16,2) NOT NULL,
    positions_mtm numeric(16,2) NOT NULL,
    equity        numeric(16,2) NOT NULL,
    n_open        integer,
    peak_equity   numeric(16,2),
    drawdown_pct  numeric(8,3),
    created_at    timestamptz DEFAULT now()
);
