-- Forward-return outcome tracking for AI analyses.
-- Filled nightly by `python -m ai_analysis.outcomes` once enough forward bars exist.

ALTER TABLE ai_analysis_results ADD COLUMN IF NOT EXISTS ret_5d  DOUBLE PRECISION;
ALTER TABLE ai_analysis_results ADD COLUMN IF NOT EXISTS ret_20d DOUBLE PRECISION;
ALTER TABLE ai_analysis_results ADD COLUMN IF NOT EXISTS ret_60d DOUBLE PRECISION;
ALTER TABLE ai_analysis_results ADD COLUMN IF NOT EXISTS hit_breakout BOOLEAN;  -- high >= AI breakout within 20 bars
ALTER TABLE ai_analysis_results ADD COLUMN IF NOT EXISTS hit_stop BOOLEAN;      -- low <= AI stop within 20 bars
ALTER TABLE ai_analysis_results ADD COLUMN IF NOT EXISTS outcomes_updated_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_ai_results_outcomes_pending
    ON ai_analysis_results (analysis_date) WHERE ret_60d IS NULL;
