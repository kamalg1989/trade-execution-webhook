-- Point-in-time corporate results-filing calendar, harvested from NSE's
-- corporate-filings endpoint (see backtest/harvest_earnings.py).
--
-- Why only DATES and not EPS numbers: NSE's financial_results endpoint returns
-- filing metadata (symbol, broadcast/filing date, period covered, audited and
-- consolidated flags, XBRL link) for an arbitrary historical date range, but
-- NOT revenue/EPS. The numeric P&L endpoint (results_comparison) only serves
-- roughly the last 5 quarters per symbol, so a genuine 11-year EPS history
-- would require harvesting and parsing ~80k XBRL documents. Dates alone are
-- both obtainable at this scale AND enough to test the two loss-cutting rules
-- we actually care about, without inventing fundamentals we cannot verify.
--
-- IMPORTANT MODELLING CAVEAT for anything built on this table: the row records
-- when results were actually broadcast. Using that date to decide something
-- BEFORE it happens is only legitimate to the extent a trader would genuinely
-- have known the date in advance. Under SEBI LODR, companies must give the
-- exchange prior intimation of a board meeting considering financial results
-- (a small number of working days ahead), so a short lead time of ~2-3 days is
-- defensible; assuming knowledge weeks ahead is NOT, and would be look-ahead
-- bias. Any rule using this table should therefore be tested across a range of
-- lead times, and a rule that only works with a long lead should be distrusted.
CREATE TABLE IF NOT EXISTS earnings_filings (
    id             SERIAL PRIMARY KEY,
    symbol         TEXT NOT NULL,
    broadcast_date DATE NOT NULL,     -- when the result hit the exchange
    period_from    DATE,              -- quarter covered, start
    period_to      DATE,              -- quarter covered, end
    relating_to    TEXT,              -- e.g. "First Quarter"
    audited        TEXT,
    consolidated   TEXT,
    created_at     TIMESTAMPTZ DEFAULT now()
);

-- A company can file the same quarter more than once (standalone + consolidated,
-- or a revision), so the natural key includes the period and the flags.
CREATE UNIQUE INDEX IF NOT EXISTS uq_earnings_filings
  ON earnings_filings (symbol, broadcast_date, period_to, COALESCE(consolidated, ''));

-- The engine's lookup is always "next filing for these symbols after date D".
CREATE INDEX IF NOT EXISTS idx_earnings_symbol_date
  ON earnings_filings (symbol, broadcast_date);
