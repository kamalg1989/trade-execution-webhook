-- Migration: Add structural_sl column to sl_positions table
-- Purpose: Persist structural stop loss levels in DB for reliability
-- Status: Missing - causing gaps for stocks without sheet/screener entries

-- Add structural_sl column if it doesn't exist
ALTER TABLE sl_positions
ADD COLUMN IF NOT EXISTS structural_sl NUMERIC(10, 2),
ADD COLUMN IF NOT EXISTS structural_sl_source VARCHAR(20);  -- 'sheet', 'screener', 'manual', or NULL

-- Add index for querying by structural_sl
CREATE INDEX IF NOT EXISTS idx_sl_positions_structural_sl ON sl_positions(structural_sl);
CREATE INDEX IF NOT EXISTS idx_sl_positions_structural_src ON sl_positions(structural_sl_source);

-- Also add to user_trades for tracking purchased positions
ALTER TABLE user_trades
ADD COLUMN IF NOT EXISTS structural_sl NUMERIC(10, 2),
ADD COLUMN IF NOT EXISTS structural_sl_source VARCHAR(20);

CREATE INDEX IF NOT EXISTS idx_user_trades_structural_sl ON user_trades(structural_sl);
