-- Quant funnel result cache — mirrors backtest_ai_signals (see 002_backtest.sql)
-- but for the quant track. Caches everything about a (symbol, signal_date)
-- funnel/entry-technique/base-stage result that does NOT depend on a run's
-- capital: entry/sl come from screen_gpt.resolve_entry() (pure OHLCV-derived
-- technicals), target from screen_gpt.compute_target() (function of entry/sl
-- only). Quantity is deliberately NOT cached here — it depends on the run's
-- capital and is computed on the fly from risk_per_share + base_stage at
-- candidate-assembly time (see funnel.py's _size_qty()).
--
-- passed=false rows are cached too (a symbol that failed base-stage or
-- entry-technique resolution on a given day will fail identically on every
-- future run touching that day — capital never enters into that decision),
-- which avoids re-running classify_base_stage()/resolve_entry() over OHLCV
-- for symbols that don't end up going anywhere.
CREATE TABLE IF NOT EXISTS backtest_quant_signals (
  id                SERIAL PRIMARY KEY,
  symbol            TEXT NOT NULL,
  signal_date       DATE NOT NULL,
  passed            BOOLEAN NOT NULL,
  entry             NUMERIC(12,2),
  sl                NUMERIC(12,2),
  entry_type        TEXT,
  base_stage        INTEGER,
  risk_per_share    NUMERIC(12,4),
  target            NUMERIC(12,2),
  ifp_score         NUMERIC(6,3),
  base_range_pct    NUMERIC(8,3),
  created_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_backtest_quant_signals
  ON backtest_quant_signals (symbol, signal_date);
