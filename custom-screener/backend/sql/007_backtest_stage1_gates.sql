-- Per-run overrides for each of Stage 1's SQL gate thresholds, so a
-- backtest run can loosen/tighten any subset of the production
-- (screen_gpt.py) survivor-gate values independently and compare results.
-- All nullable; NULL (the default) means "use the current screen_gpt.py
-- production value" -- an all-NULL run reproduces funnel.py's gate exactly.
-- Never affects the AI track or production screen_gpt.py.
ALTER TABLE backtest_runs
  ADD COLUMN IF NOT EXISTS gate_min_turnover_cr NUMERIC(10,2),        -- screen_gpt.MIN_DAILY_TURNOVER, Rs cr/day
  ADD COLUMN IF NOT EXISTS gate_max_base_range_pct NUMERIC(6,2),      -- screen_gpt.TECH_MAX_BASE_RANGE, %
  ADD COLUMN IF NOT EXISTS gate_min_vol_mult NUMERIC(6,2),            -- screen_gpt.TECH_VOL_MULT
  ADD COLUMN IF NOT EXISTS gate_min_prior_upmove_pct NUMERIC(6,2),    -- screen_gpt.BASE_MIN_PRIOR_UPMOVE_PCT, %
  ADD COLUMN IF NOT EXISTS gate_max_giveback_pct NUMERIC(6,2),        -- screen_gpt.BASE_MAX_GIVEBACK_PCT, %
  ADD COLUMN IF NOT EXISTS gate_max_vol_dryup_ratio NUMERIC(6,2),     -- screen_gpt.BASE_VOL_DRYUP_MAX_RATIO
  ADD COLUMN IF NOT EXISTS gate_max_dist_from_high_pct NUMERIC(6,2),  -- screen_gpt.NEAR_BREAKOUT_MAX_DISTANCE, %
  ADD COLUMN IF NOT EXISTS gate_min_ifp_score NUMERIC(4,3);           -- screen_gpt.IFP_MIN_SCORE
