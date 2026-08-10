-- Minimum position size floor: skip a candidate whose position value
-- (entry price x quantity) falls below this, so flat per-trade costs
-- (DP charge, stamp duty) don't disproportionately tax tiny positions.
-- See cost-drag analysis in chat history (run #38 comparison series).
ALTER TABLE backtest_runs
  ADD COLUMN IF NOT EXISTS min_position_value NUMERIC(12,2) NOT NULL DEFAULT 0;

-- Cap how many symbols per track (quant/AI) are taken per day, independent
-- of the funnel/AI ranking depth itself — lets a run test "fewer, higher-
-- conviction picks" (reduces trade frequency / fixed-cost drag) without
-- changing the underlying ranking logic. Default 3 matches existing
-- hardcoded top-3 behavior.
ALTER TABLE backtest_runs
  ADD COLUMN IF NOT EXISTS max_picks_per_track SMALLINT NOT NULL DEFAULT 3;

-- Which candidate-selection module to use for the QUANT track: 'v1' is the
-- exact production-mirroring funnel.py (unchanged, default); 'v2' is the
-- experimental re-ranked/re-gated funnel_v2.py used to validate whether
-- production's own ranking criteria actually predict forward returns
-- before ever touching screen_gpt.py. Never affects the AI track.
ALTER TABLE backtest_runs
  ADD COLUMN IF NOT EXISTS quant_funnel_variant TEXT NOT NULL DEFAULT 'v1'
    CHECK (quant_funnel_variant IN ('v1', 'v2'));
