-- Index membership + market-cap bucket + sector, fed by
-- market_data_setup/scripts/update_index_membership.py (niftyindices.com CSVs).

CREATE TABLE IF NOT EXISTS index_membership (
    symbol      TEXT NOT NULL,
    index_name  TEXT NOT NULL,          -- NIFTY50/100/200/500, MIDCAP150, SMALLCAP250, MICROCAP250
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, index_name)
);
CREATE INDEX IF NOT EXISTS idx_index_membership_index ON index_membership (index_name);

-- Bucket derived from index membership (single-feed approach):
-- NIFTY100 -> large, MIDCAP150 -> mid, SMALLCAP250 -> small, MICROCAP250 -> micro.
ALTER TABLE symbols_meta ADD COLUMN IF NOT EXISTS mcap_bucket TEXT;
