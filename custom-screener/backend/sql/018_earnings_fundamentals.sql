-- Point-in-time quarterly fundamentals, parsed from each filing's XBRL.
--
-- This is the piece that was missing for CAN SLIM's "C" and "A" (current
-- quarterly and annual earnings growth). sql/016 harvested WHEN each result was
-- filed; this stores WHAT was reported, by downloading the XBRL document NSE
-- links from every filing and extracting the headline P&L figures.
--
-- Feasibility was probed before building: a sample Ind-AS XBRL
-- (INDAS_46244_..._WEB.xml) is ~23 KB and exposes RevenueFromOperations and
-- ProfitLossForPeriod as plain numeric elements, so parsing is regex/XML-simple
-- rather than a full XBRL-taxonomy problem. At ~113k filings and ~25 KB each
-- this is a multi-day background job, not an interactive one — hence
-- backfill_fundamentals.py runs detached, rate-limited and resumable, and
-- deletes each XML after parsing so the 33 GB of free disk is never consumed.
--
-- WHY THIS TABLE IS SAFE FOR BACKTESTING, unlike a vendor fundamentals dump:
-- every row carries the broadcast_date it was filed on. A backtest may only
-- consult a row where broadcast_date <= the simulated date. That is what makes
-- this genuinely point-in-time and free of the look-ahead bias that ruins
-- naive fundamental backtests (applying today's restated figures to a 2016
-- decision). The engine-side helper must enforce that predicate; the data
-- alone does not.
--
-- Values are stored exactly as reported (absolute rupees in the Ind-AS format
-- sampled), NOT rescaled, so a later units bug can be diagnosed rather than
-- silently baked in. Growth rates should therefore always be computed as
-- ratios between two rows of this table, never mixed with any other source.

ALTER TABLE earnings_filings
  ADD COLUMN IF NOT EXISTS xbrl_url TEXT,
  ADD COLUMN IF NOT EXISTS isin TEXT,
  ADD COLUMN IF NOT EXISTS seq_number TEXT;

CREATE TABLE IF NOT EXISTS earnings_fundamentals (
    id              SERIAL PRIMARY KEY,
    symbol          TEXT NOT NULL,
    period_to       DATE NOT NULL,      -- quarter end the figures cover
    period_from     DATE,
    broadcast_date  DATE NOT NULL,      -- the point-in-time gate; see note above
    consolidated    TEXT,               -- 'Consolidated' / 'Non-Consolidated'
    revenue         NUMERIC(20,2),      -- RevenueFromOperations
    other_income    NUMERIC(20,2),
    net_profit      NUMERIC(20,2),      -- ProfitLossForPeriod
    profit_continuing NUMERIC(20,2),    -- ProfitLossForPeriodFromContinuingOperations
    eps_basic       NUMERIC(14,4),      -- when the filing reports it
    eps_diluted     NUMERIC(14,4),
    xbrl_url        TEXT,
    parse_status    TEXT NOT NULL,      -- 'ok' | 'no_data' | 'error:<kind>'
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- One row per (symbol, quarter, standalone-vs-consolidated) filing.
CREATE UNIQUE INDEX IF NOT EXISTS uq_earnings_fundamentals
  ON earnings_fundamentals (symbol, period_to, COALESCE(consolidated, ''), broadcast_date);

-- The engine's query shape: "latest quarter for this symbol already broadcast
-- as of date D", plus the year-ago quarter for the YoY comparison.
CREATE INDEX IF NOT EXISTS idx_fund_symbol_broadcast
  ON earnings_fundamentals (symbol, broadcast_date);
CREATE INDEX IF NOT EXISTS idx_fund_symbol_period
  ON earnings_fundamentals (symbol, period_to);
