-- Per-run overrides for Stage 2 (base-stage classification + entry-technique
-- detection thresholds), mirroring 007's Stage 1 gate-override pattern. All
-- nullable; NULL (the default) means "use the current screen_gpt.py
-- production value". Runs with any of these set bypass the shared
-- backtest_quant_signals cache (see backtest/funnel_stage2.py) since that
-- cache assumes Stage 2 is always computed at the fixed production
-- constants and would otherwise be silently contaminated across runs with
-- different overrides. Never affects production screen_gpt.py.
ALTER TABLE backtest_runs
  ADD COLUMN IF NOT EXISTS stage2_base_stage_max_allowed SMALLINT,       -- screen_gpt.BASE_STAGE_MAX_ALLOWED
  ADD COLUMN IF NOT EXISTS stage2_base_min_width_bars SMALLINT,          -- screen_gpt.BASE_MIN_WIDTH_BARS
  ADD COLUMN IF NOT EXISTS stage2_base_bounce_min_pct NUMERIC(6,2),      -- screen_gpt.BASE_BOUNCE_MIN_PCT, %
  ADD COLUMN IF NOT EXISTS stage2_trend_bar_close_threshold NUMERIC(4,3),-- screen_gpt.TREND_BAR_CLOSE_THRESHOLD
  ADD COLUMN IF NOT EXISTS stage2_pin_bar_max_body_pct NUMERIC(4,3),     -- screen_gpt.PIN_BAR_MAX_BODY_PCT
  ADD COLUMN IF NOT EXISTS stage2_pin_bar_min_lower_wick_pct NUMERIC(4,3), -- screen_gpt.PIN_BAR_MIN_LOWER_WICK_PCT
  ADD COLUMN IF NOT EXISTS stage2_min_bar_range_pct NUMERIC(6,3),        -- screen_gpt.MIN_BAR_RANGE_PCT, %
  ADD COLUMN IF NOT EXISTS stage2_enable_pullback_trigger BOOLEAN,       -- screen_gpt.ENABLE_PULLBACK_TRIGGER
  ADD COLUMN IF NOT EXISTS stage2_enable_breakout_retest_trigger BOOLEAN;-- screen_gpt.ENABLE_BREAKOUT_RETEST_TRIGGER
