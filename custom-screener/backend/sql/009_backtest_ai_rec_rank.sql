-- Backtest-only toggle: when true, the AI track re-ranks Gemini's results by
-- recommendation tier (SETUP_READY > EARLY_STAGE > NOT_READY > AVOID) first,
-- confidence as the tie-break within a tier -- instead of the current
-- production behavior of sorting purely by confidence regardless of
-- recommendation (see pipeline.py's analyze_symbols(), left untouched --
-- this re-ranks its output downstream, in engine.py, so production/live
-- trading is completely unaffected). See chat history for the DB evidence
-- motivating this: AVOID-rated analyses average higher confidence than
-- EARLY_STAGE, and historically ~63% of AI-track trades taken were on
-- AVOID-rated stocks.
ALTER TABLE backtest_runs
  ADD COLUMN IF NOT EXISTS ai_respect_recommendation BOOLEAN NOT NULL DEFAULT FALSE;
