-- SME / lot-traded flag + series + lot size on symbols_meta.
-- Populated by market_data_setup/scripts/update_symbols_meta.py from the
-- Dhan instrument master (series SM/ST = NSE EMERGE SME board).

ALTER TABLE symbols_meta ADD COLUMN IF NOT EXISTS series TEXT;
ALTER TABLE symbols_meta ADD COLUMN IF NOT EXISTS lot_size INT;
ALTER TABLE symbols_meta ADD COLUMN IF NOT EXISTS is_sme BOOLEAN DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_symbols_meta_is_sme ON symbols_meta (is_sme);
