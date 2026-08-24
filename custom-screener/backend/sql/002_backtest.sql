-- Backtest engine storage. Lives in the same market_data DB as
-- stock_indicators/ohlcv_data (plain tables, not hypertables — row counts
-- are small: a handful of runs, low thousands of trades per run).
-- See /BACKTEST_ENGINE_SPEC.md (repo root) for the full design doc.

CREATE TABLE IF NOT EXISTS backtest_runs (
  id                    SERIAL PRIMARY KEY,
  created_at            TIMESTAMPTZ DEFAULT NOW(),
  completed_at          TIMESTAMPTZ,
  start_date            DATE NOT NULL,
  end_date              DATE NOT NULL,
  universe              TEXT NOT NULL DEFAULT 'NSE_EQ_FULL',
  track_mode            TEXT NOT NULL DEFAULT 'BOTH',     -- QUANT | AI | BOTH
  capital               NUMERIC(15,2) NOT NULL DEFAULT 400000,
  resting_window_days   INTEGER,                          -- NULL = indefinite
  stacking_guard        BOOLEAN NOT NULL DEFAULT FALSE,
  stacking_guard_mode   TEXT,                              -- SKIP | OVERRIDE (only meaningful if stacking_guard)
  exit_config           JSONB NOT NULL DEFAULT '{"breakeven": true, "half_booking": true, "trailing": true, "fixed_target": true}',
  status                TEXT NOT NULL DEFAULT 'RUNNING',   -- RUNNING | COMPLETED | FAILED
  progress_day          INTEGER DEFAULT 0,
  progress_total_days   INTEGER,
  params                JSONB,           -- full gate-threshold/config snapshot for reproducibility
  error                 TEXT,
  notes                 TEXT
);

CREATE TABLE IF NOT EXISTS backtest_trades (
  id                    SERIAL PRIMARY KEY,
  run_id                INTEGER NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
  symbol                TEXT NOT NULL,
  quant_rank            INTEGER,           -- 1-3, set if this was a quant top-3 pick that day
  ai_rank               INTEGER,           -- 1-3, set if this was an AI top-3 pick that day
  signal_date           DATE NOT NULL,
  entry_trigger_price   NUMERIC(12,2) NOT NULL,
  structural_sl         NUMERIC(12,2) NOT NULL,
  target_price          NUMERIC(12,2),
  risk_per_share        NUMERIC(12,2),
  quantity              INTEGER,
  entry_type            TEXT,              -- HH_HL | INSIDE_BAR | PIN_BAR | TREND_BAR
  base_stage            INTEGER,
  ai_confidence         NUMERIC(4,3),
  ai_recommendation     TEXT,
  status                TEXT NOT NULL DEFAULT 'PENDING',
                        -- PENDING | OPEN | CLOSED | UNFILLED_EXPIRED | SUPERSEDED
  entry_fill_date       DATE,
  entry_fill_price      NUMERIC(12,2),
  half_booked           BOOLEAN DEFAULT FALSE,
  trail_sl              NUMERIC(12,2),
  exit_date             DATE,
  exit_price            NUMERIC(12,2),
  exit_reason           TEXT,
                        -- MINUS_8PCT | STRUCTURAL_SL | TRAIL_SL | TARGET_2R | WINDOW_END_MTM | SUPERSEDED
  realized_pnl          NUMERIC(15,2),
  r_multiple            NUMERIC(8,3),
  holding_days          INTEGER,
  meta                  JSONB,
  created_at            TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bt_trades_run ON backtest_trades (run_id);
CREATE INDEX IF NOT EXISTS idx_bt_trades_run_status ON backtest_trades (run_id, status);
CREATE INDEX IF NOT EXISTS idx_bt_trades_symbol ON backtest_trades (symbol);
CREATE INDEX IF NOT EXISTS idx_bt_trades_signal_date ON backtest_trades (run_id, signal_date);

-- AI analysis cache, reused across every run that touches the same
-- symbol+date — isolated from the live ai_analysis_results table on purpose.
CREATE TABLE IF NOT EXISTS backtest_ai_signals (
  id                  SERIAL PRIMARY KEY,
  symbol              TEXT NOT NULL,
  signal_date         DATE NOT NULL,
  prompt_version      TEXT NOT NULL DEFAULT 'v2',
  model               TEXT,
  recommendation      TEXT,
  confidence          NUMERIC(4,3),
  ifp_score           NUMERIC(5,3),
  analysis            JSONB,
  features            JSONB,
  chart_daily_path    TEXT,
  chart_weekly_path   TEXT,
  created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_backtest_ai_signals
  ON backtest_ai_signals (symbol, signal_date, prompt_version);
