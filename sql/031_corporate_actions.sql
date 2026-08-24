-- Detected/approved stock-split & bonus adjustment factors, used to back-adjust
-- ohlcv_data so historical OHLCV is continuous across corporate actions. Without
-- this, an unadjusted split shows up as a fake >40% single-day crash/spike in
-- ohlcv_data, which the backtest engines then treat as a real price move
-- (triggering bogus stop-losses or phantom multi-R winners). See run #607
-- MBAPL/KRISHANA/HEXT/PTL trades for the bug this fixes (2026-08-15).
CREATE TABLE IF NOT EXISTS corporate_actions (
    id              SERIAL PRIMARY KEY,
    symbol          TEXT NOT NULL,
    action_date     DATE NOT NULL,          -- first trading day AFTER the action (post-split close)
    ratio           NUMERIC(10,4) NOT NULL, -- pre_close / post_close, e.g. 5.0 for a 5:1 split
    raw_ratio       NUMERIC(10,4),           -- unrounded, as observed in the data, for audit
    action_type     TEXT NOT NULL DEFAULT 'SPLIT_OR_BONUS',
    classification  TEXT NOT NULL,           -- CORPORATE_ACTION_CANDIDATE | SUSPECTED_DATA_ERROR
    symbols_same_date INTEGER,               -- how many other symbols jumped the same day (cluster signal)
    status          TEXT NOT NULL DEFAULT 'DETECTED', -- DETECTED | APPROVED | APPLIED | REJECTED
    applied_at      TIMESTAMPTZ,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (symbol, action_date)
);

CREATE INDEX IF NOT EXISTS idx_corp_actions_symbol ON corporate_actions(symbol);
CREATE INDEX IF NOT EXISTS idx_corp_actions_status ON corporate_actions(status);
