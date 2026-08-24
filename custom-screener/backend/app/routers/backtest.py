"""Backtest run management + results — see /BACKTEST_ENGINE_SPEC.md (repo
root) for the full design. Runs execute as a detached subprocess
(backtest/runner.py) so the API stays responsive; status/progress is
persisted in backtest_runs, not held in memory, so it's safe across API
restarts.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()

BACKEND_DIR = Path(__file__).resolve().parents[2]  # custom-screener/backend/
LOG_DIR = Path("/tmp/ohm_backtest_logs")
LOG_DIR.mkdir(exist_ok=True)

# Backtest subprocess concurrency, tuned to the VPS's current RAM tier —
# override via env instead of code edit if the droplet is resized again.
# Chart rendering (matplotlib) is what actually OOM-killed the process in
# testing, not the Gemini network call — so MAX_CONCURRENT_RENDER is the
# tight one, MAX_CONCURRENT_AI (symbols overlapping while waiting on Gemini)
# can run well ahead of it.
#   961MB (old $6 droplet):  AI=4  RENDER=1  (verified stable, tested live)
#   2GB   ($12 droplet):     AI=8  RENDER=2  (headroom estimate — watch
#                             `free -h` / dmesg on the first real run after
#                             the resize; dial back toward the 961MB values
#                             if it swaps hard or OOMs)
BACKTEST_MAX_CONCURRENT_AI = os.getenv("BACKTEST_MAX_CONCURRENT_AI", "8")
BACKTEST_MAX_CONCURRENT_RENDER = os.getenv("BACKTEST_MAX_CONCURRENT_RENDER", "2")


class ExitConfig(BaseModel):
    breakeven: bool = True
    half_booking: bool = True
    trailing: bool = True
    fixed_target: bool = True
    # Trend-following trails — each an independent toggle (see simulator.py).
    ema10_trail: bool = False
    ema21_trail: bool = False
    ema50_trail: bool = False
    chandelier_trail: bool = False
    swing_trail: bool = False
    # sql/028-era experiment (run #589 analysis) — reuses the WEEKLY_BREAKOUT
    # strategy's own weekly-MACD-crossover trail rule as an option here. See
    # simulator.py's docstring for the measured avg-R-on-winners comparison.
    macd_trail: bool = False
    # Structural/technical hard exits.
    failed_breakout_exit: bool = False
    swing_break_exit: bool = False
    # 2026-08-17 exit-hygiene experiment — optional fractional R threshold for
    # the half-book (e.g. 1.5 books at +1.5R). None/2 = production +2R ladder,
    # byte-identical. See simulator.py half_booking_r block.
    half_booking_r: float | None = Field(None, ge=1.0, le=3.0)
    # 2026-08-18 MACD-trail tuning + profit-giveback cap (unrealized-capture
    # experiment). All None = original 12/26/9 low-level trail, no cap —
    # byte-identical to every prior run. Ride in exit_config JSON, no schema.
    macd_fast: int | None = Field(None, ge=3, le=30)
    macd_slow: int | None = Field(None, ge=10, le=60)
    macd_sig: int | None = Field(None, ge=3, le=20)
    macd_level: str | None = Field(None, pattern="^(low|close)$")
    giveback_arm_r: float | None = Field(None, ge=1.0, le=10.0)
    giveback_cap_pct: float | None = Field(None, ge=10, le=90)
    # 2026-08-18 win-rate hygiene: breakeven stop parks at entry*(1+buffer%)
    # so a breakeven stop-out covers round-trip friction (scratch, not loss).
    breakeven_buffer_pct: float | None = Field(None, ge=0, le=3)
    # Execution realism. Close-triggered exits (structural/trail SL, failed
    # breakout, time stop, swing break) cannot actually be filled at the close
    # that triggered them — the close is only known once the session is over.
    # With this on they fill at the NEXT session's open, wearing the gap.
    # Intraday-triggered exits (the safety floor, the 2R target, half-booking)
    # are unaffected: those are live orders during the session.
    # Default False so every previously recorded run keeps its meaning.
    next_open_exit: bool = False


class RunCreate(BaseModel):
    start_date: date
    end_date: date
    # sql/020 — which strategy this run executes. BREAKOUT is everything the
    # engine did before; POSITIONAL is the low-turnover momentum book.
    # WEEKLY_BREAKOUT (sql/026) is the weekly consolidation-box strategy —
    # entirely separate engine (backtest/weekly_engine.py), only shares this
    # column + backtest_trades.
    strategy: str = Field(
        "BREAKOUT",
        pattern="^(BREAKOUT|POSITIONAL|PORTFOLIO|WEEKLY_BREAKOUT|SQUEEZE_BREAKOUT|RSI_REVERSION|INDEX_TF)$",
    )
    # Positional-only knobs (ignored for BREAKOUT runs).
    # 'composite_rs' (2026-08-18) = 4-factor cross-sectional RS score; every
    # other value is the original single-column ranking, unchanged.
    pos_momentum: str = Field("pct_chg_6m", pattern="^(pct_chg_(3m|6m|1y)|composite_rs)$")
    # Optional POSITIONAL guards — all None = inert, prior runs reproduce.
    pos_atr_max_pct: float | None = Field(None, gt=0, le=20)
    # screen_gpt signal ports: IFP + penny gates (hard), base tightness (score)
    pos_min_ifp_score: float | None = Field(None, ge=0, le=1)
    pos_min_close: float | None = Field(None, ge=0, le=10000)
    pos_base_range_score_w: float | None = Field(None, ge=0, le=3)
    # H4 information discreteness (2026-08-19). 0/None = inert.
    pos_id_score_w: float | None = Field(None, ge=0, le=3)
    pos_id_lookback: int | None = Field(None)
    # H3 Barroso volatility targeting (2026-08-19). None = inert.
    pos_vol_target_pct: float | None = Field(None, ge=2, le=60)
    pos_vol_lb_days: int | None = Field(None, ge=20, le=504)
    pos_vol_max_lev: float | None = Field(None, ge=0.1, le=2.0)
    # separates the ATR entry filter from the ATR daily exit. None = True = current
    pos_atr_daily_exit: bool | None = Field(None)
    pos_atr_exempt_gain_pct: float | None = Field(None, ge=0, le=200)
    # middle-ground exit architectures (A/B/C/D), all default-inert
    pos_atr_persist_days: int | None = Field(None, ge=1, le=10)
    # per-factor composite weights (Phase 2 factor architecture). All None = 1.0
    pos_w_mom12: float | None = Field(None, ge=0, le=5)
    pos_w_mom6: float | None = Field(None, ge=0, le=5)
    pos_w_mom3: float | None = Field(None, ge=0, le=5)
    pos_w_vadj: float | None = Field(None, ge=0, le=5)
    pos_vadj_base: str | None = Field(None, pattern="^(mom6|mom12|mom3)$")
    pos_trend_ir_w: float | None = Field(None, ge=0, le=5)
    pos_trend_ir_col: str | None = Field(None, pattern="^(126|63)$")
    pos_atr_rel_mult: float | None = Field(None, ge=1.0, le=5.0)
    pos_atr_trim_pct: float | None = Field(None, ge=1, le=100)
    pos_struct_low_days: int | None = Field(None, ge=2, le=60)
    # 2026-08-18 drawdown-reduction program — all inert by default.
    pos_max_sector_pct: float | None = Field(None, ge=5, le=100)
    pos_size_mode: str | None = Field(None, pattern="^(equal|inverse_vol)$")
    pos_breadth_scaling: bool = False
    pos_rs_exit_pct: float | None = Field(None, ge=10, le=90)
    pos_cash_buffer_pct: float | None = Field(None, ge=0, le=50)
    pos_regime_ma_days: int | None = Field(None, ge=20, le=400)
    # hysteresis: require this % above the MA to RE-ENTER (exit stays at the
    # MA). 0/None = plain threshold, prior runs reproduce.
    pos_regime_entry_band_pct: float | None = Field(None, ge=0, le=15)
    pos_cash_annual_pct: float | None = Field(None, ge=0, le=15)
    pos_rebalance_days: int = Field(21, ge=1, le=250)
    pos_top_n: int = Field(10, ge=1, le=50)
    pos_buffer_n: int = Field(20, ge=1, le=100)
    pos_min_turnover_cr: float = 5.0
    # sql/021 — daily-checked stop for the positional book. Default 'none'
    # reproduces the original rebalance-only exits, so old runs stay comparable.
    pos_sl_mode: str = Field("none", pattern="^(none|fixed|trail|atr_trail|sma200|sma50|ema50|ema21)$")
    pos_sl_atr_mult: float | None = Field(None, ge=1.0, le=8.0)
    # 2026-08-20 Martin-port: profit-armed stop + equity-curve entry throttle.
    pos_sl_arm_pct: float | None = Field(None, ge=0, le=100)
    pos_eq_throttle_dd_pct: float | None = Field(None, ge=1, le=50)
    pos_eq_throttle_cut: float | None = Field(None, ge=0.0, le=1.0)
    pos_eq_throttle_mode: str | None = Field(None, pattern="^(step|linear)$")
    pos_b200_mid_cut: float | None = Field(None, ge=0.0, le=1.0)
    pos_b200_band_lo: float | None = Field(None, ge=0, le=100)
    pos_b200_band_hi: float | None = Field(None, ge=0, le=100)
    pos_w_52wh: float | None = Field(None, ge=0, le=5)
    pos_earn_gate_days: int | None = Field(None, ge=1, le=30)
    pos_sl_pct: float = Field(0.0, ge=0, le=50)
    # sql/022 — PORTFOLIO-only. All default to INERT: a risk control that is on
    # by default cannot be measured against a baseline (see BACKTEST_REPORT §9.7).
    pf_vol_mode: str = Field("none", pattern="^(none|pct|abs)$")
    pf_vol_floor: float | None = Field(None, ge=10, le=100)
    pf_max_per_stock_pct: float = Field(100.0, gt=0, le=100)
    pf_max_per_sector_pct: float = Field(100.0, gt=0, le=100)
    pf_max_stocks_per_sector: int = Field(99, ge=1, le=99)
    pf_require_sector: bool = False
    pf_dd_throttle_at: float = Field(0.0, ge=0, le=0.5)
    # sql/023 — how often the funnel generates signals. Only SIGNAL GENERATION
    # is gated; exits, fills and mark-to-market still run every session, so a
    # weekly scan never means a weekly stop-loss.
    signal_cadence: str = Field("daily", pattern="^(daily|weekly|monthly)$")
    signal_scan_day: str = Field("last", pattern="^(first|last)$")
    # sql/024 — require a BUY POINT as well as a trigger candle, and pick the
    # base-stage allocation ladder. Defaults reproduce production exactly.
    entry_v2_buy_points: bool = False
    base_stage_ladder: str = Field("prod", pattern="^(prod|v2)$")
    track_mode: str = Field("BOTH", pattern="^(QUANT|AI|BOTH)$")
    capital: float = 400000
    resting_window_days: int | None = None
    stacking_guard: bool = False
    stacking_guard_mode: str | None = Field(None, pattern="^(SKIP|OVERRIDE)$")
    exit_config: ExitConfig = ExitConfig()
    notes: str | None = None
    # Cost realism + custom safety-SL (backtest_runs columns, see
    # sql/004_backtest_costs_and_rules.sql — defaults mirror the DB defaults).
    safety_sl_pct: float = 8.0
    slippage_pct: float = 0.10
    chandelier_atr_mult: float = 3.0
    # Real Dhan equity-delivery cost model (sql/005_backtest_dhan_costs.sql —
    # Dhan charges zero brokerage on delivery; the old brokerage_per_order=20
    # default was actually modeling Dhan's *intraday* rate). brokerage_per_order
    # is kept at 0 by default and only exists as a knob for a different broker.
    brokerage_per_order: float = 0.0
    stt_pct: float = 0.100
    stamp_duty_pct: float = 0.015
    exchange_charges_pct: float = 0.0030
    dp_charge: float = 14.75
    # Skip a candidate whose position value (entry x qty) falls below this —
    # flat per-trade costs (DP charge, stamp duty) disproportionately tax
    # tiny positions. See sql/006_backtest_min_position.sql.
    min_position_value: float = 0.0
    # Cap picks/day/track (default 3 matches existing behavior) — lower it
    # to test "fewer, higher-conviction trades" against cost drag.
    max_picks_per_track: int = Field(3, ge=1, le=10)
    # 'v1' = production ranking (-ifp_score, base_range_pct asc), unchanged.
    # 'v2' = the already-tested-and-rejected alternate ranking from run
    # #60/#61 (base-range-target distance, turnover asc) — kept available but
    # off by default; independent of the gate_* overrides below.
    quant_funnel_variant: str = Field("v1", pattern="^(v1|v2)$")
    # Stage 1 SQL gate threshold overrides (sql/007_backtest_stage1_gates.sql)
    # — any subset; None (default) = use screen_gpt.py's current production
    # value for that gate. Backtest-only, routed through funnel_v2.py, never
    # touches production screen_gpt.py.
    gate_min_turnover_cr: float | None = None
    gate_max_base_range_pct: float | None = None
    gate_min_vol_mult: float | None = None
    gate_min_prior_upmove_pct: float | None = None
    gate_max_giveback_pct: float | None = None
    gate_max_vol_dryup_ratio: float | None = None
    gate_max_dist_from_high_pct: float | None = None
    gate_min_ifp_score: float | None = None
    # Stage 2 (base-stage classification + entry-technique) overrides
    # (sql/008_backtest_stage2_overrides.sql) — any subset; None (default) =
    # use screen_gpt.py's current production value. Routed through
    # funnel_stage2.py, which bypasses the shared quant-signal cache when
    # any of these are set (see that module's docstring). Never touches
    # production screen_gpt.py.
    stage2_base_stage_max_allowed: int | None = None
    stage2_base_min_width_bars: int | None = None
    stage2_base_bounce_min_pct: float | None = None
    stage2_trend_bar_close_threshold: float | None = None
    stage2_pin_bar_max_body_pct: float | None = None
    stage2_pin_bar_min_lower_wick_pct: float | None = None
    stage2_min_bar_range_pct: float | None = None
    stage2_enable_pullback_trigger: bool | None = None
    stage2_enable_breakout_retest_trigger: bool | None = None
    # sql/009_backtest_ai_rec_rank.sql — when True, the AI track re-ranks
    # Gemini's results by recommendation tier (SETUP_READY > EARLY_STAGE >
    # NOT_READY > AVOID) then confidence, instead of confidence alone.
    # Applied downstream of pipeline.py, which is untouched — backtest-only.
    ai_respect_recommendation: bool = False
    # sql/011 — skip NEW entries on days where % of stocks above their 200SMA
    # is >= this value (late-cycle entries underperform in both validation
    # windows). None = no filter. Gates entries only, never exits.
    entry_breadth_max_pct: float | None = None
    # sql/012 — only enter while breadth is at/above its trailing 20-session
    # average (rising) rather than still falling. Combines with the level cap.
    entry_breadth_require_rising: bool = False
    # sql/013 — position sizing. None = production's hardcoded 0.25% / 10%.
    risk_per_trade_pct: float | None = None
    max_capital_per_trade_pct: float | None = None
    # sql/014 — VCP-style base-contraction gate: only take entries whose
    # range(last 10 bars)/range(prior 15 bars) is <= this. None = no filter.
    max_contraction_ratio: float | None = None
    # sql/015 — cost-edge filters. See that migration for the measured basis.
    min_risk_pct_of_price: float | None = None
    max_holding_days: int | None = None
    # sql/017 — earnings-event rules (short lead times only; see migration).
    avoid_entry_days_before_earnings: int | None = None
    exit_days_before_earnings: int | None = None
    # sql/019 — regime state machine (hysteresis). All three required together.
    regime_ma_days: int | None = None
    regime_confirm_days: int | None = None
    regime_action: str | None = Field(None, pattern="^(block|half)$")
    # sql/026 — WEEKLY_BREAKOUT-only. Account-risk % per trade for the
    # weekly strategy's own position-sizing formula (ignored otherwise).
    weekly_risk_pct: float = Field(1.0, ge=0.1, le=10)
    # sql/028 — BREAKOUT-only experiment (run #589 analysis, 2026-08-14):
    # require a recent weekly consolidation-box breakout (same definition as
    # WEEKLY_BREAKOUT) alongside the daily funnel's own signal, borrowing
    # that strategy's coarser/less noisy breakout definition as an extra
    # entry confirmation. False = no filter, existing runs unaffected.
    require_weekly_box_breakout: bool = False
    weekly_box_lookback_days: int = Field(10, ge=1, le=60)
    # sql/029 — SQUEEZE_BREAKOUT-only (Strategy 2, user spec 2026-08-14).
    # Volume expansion required on the breakout candle vs the prior-20-day
    # average — one of the spec's own named sweep parameters (1.2x/1.5x/2.0x).
    squeeze_volume_multiplier: float = Field(1.5, ge=1.0, le=5.0)
    # sql/029 — RSI_REVERSION-only (Strategy 3, user spec 2026-08-14).
    rsi_entry_threshold: float = Field(35.0, ge=10, le=50)   # spec's 30-vs-35 sweep
    rsi_stop_pct: float = Field(4.5, ge=1, le=15)
    rsi_target_pct: float = Field(5.0, ge=1, le=20)
    # sql/030 — WEEKLY_BREAKOUT-only. Check the stop-breach daily instead of
    # only at week-end (MACD ratchet level itself still updates weekly).
    weekly_daily_exit_check: bool = False
    # sql/031 — WEEKLY_BREAKOUT-only. Size positions off running equity
    # (capital + cumulative realized P&L) instead of fixed starting capital.
    weekly_compounding_sizing: bool = False
    # Compounding position allocation (all strategies)
    compounding_enabled: bool = False
    compounding_min_capital: float = Field(400000, gt=0)
    compounding_mode: str = Field("profit_only", pattern="^(profit_only|drawdown_aware)$")
    # 2026-08-17 — WEEKLY_BREAKOUT-only experimental knobs (see
    # weekly_engine.py's run_weekly_backtest docstring comment for full
    # rationale). 'biweekly' only throttles NEW signal evaluation, not
    # exits. Rotation sells the worst-current-R-multiple OPEN position to
    # fund a new pick when capital is fully committed, instead of just
    # skipping the pick.
    weekly_entry_cadence: str = Field("weekly", pattern="^(weekly|biweekly)$")
    weekly_rotation_enabled: bool = False
    # Phase 2 (2026-08-17, CAGR-optimization) — WEEKLY_BREAKOUT-only exit-
    # ladder additions, ported from the daily engine's breakeven/half_booking
    # exit_config toggles (see ExitConfig above) into weekly_simulator.py's
    # step_exit_weekly. Both default off — an unconfigured run reproduces
    # the original structural-stop + MACD-ratchet-only exit exactly.
    weekly_breakeven_enabled: bool = False
    weekly_half_booking_enabled: bool = False
    # 2026-08-17 quant research — WEEKLY_BREAKOUT candidate ranking.
    # 'box_weeks' reproduces production exactly (longest base first, which
    # trade-level research showed is uncorrelated with outcome). 'composite'
    # ranks by a within-week cross-sectional z-score of low turnover + 3m
    # momentum + distance above the 200SMA. See weekly_engine.COMPOSITE_FACTORS
    # and the rank_mode comment in run_weekly_backtest for the measured basis.
    weekly_rank_mode: str = Field("box_weeks", pattern="^(box_weeks|composite)$")
    # 2026-08-17 — equity-curve circuit breaker (WEEKLY_BREAKOUT). Throttles
    # NEW-entry risk budgets while the realized equity curve is below its peak
    # by dd_pct and/or below its own N-week MA. 'none' = inert default.
    # cut=0.0 pauses new entries entirely; 0.5 halves size. Exits are never
    # throttled. See run_weekly_backtest's throttle_mode comment.
    weekly_equity_throttle_mode: str = Field("none", pattern="^(none|dd_peak|equity_ma|both)$")
    weekly_equity_throttle_dd_pct: float = Field(10.0, ge=1, le=50)
    weekly_equity_throttle_cut: float = Field(0.5, ge=0.0, le=1.0)
    weekly_equity_ma_weeks: int = Field(4, ge=2, le=52)
    # INDEX_TF-only (2026-08-17). Long/flat moving-average trend following on an
    # index proxy -- built as a deliberately uncorrelated diversifier to the
    # single-stock breakout book (measured monthly-return rho = 0.015).
    # 2026-08-17 risk audit — code-enforced guards + stressed-exit slippage.
    # All default None/off so pre-audit runs reproduce byte-identically.
    exit_slippage_pct: float | None = Field(None, ge=0, le=5)
    adv_position_cap_pct: float | None = Field(None, gt=0, le=100)
    compounding_max_capital: float | None = Field(None, gt=0)
    itf_proxy: str = Field("SYNTH_EQW", pattern="^[A-Z_0-9]{2,20}$")
    itf_ma_days: int = Field(200, ge=20, le=400)
    itf_capital_pct: float = Field(95.0, ge=10, le=100)
    itf_cash_annual_pct: float = Field(6.0, ge=0, le=15)


def _pool(request: Request):
    repo = getattr(request.app.state, "repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="DB not ready")
    return repo.pool


@router.post("/backtest/runs")
async def create_run(body: RunCreate, request: Request):
    if body.end_date < body.start_date:
        raise HTTPException(status_code=400, detail="end_date must be >= start_date")
    if body.stacking_guard and not body.stacking_guard_mode:
        raise HTTPException(status_code=400, detail="stacking_guard_mode required when stacking_guard is on")

    pool = _pool(request)
    running = await pool.fetchrow("SELECT id FROM backtest_runs WHERE status = 'RUNNING'")
    if running:
        raise HTTPException(
            status_code=409,
            detail=f"Run #{running['id']} is already in progress — only one run at a time.",
        )

    # Capture full config snapshot in params for reproducibility
    params_snapshot = {
        "notes": body.notes,
        "strategy": body.strategy,
        "capital": body.capital,
        "start_date": str(body.start_date),
        "end_date": str(body.end_date),
        "track_mode": body.track_mode,
        "pos_momentum": body.pos_momentum,
        "pos_rebalance_days": body.pos_rebalance_days,
        "pos_top_n": body.pos_top_n,
        "pos_buffer_n": body.pos_buffer_n,
        "pos_min_turnover_cr": body.pos_min_turnover_cr,
        "pos_sl_mode": body.pos_sl_mode,
        "pos_sl_pct": body.pos_sl_pct,
        "safety_sl_pct": body.safety_sl_pct,
        "slippage_pct": body.slippage_pct,
        "stt_pct": body.stt_pct,
        "stamp_duty_pct": body.stamp_duty_pct,
        "exchange_charges_pct": body.exchange_charges_pct,
        "dp_charge": body.dp_charge,
        "max_picks_per_track": body.max_picks_per_track,
        "signal_cadence": body.signal_cadence,
        "signal_scan_day": body.signal_scan_day,
        "gate_min_turnover_cr": body.gate_min_turnover_cr,
        "gate_max_base_range_pct": body.gate_max_base_range_pct,
        "gate_min_vol_mult": body.gate_min_vol_mult,
        "gate_max_giveback_pct": body.gate_max_giveback_pct,
        "gate_max_vol_dryup_ratio": body.gate_max_vol_dryup_ratio,
        "gate_max_dist_from_high_pct": body.gate_max_dist_from_high_pct,
        "gate_min_ifp_score": body.gate_min_ifp_score,
        "stage2_base_stage_max_allowed": body.stage2_base_stage_max_allowed,
        "stage2_base_min_width_bars": body.stage2_base_min_width_bars,
        "stage2_base_bounce_min_pct": body.stage2_base_bounce_min_pct,
        "risk_per_trade_pct": body.risk_per_trade_pct,
        "max_capital_per_trade_pct": body.max_capital_per_trade_pct,
        "max_contraction_ratio": body.max_contraction_ratio,
        "min_risk_pct_of_price": body.min_risk_pct_of_price,
        "entry_breadth_max_pct": body.entry_breadth_max_pct,
        "entry_breadth_require_rising": body.entry_breadth_require_rising,
        "compounding_enabled": body.compounding_enabled,
        "compounding_min_capital": body.compounding_min_capital,
        "compounding_mode": body.compounding_mode,
        "weekly_compounding_sizing": body.weekly_compounding_sizing,
        "weekly_entry_cadence": body.weekly_entry_cadence,
        "weekly_rotation_enabled": body.weekly_rotation_enabled,
        "weekly_breakeven_enabled": body.weekly_breakeven_enabled,
        "weekly_half_booking_enabled": body.weekly_half_booking_enabled,
        "weekly_rank_mode": body.weekly_rank_mode,
        "weekly_equity_throttle_mode": body.weekly_equity_throttle_mode,
        "weekly_equity_throttle_dd_pct": body.weekly_equity_throttle_dd_pct,
        "weekly_equity_throttle_cut": body.weekly_equity_throttle_cut,
        "weekly_equity_ma_weeks": body.weekly_equity_ma_weeks,
        "itf_proxy": body.itf_proxy,
        "itf_ma_days": body.itf_ma_days,
        "itf_capital_pct": body.itf_capital_pct,
        "exit_slippage_pct": body.exit_slippage_pct,
        "adv_position_cap_pct": body.adv_position_cap_pct,
        "compounding_max_capital": body.compounding_max_capital,
    }

    row = await pool.fetchrow(
        """
        INSERT INTO backtest_runs
          (start_date, end_date, track_mode, capital, resting_window_days,
           stacking_guard, stacking_guard_mode, exit_config, status, params,
           safety_sl_pct, slippage_pct, brokerage_per_order, chandelier_atr_mult,
           stt_pct, stamp_duty_pct, exchange_charges_pct, dp_charge, min_position_value,
           max_picks_per_track, quant_funnel_variant,
           gate_min_turnover_cr, gate_max_base_range_pct, gate_min_vol_mult,
           gate_min_prior_upmove_pct, gate_max_giveback_pct, gate_max_vol_dryup_ratio,
           gate_max_dist_from_high_pct, gate_min_ifp_score,
           stage2_base_stage_max_allowed, stage2_base_min_width_bars, stage2_base_bounce_min_pct,
           stage2_trend_bar_close_threshold, stage2_pin_bar_max_body_pct,
           stage2_pin_bar_min_lower_wick_pct, stage2_min_bar_range_pct,
           stage2_enable_pullback_trigger, stage2_enable_breakout_retest_trigger,
           ai_respect_recommendation, entry_breadth_max_pct, entry_breadth_require_rising,
           risk_per_trade_pct, max_capital_per_trade_pct, max_contraction_ratio,
           min_risk_pct_of_price, max_holding_days,
           avoid_entry_days_before_earnings, exit_days_before_earnings,
           regime_ma_days, regime_confirm_days, regime_action,
           strategy, pos_momentum, pos_rebalance_days, pos_top_n, pos_buffer_n,
           pos_min_turnover_cr, pos_sl_mode, pos_sl_pct,
           pf_vol_mode, pf_vol_floor, pf_max_per_stock_pct, pf_max_per_sector_pct,
           pf_max_stocks_per_sector, pf_require_sector, pf_dd_throttle_at,
           signal_cadence, signal_scan_day, entry_v2_buy_points, base_stage_ladder,
           weekly_risk_pct, require_weekly_box_breakout, weekly_box_lookback_days,
           squeeze_volume_multiplier, rsi_entry_threshold, rsi_stop_pct, rsi_target_pct,
           weekly_daily_exit_check, weekly_compounding_sizing,
           compounding_enabled, compounding_mode, compounding_min_capital,
           weekly_entry_cadence, weekly_rotation_enabled,
           weekly_breakeven_enabled, weekly_half_booking_enabled, weekly_rank_mode,
           weekly_equity_throttle_mode, weekly_equity_throttle_dd_pct,
           weekly_equity_throttle_cut, weekly_equity_ma_weeks,
           itf_proxy, itf_ma_days, itf_capital_pct, itf_cash_annual_pct,
           exit_slippage_pct, adv_position_cap_pct, compounding_max_capital,
           pos_atr_max_pct, pos_regime_ma_days, pos_cash_annual_pct,
           pos_max_sector_pct, pos_size_mode, pos_breadth_scaling,
           pos_rs_exit_pct, pos_cash_buffer_pct, pos_sl_atr_mult,
           pos_regime_entry_band_pct,
           pos_min_ifp_score, pos_min_close, pos_base_range_score_w,
           pos_id_score_w, pos_id_lookback,
           pos_vol_target_pct, pos_vol_lb_days, pos_vol_max_lev,
           pos_atr_daily_exit, pos_atr_exempt_gain_pct,
           pos_atr_persist_days, pos_atr_rel_mult, pos_atr_trim_pct, pos_struct_low_days,
           pos_w_mom12, pos_w_mom6, pos_w_mom3, pos_w_vadj, pos_vadj_base,
           pos_trend_ir_w, pos_trend_ir_col,
           pos_sl_arm_pct, pos_eq_throttle_dd_pct, pos_eq_throttle_cut,
           pos_eq_throttle_mode, pos_b200_mid_cut, pos_b200_band_lo, pos_b200_band_hi,
           pos_w_52wh, pos_earn_gate_days)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'RUNNING',$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,
                $21,$22,$23,$24,$25,$26,$27,$28,
                $29,$30,$31,$32,$33,$34,$35,$36,$37,$38,$39,$40,$41,$42,$43,$44,$45,$46,$47,$48,$49,$50,$51,$52,$53,$54,$55,$56,
                $57,$58,$59,$60,$61,$62,$63,$64,$65,$66,$67,$68,$69,$70,$71,$72,$73,$74,$75,$76,$77,$78,
                $79,$80,$81,$82,$83,$84,$85,$86,
                $87,$88,$89,$90,$91,$92,$93,$94,$95,$96,$97,$98,$99,$100,
                $101,$102,$103,$104,$105,$106,$107,$108,$109,$110,$111,$112,
                $113,$114,$115,$116,$117,$118,$119,$120,$121,
                $122,$123,$124,$125,$126,$127,$128,$129,$130,$131,$132,$133,$134,$135,$136,$137)
        RETURNING id
        """,
        body.start_date, body.end_date, body.track_mode, body.capital,
        body.resting_window_days, body.stacking_guard, body.stacking_guard_mode,
        json.dumps(body.exit_config.model_dump()),
        json.dumps(params_snapshot),
        body.safety_sl_pct, body.slippage_pct, body.brokerage_per_order, body.chandelier_atr_mult,
        body.stt_pct, body.stamp_duty_pct, body.exchange_charges_pct, body.dp_charge,
        body.min_position_value, body.max_picks_per_track, body.quant_funnel_variant,
        body.gate_min_turnover_cr, body.gate_max_base_range_pct, body.gate_min_vol_mult,
        body.gate_min_prior_upmove_pct, body.gate_max_giveback_pct, body.gate_max_vol_dryup_ratio,
        body.gate_max_dist_from_high_pct, body.gate_min_ifp_score,
        body.stage2_base_stage_max_allowed, body.stage2_base_min_width_bars, body.stage2_base_bounce_min_pct,
        body.stage2_trend_bar_close_threshold, body.stage2_pin_bar_max_body_pct,
        body.stage2_pin_bar_min_lower_wick_pct, body.stage2_min_bar_range_pct,
        body.stage2_enable_pullback_trigger, body.stage2_enable_breakout_retest_trigger,
        body.ai_respect_recommendation, body.entry_breadth_max_pct,
        body.entry_breadth_require_rising,
        body.risk_per_trade_pct, body.max_capital_per_trade_pct,
        body.max_contraction_ratio,
        body.min_risk_pct_of_price, body.max_holding_days,
        body.avoid_entry_days_before_earnings, body.exit_days_before_earnings,
        body.regime_ma_days, body.regime_confirm_days, body.regime_action,
        body.strategy, body.pos_momentum, body.pos_rebalance_days, body.pos_top_n,
        body.pos_buffer_n, body.pos_min_turnover_cr,
        body.pos_sl_mode, body.pos_sl_pct,
        body.pf_vol_mode, body.pf_vol_floor, body.pf_max_per_stock_pct,
        body.pf_max_per_sector_pct, body.pf_max_stocks_per_sector,
        body.pf_require_sector, body.pf_dd_throttle_at,
        body.signal_cadence, body.signal_scan_day,
        body.entry_v2_buy_points, body.base_stage_ladder,
        body.weekly_risk_pct, body.require_weekly_box_breakout, body.weekly_box_lookback_days,
        body.squeeze_volume_multiplier, body.rsi_entry_threshold,
        body.rsi_stop_pct, body.rsi_target_pct,
        body.weekly_daily_exit_check, body.weekly_compounding_sizing,
        body.compounding_enabled, body.compounding_mode, body.compounding_min_capital,
        body.weekly_entry_cadence, body.weekly_rotation_enabled,
        body.weekly_breakeven_enabled, body.weekly_half_booking_enabled,
        body.weekly_rank_mode,
        body.weekly_equity_throttle_mode, body.weekly_equity_throttle_dd_pct,
        body.weekly_equity_throttle_cut, body.weekly_equity_ma_weeks,
        body.itf_proxy, body.itf_ma_days, body.itf_capital_pct, body.itf_cash_annual_pct,
        body.exit_slippage_pct, body.adv_position_cap_pct, body.compounding_max_capital,
        body.pos_atr_max_pct, body.pos_regime_ma_days, body.pos_cash_annual_pct,
        body.pos_max_sector_pct, body.pos_size_mode, body.pos_breadth_scaling,
        body.pos_rs_exit_pct, body.pos_cash_buffer_pct, body.pos_sl_atr_mult,
        body.pos_regime_entry_band_pct,
        body.pos_min_ifp_score, body.pos_min_close, body.pos_base_range_score_w,
        body.pos_id_score_w, body.pos_id_lookback,
        body.pos_vol_target_pct, body.pos_vol_lb_days, body.pos_vol_max_lev,
        body.pos_atr_daily_exit, body.pos_atr_exempt_gain_pct,
        body.pos_atr_persist_days, body.pos_atr_rel_mult,
        body.pos_atr_trim_pct, body.pos_struct_low_days,
        body.pos_w_mom12, body.pos_w_mom6, body.pos_w_mom3,
        body.pos_w_vadj, body.pos_vadj_base,
        body.pos_trend_ir_w, body.pos_trend_ir_col,
        body.pos_sl_arm_pct, body.pos_eq_throttle_dd_pct, body.pos_eq_throttle_cut,
        body.pos_eq_throttle_mode,
        body.pos_b200_mid_cut, body.pos_b200_band_lo, body.pos_b200_band_hi,
        body.pos_w_52wh, body.pos_earn_gate_days,
    )
    run_id = row["id"]

    env = {
        **os.environ,
        "MAX_CONCURRENT_AI": BACKTEST_MAX_CONCURRENT_AI,
        "MAX_CONCURRENT_RENDER": BACKTEST_MAX_CONCURRENT_RENDER,
    }

    log_path = LOG_DIR / f"run_{run_id}.log"
    with open(log_path, "wb") as logf:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "backtest.runner", "--run-id", str(run_id),
            cwd=str(BACKEND_DIR), stdout=logf, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True, env=env,
        )
    # Deliberately not awaited — runs detached. If the process dies before
    # writing COMPLETED/FAILED (e.g. OOM-killed), the run just sits at
    # RUNNING forever; see the log file for diagnosis, mark FAILED by hand.
    del proc

    return {"id": run_id, "status": "RUNNING"}


# The runs list is polled by the UI, so it must stay cheap: the old version
# ran a per-open-trade LATERAL last-close lookup across 250 runs on every
# poll, which saturated the 1-vCPU box. Now: one runs query, one grouped
# aggregate, one last-close batch per DISTINCT end_date, and a short TTL
# cache so concurrent tabs and the poll interval share one computation.
_RUNS_CACHE: dict = {"key": None, "at": 0.0, "data": None}


@router.get("/backtest/runs")
async def list_runs(request: Request, limit: int = 100):
    import time as _time
    limit = max(1, min(int(limit), 500))
    now = _time.monotonic()
    if _RUNS_CACHE["data"] is not None and _RUNS_CACHE["key"] == limit \
            and now - _RUNS_CACHE["at"] < 10.0:
        return _RUNS_CACHE["data"]
    pool = _pool(request)
    rows = await pool.fetch(
        "SELECT * FROM backtest_runs ORDER BY created_at DESC LIMIT $1", limit)
    ids = [r["id"] for r in rows]
    aggs = {a["run_id"]: a for a in await pool.fetch(
        """SELECT run_id, count(*) AS trade_count,
                  COALESCE(SUM(realized_pnl) FILTER (WHERE status = 'CLOSED'), 0)
                    AS realized_pnl
           FROM backtest_trades WHERE run_id = ANY($1) GROUP BY run_id""", ids)}
    open_tr = await pool.fetch(
        """SELECT t.run_id, t.symbol, t.entry_fill_price, t.quantity, r.end_date
           FROM backtest_trades t JOIN backtest_runs r ON r.id = t.run_id
           WHERE t.run_id = ANY($1) AND t.status = 'OPEN'
             AND t.entry_fill_price IS NOT NULL""", ids)
    unreal: dict = {}
    if open_tr:
        by_end: dict = {}
        for t in open_tr:
            by_end.setdefault(t["end_date"], set()).add(t["symbol"])
        px: dict = {}
        for end_d, syms in by_end.items():
            for r in await pool.fetch(
                """SELECT DISTINCT ON (symbol) symbol, close FROM ohlcv_data
                   WHERE symbol = ANY($1)
                     AND time < ($2::date + INTERVAL '1 day')
                   ORDER BY symbol, time DESC""", list(syms), end_d):
                px[(end_d, r["symbol"])] = float(r["close"])
        for t in open_tr:
            c = px.get((t["end_date"], t["symbol"]))
            if c is not None:
                unreal[t["run_id"]] = unreal.get(t["run_id"], 0.0) + \
                    (c - float(t["entry_fill_price"])) * t["quantity"]
    out = []
    for r in rows:
        d = dict(r)
        # The list never renders the full equity curve / calendar table —
        # serializing them added megabytes to every poll. Detail endpoints
        # (/runs/{id}, /summary) still return them.
        d["pf_equity_curve"] = None
        d["pf_calendar"] = None
        a = aggs.get(r["id"])
        d["trade_count"] = int(a["trade_count"]) if a else 0
        d["realized_pnl"] = float(a["realized_pnl"]) if a else 0.0
        d["unrealized_pnl"] = round(unreal.get(r["id"], 0.0), 2)
        out.append(_run_to_json(d))
    _RUNS_CACHE.update(key=limit, at=now, data=out)
    return out


@router.get("/backtest/runs/{run_id}")
async def get_run(run_id: int, request: Request):
    pool = _pool(request)
    row = await pool.fetchrow("SELECT * FROM backtest_runs WHERE id = $1", run_id)
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    return _run_to_json(row)


@router.post("/backtest/runs/{run_id}/cancel")
async def cancel_run(run_id: int, request: Request):
    """Stop a RUNNING run (user-requested, or to clear a stuck one). Best-effort
    kills the detached subprocess by cmdline match, then unconditionally marks
    the row FAILED — a run can be stuck at RUNNING with no process actually
    alive (e.g. a `systemctl restart custom-screener-api` during a deploy
    kills it too: systemd's default KillMode=control-group tears down the
    whole service cgroup, including detached children, on restart), so the
    DB update must not depend on the kill finding anything."""
    pool = _pool(request)
    row = await pool.fetchrow("SELECT status FROM backtest_runs WHERE id = $1", run_id)
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    if row["status"] != "RUNNING":
        raise HTTPException(status_code=400, detail=f"Run #{run_id} is {row['status']}, not RUNNING")

    proc = await asyncio.create_subprocess_exec(
        "pkill", "-9", "-f", f"backtest.runner --run-id {run_id}"
    )
    await proc.wait()

    await pool.execute(
        "UPDATE backtest_runs SET status='FAILED', error=$2, completed_at=NOW() WHERE id=$1",
        run_id, "Cancelled by user",
    )
    return {"id": run_id, "status": "FAILED"}


def _portfolio_pnl(d: dict) -> dict:
    """Realized / unrealized / total, with the PORTFOLIO case reconciled.

    BREAKOUT and POSITIONAL runs keep the SQL decomposition: they have no
    engine-level equity figure to reconcile against, so realized + unrealized is
    the best available and total is their sum.

    A PORTFOLIO run does have one. `pf_final_equity` is what the continuous
    simulation actually ended with, having paid every charge, so:

        total      = pf_final_equity - capital        (authoritative)
        realized   = sum of closed-trade P&L          (authoritative)
        unrealized = total - realized                 (residual, so it balances)

    Deriving unrealized as the residual is deliberate. The alternative — storing
    entry costs on open trade rows and re-deriving — would leave two independent
    computations of the same quantity free to drift apart, which is how the
    Rs.1,860 discrepancy arose in the first place. Here the three figures
    reconcile by construction, and a run whose stored equity is missing simply
    falls back to the generic decomposition rather than reporting a wrong total.
    """
    real = float(d["realized_pnl"]) if d.get("realized_pnl") is not None else None
    unreal = float(d["unrealized_pnl"]) if d.get("unrealized_pnl") is not None else None
    total = real + unreal if real is not None and unreal is not None else None

    if (d.get("strategy") == "PORTFOLIO" and d.get("pf_final_equity") is not None
            and d.get("capital") is not None and real is not None):
        total = float(d["pf_final_equity"]) - float(d["capital"])
        unreal = total - real

    return {"realizedPnl": real, "unrealizedPnl": unreal, "totalPnl": total}


def _run_to_json(r) -> dict:
    d = dict(r)
    exit_cfg = d.get("exit_config")
    if isinstance(exit_cfg, str):
        exit_cfg = json.loads(exit_cfg)
    params = d.get("params")
    if isinstance(params, str):
        params = json.loads(params)

    def _f(v):
        """NUMERIC -> float, preserving NULL as None. The pf_* metrics are NULL
        on every BREAKOUT/POSITIONAL run, and coercing those to 0.0 would make
        the UI render a real-looking 0% CAGR on runs that never computed one."""
        return float(v) if v is not None else None

    def _j(v):
        return json.loads(v) if isinstance(v, str) else v

    return {
        "id": d["id"], "createdAt": d["created_at"].isoformat() if d.get("created_at") else None,
        "completedAt": d["completed_at"].isoformat() if d.get("completed_at") else None,
        "startDate": str(d["start_date"]), "endDate": str(d["end_date"]),
        "universe": d["universe"], "trackMode": d["track_mode"], "capital": float(d["capital"]),
        "restingWindowDays": d["resting_window_days"], "stackingGuard": d["stacking_guard"],
        "stackingGuardMode": d["stacking_guard_mode"], "exitConfig": exit_cfg,
        "status": d["status"], "progressDay": d["progress_day"], "progressTotalDays": d["progress_total_days"],
        "error": d["error"], "params": params, "tradeCount": d.get("trade_count"),
        # Present only on the list endpoint (see list_runs) — null elsewhere.
        # For a PORTFOLIO run the ENGINE's final equity is authoritative and the
        # SQL decomposition is not. The generic unrealized formula is
        #     (last_close - entry_fill_price) * quantity
        # which omits the buy-side charges already deducted from cash at entry,
        # because those are not stored on an open trade row. Measured on the
        # continuous run that overstated total P&L by Rs.1,860 across 16 open
        # positions (~Rs.116 each — exactly one buy-side leg cost).
        #
        # So total is taken from the engine, and unrealized is derived as the
        # residual. That makes realized + unrealized == total EXACTLY by
        # construction, and pushes the open-position entry costs into the
        # unrealized figure where they belong. See _portfolio_pnl below.
        **_portfolio_pnl(d),
        "safetySlPct": float(d["safety_sl_pct"]) if d.get("safety_sl_pct") is not None else None,
        "slippagePct": float(d["slippage_pct"]) if d.get("slippage_pct") is not None else None,
        "brokeragePerOrder": float(d["brokerage_per_order"]) if d.get("brokerage_per_order") is not None else None,
        "chandelierAtrMult": float(d["chandelier_atr_mult"]) if d.get("chandelier_atr_mult") is not None else None,
        "sttPct": float(d["stt_pct"]) if d.get("stt_pct") is not None else None,
        "stampDutyPct": float(d["stamp_duty_pct"]) if d.get("stamp_duty_pct") is not None else None,
        "exchangeChargesPct": float(d["exchange_charges_pct"]) if d.get("exchange_charges_pct") is not None else None,
        "dpCharge": float(d["dp_charge"]) if d.get("dp_charge") is not None else None,
        "minPositionValue": float(d["min_position_value"]) if d.get("min_position_value") is not None else None,
        "maxPicksPerTrack": d.get("max_picks_per_track"),
        "quantFunnelVariant": d.get("quant_funnel_variant"),
        "gateMinTurnoverCr": float(d["gate_min_turnover_cr"]) if d.get("gate_min_turnover_cr") is not None else None,
        "gateMaxBaseRangePct": float(d["gate_max_base_range_pct"]) if d.get("gate_max_base_range_pct") is not None else None,
        "gateMinVolMult": float(d["gate_min_vol_mult"]) if d.get("gate_min_vol_mult") is not None else None,
        "gateMinPriorUpmovePct": float(d["gate_min_prior_upmove_pct"]) if d.get("gate_min_prior_upmove_pct") is not None else None,
        "gateMaxGivebackPct": float(d["gate_max_giveback_pct"]) if d.get("gate_max_giveback_pct") is not None else None,
        "gateMaxVolDryupRatio": float(d["gate_max_vol_dryup_ratio"]) if d.get("gate_max_vol_dryup_ratio") is not None else None,
        "gateMaxDistFromHighPct": float(d["gate_max_dist_from_high_pct"]) if d.get("gate_max_dist_from_high_pct") is not None else None,
        "gateMinIfpScore": float(d["gate_min_ifp_score"]) if d.get("gate_min_ifp_score") is not None else None,
        "stage2BaseStageMaxAllowed": d.get("stage2_base_stage_max_allowed"),
        "stage2BaseMinWidthBars": d.get("stage2_base_min_width_bars"),
        "stage2BaseBouncePct": float(d["stage2_base_bounce_min_pct"]) if d.get("stage2_base_bounce_min_pct") is not None else None,
        "stage2TrendBarCloseThreshold": float(d["stage2_trend_bar_close_threshold"]) if d.get("stage2_trend_bar_close_threshold") is not None else None,
        "stage2PinBarMaxBodyPct": float(d["stage2_pin_bar_max_body_pct"]) if d.get("stage2_pin_bar_max_body_pct") is not None else None,
        "stage2PinBarMinLowerWickPct": float(d["stage2_pin_bar_min_lower_wick_pct"]) if d.get("stage2_pin_bar_min_lower_wick_pct") is not None else None,
        "stage2MinBarRangePct": float(d["stage2_min_bar_range_pct"]) if d.get("stage2_min_bar_range_pct") is not None else None,
        "stage2EnablePullbackTrigger": d.get("stage2_enable_pullback_trigger"),
        "stage2EnableBreakoutRetestTrigger": d.get("stage2_enable_breakout_retest_trigger"),
        "aiRespectRecommendation": d.get("ai_respect_recommendation"),
        "entryBreadthMaxPct": float(d["entry_breadth_max_pct"]) if d.get("entry_breadth_max_pct") is not None else None,
        "entryBreadthRequireRising": d.get("entry_breadth_require_rising"),
        "riskPerTradePct": float(d["risk_per_trade_pct"]) if d.get("risk_per_trade_pct") is not None else None,
        "maxCapitalPerTradePct": float(d["max_capital_per_trade_pct"]) if d.get("max_capital_per_trade_pct") is not None else None,
        "maxContractionRatio": float(d["max_contraction_ratio"]) if d.get("max_contraction_ratio") is not None else None,
        "minRiskPctOfPrice": float(d["min_risk_pct_of_price"]) if d.get("min_risk_pct_of_price") is not None else None,
        "maxHoldingDays": d.get("max_holding_days"),
        "avoidEntryDaysBeforeEarnings": d.get("avoid_entry_days_before_earnings"),
        "exitDaysBeforeEarnings": d.get("exit_days_before_earnings"),
        "regimeMaDays": d.get("regime_ma_days"),
        "regimeConfirmDays": d.get("regime_confirm_days"),
        "regimeAction": d.get("regime_action"),
        "strategy": d.get("strategy") or "BREAKOUT",
        "posMomentum": d.get("pos_momentum"),
        "posRebalanceDays": d.get("pos_rebalance_days"),
        "posTopN": d.get("pos_top_n"),
        "posBufferN": d.get("pos_buffer_n"),
        "posAtrMaxPct": _f(d.get("pos_atr_max_pct")),
        "posRegimeMaDays": d.get("pos_regime_ma_days"),
        "posMinTurnoverCr": float(d["pos_min_turnover_cr"]) if d.get("pos_min_turnover_cr") is not None else None,
        "posSlMode": d.get("pos_sl_mode") or "none",
        "posSlPct": float(d["pos_sl_pct"]) if d.get("pos_sl_pct") is not None else 0.0,
        "pfVolMode": d.get("pf_vol_mode") or "none",
        "pfVolFloor": _f(d.get("pf_vol_floor")),
        "pfMaxPerStockPct": _f(d.get("pf_max_per_stock_pct")),
        "pfMaxPerSectorPct": _f(d.get("pf_max_per_sector_pct")),
        "pfMaxStocksPerSector": d.get("pf_max_stocks_per_sector"),
        "pfRequireSector": d.get("pf_require_sector"),
        "pfDdThrottleAt": _f(d.get("pf_dd_throttle_at")),
        "signalCadence": d.get("signal_cadence") or "daily",
        "signalScanDay": d.get("signal_scan_day") or "last",
        "entryV2BuyPoints": bool(d.get("entry_v2_buy_points")),
        "baseStageLadder": d.get("base_stage_ladder") or "prod",
        "weeklyRiskPct": _f(d.get("weekly_risk_pct")),
        "requireWeeklyBoxBreakout": bool(d.get("require_weekly_box_breakout")),
        "weeklyBoxLookbackDays": d.get("weekly_box_lookback_days"),
        "squeezeVolumeMultiplier": _f(d.get("squeeze_volume_multiplier")),
        "rsiEntryThreshold": _f(d.get("rsi_entry_threshold")),
        "rsiStopPct": _f(d.get("rsi_stop_pct")),
        "rsiTargetPct": _f(d.get("rsi_target_pct")),
        "weeklyDailyExitCheck": bool(d.get("weekly_daily_exit_check")),
        "weeklyCompoundingSizing": bool(d.get("weekly_compounding_sizing")),
        "weeklyEntryCadence": d.get("weekly_entry_cadence") or "weekly",
        "weeklyRotationEnabled": bool(d.get("weekly_rotation_enabled")),
        "weeklyBreakevenEnabled": bool(d.get("weekly_breakeven_enabled")),
        "weeklyHalfBookingEnabled": bool(d.get("weekly_half_booking_enabled")),
        "weeklyRankMode": d.get("weekly_rank_mode") or "box_weeks",
        "weeklyEquityThrottleMode": d.get("weekly_equity_throttle_mode") or "none",
        "weeklyEquityThrottleDdPct": _f(d.get("weekly_equity_throttle_dd_pct")),
        "weeklyEquityThrottleCut": _f(d.get("weekly_equity_throttle_cut")),
        "weeklyEquityMaWeeks": d.get("weekly_equity_ma_weeks"),
        "itfProxy": d.get("itf_proxy"),
        "itfMaDays": d.get("itf_ma_days"),
        "itfCapitalPct": _f(d.get("itf_capital_pct")),
        "exitSlippagePct": _f(d.get("exit_slippage_pct")),
        "advPositionCapPct": _f(d.get("adv_position_cap_pct")),
        "compoundingMaxCapital": _f(d.get("compounding_max_capital")),
        # Path metrics — NULL on non-PORTFOLIO runs, which the UI reads as
        # "no path metrics" rather than as zeros.
        "pfCagrPct": _f(d.get("pf_cagr_pct")),
        "pfMaxDDPct": _f(d.get("pf_max_dd_pct")),
        "pfUlcer": _f(d.get("pf_ulcer")),
        "pfWorst12mPct": _f(d.get("pf_worst_12m_pct")),
        "pfMartin": _f(d.get("pf_martin")),
        "pfTurnoverPerYr": _f(d.get("pf_turnover_per_yr")),
        "pfAvgExposure": _f(d.get("pf_avg_exposure")),
        "pfFinalEquity": _f(d.get("pf_final_equity")),
        "pfCalendar": _j(d.get("pf_calendar")),
        "pfEquityCurve": _j(d.get("pf_equity_curve")),
        # Generic path metrics for all strategies (non-PORTFOLIO uses these)
        "cagrPct": _f(d.get("cagr_pct")),
        "maxDDPct": _f(d.get("max_dd_pct")),
        # MtM path stats written by backtest/path_stats.py at run completion
        # for WEEKLY_BREAKOUT / INDEX_TF (PORTFOLIO keeps its own pf_* set).
        "worst12mPct": _f(d.get("pf_worst_12m_pct")),
        "martin": _f(d.get("pf_martin")),
        "maxUwDays": d.get("max_uw_days"),
        "startedAt": d["created_at"].isoformat() if d.get("created_at") else None,
        "execSeconds": d.get("exec_seconds"),
    }


async def _latest_close_map(pool, symbols: list[str], upto: date) -> dict[str, float]:
    """Latest close on or before `upto` per symbol — used to mark OPEN
    positions to market for unrealized P&L (in trade log / summary, "now"
    is the run's end_date since the simulation doesn't extend past it)."""
    if not symbols:
        return {}
    rows = await pool.fetch(
        """
        SELECT DISTINCT ON (symbol) symbol, close
        FROM ohlcv_data
        WHERE symbol = ANY($1) AND time < ($2::date + INTERVAL '1 day')
        ORDER BY symbol, time DESC
        """,
        symbols, upto,
    )
    return {r["symbol"]: float(r["close"]) for r in rows}


def _unrealized(t: dict, current_price: float | None) -> float | None:
    if t["status"] != "OPEN" or current_price is None or not t.get("entry_fill_price"):
        return None
    return round((current_price - float(t["entry_fill_price"])) * t["quantity"], 2)


def _deployed(t: dict) -> float:
    if t.get("entry_fill_price") is None or t.get("quantity") is None:
        return 0.0
    return float(t["entry_fill_price"]) * t["quantity"]


def _track_stats(closed: list[dict], open_trades: list[dict], price_map: dict, rank_key: str, capital: float) -> dict:
    ts = [t for t in closed if t.get(rank_key) is not None]
    open_ts = [t for t in open_trades if t.get(rank_key) is not None]
    unrealized = sum(
        (u for u in (_unrealized(t, price_map.get(t["symbol"])) for t in open_ts) if u is not None)
    )
    deployed = round(sum(_deployed(t) for t in open_ts), 2)
    unrealized_pct = round(unrealized / deployed * 100, 2) if deployed else 0.0
    n = len(ts)
    if n == 0:
        return {
            "count": 0, "winRate": 0.0, "totalPnl": 0.0, "totalPnlPct": 0.0,
            "totalGrossPnl": 0.0, "costDrag": 0.0,
            "avgR": None, "maxDrawdown": 0.0,
            "unrealizedPnl": round(unrealized, 2), "unrealizedPnlPct": unrealized_pct,
            "deployed": deployed, "openPositionCount": len(open_ts),
        }
    wins = len([t for t in ts if (t["realized_pnl"] or 0) > 0])
    r_vals = [float(t["r_multiple"]) for t in ts if t.get("r_multiple") is not None]
    by_day: dict = {}
    for t in ts:
        if t.get("exit_date"):
            by_day[t["exit_date"]] = by_day.get(t["exit_date"], 0.0) + float(t["realized_pnl"] or 0)
    cum, peak, max_dd = 0.0, 0.0, 0.0
    for d in sorted(by_day):
        cum += by_day[d]
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    total_pnl = round(sum(float(t["realized_pnl"] or 0) for t in ts), 2)
    total_gross_pnl = round(sum(float(t["gross_pnl"] or 0) for t in ts if t.get("gross_pnl") is not None), 2)
    return {
        "count": n, "winRate": round(wins / n * 100, 1),
        "totalPnl": total_pnl,
        "totalPnlPct": round(total_pnl / capital * 100, 2) if capital else 0.0,
        "totalGrossPnl": total_gross_pnl,
        "costDrag": round(total_gross_pnl - total_pnl, 2),  # slippage + brokerage, in rupees
        "avgR": round(sum(r_vals) / len(r_vals), 2) if r_vals else None,
        "maxDrawdown": round(max_dd, 2),
        "unrealizedPnl": round(unrealized, 2),
        "unrealizedPnlPct": unrealized_pct,
        "deployed": deployed,
        "openPositionCount": len(open_ts),
    }


async def _compute_run_path_stats(pool, run_id: int, start_date: date, end_date: date, capital: float) -> dict | None:
    """Compute CAGR and max drawdown for non-PORTFOLIO runs by building their
    equity curve from trades. Returns {"cagrPct": float, "maxDrawdownPct": float}
    or None if no trades exist."""
    trades = [dict(r) for r in await pool.fetch(
        "SELECT * FROM backtest_trades WHERE run_id = $1", run_id
    )]
    if not trades:
        return None

    closed = [t for t in trades if t["status"] == "CLOSED" and t.get("exit_date")]
    open_trades = [t for t in trades if t["status"] == "OPEN"]

    # Build equity curve from realized P&L by exit date
    realized_by_day: dict = {}
    for t in closed:
        d = t["exit_date"]
        realized_by_day[d] = realized_by_day.get(d, 0.0) + float(t["realized_pnl"] or 0)

    # Build unrealized P&L by day for open positions (query once for all symbols)
    unrealized_by_day: dict = {}
    if open_trades:
        open_symbols = list({t["symbol"] for t in open_trades})
        price_map = await _latest_close_map(pool, open_symbols, end_date)

        # For each day a position was open, mark-to-market it to the latest close on or before that day
        for t in open_trades:
            if t.get("entry_fill_date"):
                # Use latest close as of end_date
                curr_price = price_map.get(t["symbol"])
                if curr_price:
                    unrealized = (curr_price - float(t["entry_fill_price"])) * t["quantity"] if t.get("entry_fill_price") else 0
                    for d in (realized_by_day.keys() | set(unrealized_by_day.keys()) | {end_date}):
                        if d >= t["entry_fill_date"] and d <= end_date:
                            unrealized_by_day.setdefault(d, 0.0)
                            unrealized_by_day[d] += unrealized

    # Build equity curve: capital + cumulative realized + unrealized for that day
    all_days = sorted(set(realized_by_day) | set(unrealized_by_day))
    equities = []
    cum_realized = 0.0
    for d in all_days:
        if d in realized_by_day:
            cum_realized += realized_by_day[d]
        unrealized = unrealized_by_day.get(d, 0.0)
        eq = capital + cum_realized + unrealized
        equities.append(eq)

    if not equities:
        return None

    return _equity_path_stats(equities, capital, start_date, end_date)


def _equity_path_stats(equities: list[float], capital: float, start_date: date, end_date: date) -> dict:
    """Max drawdown % and CAGR % off a (possibly sparse) mark-to-market equity
    series — capital + cumulative realized P&L + that day's unrealized P&L,
    one point per day that had a fill/exit/open position (see equity_curve in
    get_summary). Sparse is fine for drawdown: the series only needs to be
    correct at the marks it has, and a strategy with long flat stretches (no
    position open) can't set a new peak or trough there anyway.

    Applies to every non-PORTFOLIO strategy (BREAKOUT, POSITIONAL, and
    WEEKLY_BREAKOUT) — previously only PORTFOLIO runs got CAGR/maxDD because
    those are computed by the engine's own continuous-equity simulation; this
    reconstructs the equivalent off the generic realized+unrealized series so
    every strategy can be judged on the same path metrics, not just total P&L.
    CAGR anchors on the run's actual start/end dates, not the sparse marks,
    since a fixed-capital (non-compounding) strategy sitting flat at capital
    for stretches is real information, not a gap to skip over.
    """
    peak = capital
    max_dd_pct = 0.0
    for eq in equities:
        peak = max(peak, eq)
        if peak > 0:
            max_dd_pct = max(max_dd_pct, (peak - eq) / peak * 100)
    final_equity = equities[-1] if equities else capital
    days = (end_date - start_date).days
    cagr_pct = None
    if days > 0 and capital > 0 and final_equity > 0:
        cagr_pct = (((final_equity / capital) ** (365.25 / days)) - 1) * 100
    return {
        "maxDrawdownPct": round(max_dd_pct, 2),
        "cagrPct": round(cagr_pct, 2) if cagr_pct is not None else None,
    }


async def _daily_unrealized(pool, run_id: int, end_date: date,
                            all_quant: bool = False) -> dict:
    """Per-day mark-to-market snapshot (not cumulative — a level, not a
    flow) of unrealized P&L for every day a position was actually open,
    split by track. For each trade, joins its symbol's close price across
    every day from entry_fill_date up to (but excluding) exit_date — or
    through the run's end_date if it's still open — so a position open for
    N days contributes N daily mark-to-market rows."""
    # all_quant is retained for callers that need a single undivided book. It is
    # currently unused: PORTFOLIO runs, the only such case, now skip this
    # function entirely and render the engine's stored equity curve instead.
    quant_when = "TRUE" if all_quant else "t.quant_rank IS NOT NULL"
    # PERFORMANCE. This query took ~4-6s and dominated the summary endpoint.
    # Three separate causes, all fixed here:
    #
    # 1. `o.time::date >= ...` wrapped the partition key in a function, so
    #    Postgres could not prune and scanned all 203 ohlcv_data partitions.
    #    Comparing o.time directly against the date bounds lets pruning work.
    #    `< exit_date` on a timestamp column is equivalent to `< exit_date` on
    #    the cast, since a bar at exactly midnight of the exit date is excluded
    #    either way and intraday timestamps within the exit date must also be.
    # 2. With 203 partitions the planner emitted 2,861 JIT functions costing
    #    ~2.1s of a 3.7s query — far more than JIT saved. Disabled per
    #    transaction, not globally, so other workloads keep it.
    # 3. Planning alone was ~450ms; pruning cuts that too.
    async with pool.acquire() as con:
        async with con.transaction():
            await con.execute("SET LOCAL jit = off")
            rows = await con.fetch(
                f"""
                SELECT o.time::date AS d,
                  SUM(CASE WHEN {quant_when}
                            THEN (o.close - t.entry_fill_price) * t.quantity ELSE 0 END) AS quant_unrealized,
                  SUM(CASE WHEN t.ai_rank IS NOT NULL
                            THEN (o.close - t.entry_fill_price) * t.quantity ELSE 0 END) AS ai_unrealized
                FROM backtest_trades t
                JOIN ohlcv_data o
                  ON o.symbol = t.symbol
                 AND o.time >= t.entry_fill_date
                 AND o.time < COALESCE(t.exit_date, $2::date + 1)
                WHERE t.run_id = $1 AND t.entry_fill_date IS NOT NULL
                GROUP BY o.time::date
                """,
                run_id, end_date,
            )
    return {r["d"]: {"quant": float(r["quant_unrealized"] or 0), "ai": float(r["ai_unrealized"] or 0)} for r in rows}


@router.get("/backtest/blend")
async def get_blend(request: Request, run_a: int, run_b: int,
                    weight_a: float = 0.3, rebalance: str = "monthly"):
    """Two-book blended portfolio metrics, computed from the two runs' actual
    engine trade logs (mark-to-market weekly curves via path_stats.build_
    mtm_curve — the same definition the run table shows).

    This exists because no single engine run can hold WB-Composite and an
    INDEX_TF ETF book simultaneously, yet the deployable configuration IS
    that pair. Rather than approximating the blend offline (or pretending a
    single run measured it), this computes it server-side from engine truth:
    each leg's own curve, aligned on the common window, capital split
    weight_a/(1-weight_a), optionally re-set to target weights on the first
    grid date of each month (the validated +5pp rebalancing-premium policy).
    Research basis: 2026-08-17 pre-deployment study — fixed monthly
    rebalancing beat drift AND every dynamic-switching variant tested.
    """
    if not (0.0 <= weight_a <= 1.0):
        raise HTTPException(status_code=400, detail="weight_a must be in [0,1]")
    if rebalance not in ("monthly", "none"):
        raise HTTPException(status_code=400, detail="rebalance must be monthly|none")
    from backtest.path_stats import build_mtm_curve

    pool = _pool(request)
    wa, ea, run_a_row = await build_mtm_curve(pool, run_a)
    wb, eb, run_b_row = await build_mtm_curve(pool, run_b)
    if wa is None or wb is None:
        raise HTTPException(status_code=404, detail="curve unavailable for one of the runs")

    start, end = max(wa[0], wb[0]), min(wa[-1], wb[-1])
    if (end - start).days < 365:
        raise HTTPException(status_code=400, detail="runs overlap under 1 year — blend not meaningful")

    # union grid over the common window; each leg forward-filled onto it
    grid = sorted({d for d in wa if start <= d <= end} | {d for d in wb if start <= d <= end})

    def ffill(weeks, eq):
        out, j = [], 0
        for d in grid:
            while j + 1 < len(weeks) and weeks[j + 1] <= d:
                j += 1
            out.append(eq[j])
        return out

    A, B = ffill(wa, ea), ffill(wb, eb)
    A = [v / A[0] for v in A]
    B = [v / B[0] for v in B]

    h_a, h_b = weight_a, 1.0 - weight_a
    blend = [1.0]
    last_month = grid[0].month
    for i in range(1, len(grid)):
        h_a *= A[i] / A[i - 1]
        h_b *= B[i] / B[i - 1]
        tot = h_a + h_b
        if rebalance == "monthly" and grid[i].month != last_month:
            h_a, h_b = tot * weight_a, tot * (1.0 - weight_a)
            last_month = grid[i].month
        blend.append(tot)

    def stats(vals):
        yrs = (grid[-1] - grid[0]).days / 365.25
        cagr = (vals[-1] ** (1 / yrs) - 1) * 100
        peak, max_dd = vals[0], 0.0
        uw_start, uw_days = None, 0
        for d, v in zip(grid, vals):
            peak = max(peak, v)
            dd = (peak - v) / peak * 100
            max_dd = max(max_dd, dd)
            if dd > 0.01:
                uw_start = uw_start or d
                uw_days = max(uw_days, (d - uw_start).days)
            else:
                uw_start = None
        return {"cagrPct": round(cagr, 2), "maxDDPct": round(max_dd, 2),
                "maxUwDays": uw_days,
                "calmar": round(cagr / max_dd, 2) if max_dd > 0 else None}

    return {
        "runA": run_a, "runB": run_b, "weightA": weight_a, "rebalance": rebalance,
        "window": {"start": str(grid[0]), "end": str(grid[-1]),
                   "years": round((grid[-1] - grid[0]).days / 365.25, 1)},
        "legA": stats(A), "legB": stats(B), "blend": stats(blend),
        "note": "MtM weekly curves from engine trade logs; survivorship haircut NOT included",
    }


@router.get("/backtest/runs/{run_id}/summary")
async def get_summary(run_id: int, request: Request):
    ck = ("summary", run_id)
    if ck in _DONE_CACHE:
        return _DONE_CACHE[ck]
    pool = _pool(request)
    run = await pool.fetchrow(
        "SELECT end_date, start_date, capital, strategy, status, pf_equity_curve, "
        "       pf_cagr_pct, pf_max_dd_pct, pf_ulcer, pf_worst_12m_pct, "
        "       pf_martin, pf_turnover_per_yr, pf_final_equity, pf_calendar "
        "FROM backtest_runs WHERE id = $1", run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    capital = float(run["capital"])
    is_pf = run["strategy"] == "PORTFOLIO"
    trades = [dict(r) for r in await pool.fetch(
        "SELECT * FROM backtest_trades WHERE run_id = $1", run_id
    )]
    if is_pf:
        # A PORTFOLIO run is one undivided book — there is no quant/AI split to
        # make. Every downstream stat filters on quant_rank, so the trades are
        # normalised onto the quant track here rather than duplicating each of
        # those filters. Rank 1 is a placeholder for grouping only; the trade
        # log's own Rank column is not meaningful for this strategy.
        for t in trades:
            if t.get("quant_rank") is None:
                t["quant_rank"] = 1
    closed = [t for t in trades if t["status"] == "CLOSED" and t.get("exit_date")]
    open_trades = [t for t in trades if t["status"] == "OPEN"]
    # While a run is still RUNNING, its OPEN positions must be marked only up
    # to the simulation's CURRENT date — marking them through end_date walks
    # their prices years past the sim clock with none of the strategy's exits
    # applied, which manufactured absurd mid-run drawdowns (observed: -61.7%
    # at day 3251 of #1047 vs -25.6% final).
    mark_end = run["end_date"]
    if run["status"] == "RUNNING" and trades:
        dates = [t.get("exit_date") for t in trades if t.get("exit_date")] + \
                [t.get("entry_fill_date") for t in trades if t.get("entry_fill_date")]
        if dates:
            mark_end = min(run["end_date"], max(dates))
    price_map = await _latest_close_map(pool, list({t["symbol"] for t in open_trades}), mark_end)

    realized_by_day: dict = {}
    for t in closed:
        d = t["exit_date"]
        row = realized_by_day.setdefault(d, {"quant": 0.0, "ai": 0.0})
        if t.get("quant_rank") is not None:
            row["quant"] += float(t["realized_pnl"] or 0)
        if t.get("ai_rank") is not None:
            row["ai"] += float(t["realized_pnl"] or 0)

    # A PORTFOLIO run renders `portfolioEquity` (the engine's stored daily
    # equity), so the reconstructed series below is computed and then discarded.
    # Skipping it removes the single most expensive query in this endpoint for
    # exactly the runs being clicked on most.
    unrealized_by_day = ({} if is_pf else
                         await _daily_unrealized(pool, run_id, mark_end))

    all_days = sorted(set(realized_by_day) | set(unrealized_by_day))
    # Fill the calendar between first and last activity with every weekday, so
    # all-cash stretches (regime-off, pre-first-buy) appear as FLAT segments
    # instead of silently vanishing — the x-axis is index-scaled, so a missing
    # month used to compress time and look like a "gap"/jump in the curve.
    # A day with no unrealized rows genuinely had no open positions, so
    # unrealized=0 for those fills is exact, not an approximation.
    if all_days:
        # Fill with actual TRADING days only (from the indicator calendar) —
        # a naive weekday fill inserted exchange holidays with unrealized=0,
        # which faked one-day equity craters (observed: -24.5% on 2023-09-19,
        # Ganesh Chaturthi). A trading day genuinely absent from the marks
        # means the book was all-cash, where 0 unrealized is exact.
        tdays = [r["d"] for r in await pool.fetch(
            "SELECT DISTINCT indicator_date AS d FROM stock_indicators "
            "WHERE indicator_date BETWEEN $1 AND $2", all_days[0], all_days[-1])]
        all_days = sorted(set(all_days) | set(tdays))
    equity_curve = []
    cq = ca = 0.0
    for d in all_days:
        r = realized_by_day.get(d)
        if r:
            cq += r["quant"]
            ca += r["ai"]
        u = unrealized_by_day.get(d, {"quant": 0.0, "ai": 0.0})
        equity_curve.append({
            "date": str(d),
            "quantRealizedCumPnl": round(cq, 2), "aiRealizedCumPnl": round(ca, 2),
            "quantUnrealizedPnl": round(u["quant"], 2), "aiUnrealizedPnl": round(u["ai"], 2),
        })

    # Overall deployed capital — from the raw open-trade rows (each counted
    # once), unlike the per-track figures below which double-count a symbol
    # picked by both tracks the same day (by design, same as their P&L).
    total_deployed = round(sum(_deployed(t) for t in open_trades), 2)

    # The engine's own daily equity (cash + marked holdings), stored at run time.
    # For a compounding book this is the chart that matters: the generic
    # equityCurve above plots cumulative realized P&L and unrealized as separate
    # series against zero, which describes flows, not the level of the account.
    pf_curve = run["pf_equity_curve"]
    if isinstance(pf_curve, str):
        pf_curve = json.loads(pf_curve)

    # Headline metrics for a continuous run, so the results panel can lead with
    # CAGR and drawdown instead of a quant/AI split that does not apply to it.
    pf_cal = run["pf_calendar"]
    if isinstance(pf_cal, str):
        pf_cal = json.loads(pf_cal)
    portfolio = None
    if is_pf and run["pf_final_equity"] is not None:
        portfolio = {
            "cagrPct": float(run["pf_cagr_pct"] or 0),
            "maxDDPct": float(run["pf_max_dd_pct"] or 0),
            "ulcer": float(run["pf_ulcer"] or 0),
            "worst12mPct": float(run["pf_worst_12m_pct"] or 0),
            "martin": float(run["pf_martin"] or 0),
            "turnoverPerYr": float(run["pf_turnover_per_yr"] or 0),
            "finalEquity": float(run["pf_final_equity"]),
            "totalPnl": float(run["pf_final_equity"]) - capital,
            "calendar": pf_cal or {},
            # A window under ~2 years restarts at the initial capital, so its
            # CAGR annualises one short period and is not comparable.
            "shortWindow": (run["end_date"] - run["start_date"]).days < 730,
        }

    quant_stats = _track_stats(closed, open_trades, price_map, "quant_rank", capital)
    ai_stats = _track_stats(closed, open_trades, price_map, "ai_rank", capital)
    # DD%/CAGR off the same equity_curve used for the chart — skipped for
    # PORTFOLIO, which already gets these (and the engine's true compounding
    # equity, not this fixed-capital reconstruction) via `portfolio` above.
    if not is_pf:
        quant_equity = [capital + e["quantRealizedCumPnl"] + e["quantUnrealizedPnl"] for e in equity_curve]
        ai_equity = [capital + e["aiRealizedCumPnl"] + e["aiUnrealizedPnl"] for e in equity_curve]
        quant_stats.update(_equity_path_stats(quant_equity, capital, run["start_date"], run["end_date"]))
        ai_stats.update(_equity_path_stats(ai_equity, capital, run["start_date"], run["end_date"]))

    result = {
        "runId": run_id,
        "capital": capital,
        "equityCurve": equity_curve,
        "portfolio": portfolio,
        "portfolioEquity": ([{"date": d, "equity": v} for d, v in pf_curve]
                            if pf_curve else None),
        "quant": quant_stats,
        "ai": ai_stats,
        "openCount": len(open_trades),
        "pendingCount": len([t for t in trades if t["status"] == "PENDING"]),
        "totalDeployed": total_deployed,
    }
    if run["status"] in ("COMPLETED", "FAILED"):
        _done_cache_put(ck, result)
    return result


# A COMPLETED/FAILED run's trades and summary never change again, so both
# endpoints cache finished-run responses in-process (bounded LRU-ish).
_DONE_CACHE: dict = {}
_DONE_CACHE_MAX = 48


def _done_cache_put(key, value):
    if len(_DONE_CACHE) >= _DONE_CACHE_MAX:
        _DONE_CACHE.pop(next(iter(_DONE_CACHE)))
    _DONE_CACHE[key] = value


@router.get("/backtest/runs/{run_id}/trades")
async def get_trades(run_id: int, request: Request, track: str | None = None, status: str | None = None):
    ck = ("trades", run_id, track, status)
    if ck in _DONE_CACHE:
        return _DONE_CACHE[ck]
    pool = _pool(request)
    run = await pool.fetchrow("SELECT end_date, status FROM backtest_runs WHERE id = $1", run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    q = "SELECT * FROM backtest_trades WHERE run_id = $1"
    params = [run_id]
    if status:
        params.append(status)
        q += f" AND status = ${len(params)}"
    q += " ORDER BY signal_date DESC, id DESC"
    rows = [dict(r) for r in await pool.fetch(q, *params)]
    if track == "quant":
        rows = [r for r in rows if r.get("quant_rank") is not None]
    elif track == "ai":
        rows = [r for r in rows if r.get("ai_rank") is not None]
    open_symbols = list({r["symbol"] for r in rows if r["status"] == "OPEN"})
    price_map = await _latest_close_map(pool, open_symbols, run["end_date"])
    out = [_trade_to_json(r, price_map.get(r["symbol"])) for r in rows]
    if run["status"] in ("COMPLETED", "FAILED"):
        _done_cache_put(ck, out)
    return out


def _trade_to_json(t: dict, current_price: float | None = None) -> dict:
    return {
        "id": t["id"], "symbol": t["symbol"], "quantRank": t["quant_rank"], "aiRank": t["ai_rank"],
        "signalDate": str(t["signal_date"]), "entryTriggerPrice": float(t["entry_trigger_price"]),
        "structuralSl": float(t["structural_sl"]), "targetPrice": float(t["target_price"]) if t["target_price"] else None,
        "riskPerShare": float(t["risk_per_share"]) if t.get("risk_per_share") is not None else None,
        "quantity": t["quantity"], "entryType": t["entry_type"], "baseStage": t["base_stage"],
        "aiConfidence": float(t["ai_confidence"]) if t["ai_confidence"] is not None else None,
        "aiRecommendation": t["ai_recommendation"], "status": t["status"],
        "entryFillDate": str(t["entry_fill_date"]) if t["entry_fill_date"] else None,
        "entryFillPrice": float(t["entry_fill_price"]) if t["entry_fill_price"] is not None else None,
        "halfBooked": t["half_booked"],
        "trailSl": float(t["trail_sl"]) if t.get("trail_sl") is not None else None,
        "exitDate": str(t["exit_date"]) if t["exit_date"] else None,
        "exitPrice": float(t["exit_price"]) if t["exit_price"] is not None else None,
        "exitReason": t["exit_reason"],
        "realizedPnl": float(t["realized_pnl"]) if t["realized_pnl"] is not None else None,
        "grossPnl": float(t["gross_pnl"]) if t.get("gross_pnl") is not None else None,
        "unrealizedPnl": _unrealized(t, current_price),
        "rMultiple": float(t["r_multiple"]) if t["r_multiple"] is not None else None,
        "holdingDays": t["holding_days"],
        "allocation": _allocation(t),
    }


def _allocation(t: dict) -> float:
    """Capital committed to this stock — actual fill price once filled,
    else the theoretical trigger price (for still-PENDING rows)."""
    price = t.get("entry_fill_price") if t.get("entry_fill_price") is not None else t.get("entry_trigger_price")
    if price is None or t.get("quantity") is None:
        return 0.0
    return round(float(price) * t["quantity"], 2)


@router.get("/backtest/runs/{run_id}/trades/{trade_id}/chart")
async def get_trade_chart(run_id: int, trade_id: int, request: Request):
    """Annotated PNG for one trade — entry, -8% floor, structural/trail SL,
    1R/2R/3R, target, entry/exit day markers. See backtest/chart.py."""
    from fastapi import Response

    from backtest.chart import load_trade_window, load_weekly_trail_sl_series, render_trade_chart

    pool = _pool(request)
    run = await pool.fetchrow("SELECT end_date FROM backtest_runs WHERE id = $1", run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    row = await pool.fetchrow(
        "SELECT * FROM backtest_trades WHERE id = $1 AND run_id = $2", trade_id, run_id
    )
    if not row:
        raise HTTPException(status_code=404, detail="Trade not found")
    t = dict(row)
    trade = _trade_to_json(t)

    anchor_start = t["entry_fill_date"] or t["signal_date"]
    anchor_end = t["exit_date"] or run["end_date"]

    df = await load_trade_window(pool, t["symbol"], anchor_start, anchor_end)
    if df is None or df.empty:
        raise HTTPException(status_code=404, detail="No OHLCV data for this symbol/window")

    # WEEKLY_BOX_BREAKOUT trades trail via a weekly MACD-crossover ratchet
    # (weekly_simulator.py), not a per-day EMA/R-ladder step — so their trail
    # SL is worth plotting as the evolving line it actually was, not the
    # single final hline every other strategy gets. See chart.py docstring.
    trail_sl_series = None
    if t.get("entry_type") == "WEEKLY_BOX_BREAKOUT" and t.get("entry_fill_date"):
        trail_sl_series = await load_weekly_trail_sl_series(
            pool, t["symbol"], t["entry_fill_date"], t["structural_sl"], anchor_end,
        )

    png = render_trade_chart(df, trade, t["symbol"], trail_sl_series=trail_sl_series)
    return Response(content=png, media_type="image/png")


@router.get("/backtest/runs/{run_id}/day/{d}")
async def get_day(run_id: int, d: date, request: Request):
    pool = _pool(request)
    picks = [dict(r) for r in await pool.fetch(
        "SELECT * FROM backtest_trades WHERE run_id = $1 AND signal_date = $2", run_id, d
    )]
    filled_today = [dict(r) for r in await pool.fetch(
        "SELECT * FROM backtest_trades WHERE run_id = $1 AND entry_fill_date = $2", run_id, d
    )]
    closed_today = [dict(r) for r in await pool.fetch(
        "SELECT * FROM backtest_trades WHERE run_id = $1 AND exit_date = $2", run_id, d
    )]
    open_rows = [dict(r) for r in await pool.fetch(
        """
        SELECT * FROM backtest_trades
        WHERE run_id = $1 AND status IN ('OPEN', 'PENDING')
          AND signal_date <= $2
          AND (entry_fill_date IS NULL OR entry_fill_date <= $2)
        """,
        run_id, d,
    )]
    open_symbols = list({r["symbol"] for r in open_rows if r["status"] == "OPEN"})
    closes_today = await _latest_close_map(pool, open_symbols, d)
    open_positions = [_trade_to_json(r, closes_today.get(r["symbol"])) for r in open_rows]

    realized_row = await pool.fetchrow(
        "SELECT COALESCE(SUM(realized_pnl), 0) AS s FROM backtest_trades "
        "WHERE run_id = $1 AND status = 'CLOSED' AND exit_date <= $2",
        run_id, d,
    )

    return {
        "date": str(d),
        "picks": [_trade_to_json(r) for r in picks],
        "ordersFilled": [_trade_to_json(r) for r in filled_today],
        "closedToday": [_trade_to_json(r) for r in closed_today],
        "openPositions": open_positions,
        "realizedPnlToDate": round(float(realized_row["s"]), 2),
    }
