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
    # Structural/technical hard exits.
    failed_breakout_exit: bool = False
    swing_break_exit: bool = False
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
    strategy: str = Field("BREAKOUT", pattern="^(BREAKOUT|POSITIONAL|PORTFOLIO|WEEKLY_BREAKOUT)$")
    # Positional-only knobs (ignored for BREAKOUT runs).
    pos_momentum: str = Field("pct_chg_6m", pattern="^pct_chg_(3m|6m|1y)$")
    pos_rebalance_days: int = Field(21, ge=1, le=250)
    pos_top_n: int = Field(10, ge=1, le=50)
    pos_buffer_n: int = Field(20, ge=1, le=100)
    pos_min_turnover_cr: float = 5.0
    # sql/021 — daily-checked stop for the positional book. Default 'none'
    # reproduces the original rebalance-only exits, so old runs stay comparable.
    pos_sl_mode: str = Field("none", pattern="^(none|fixed|trail|sma200|sma50|ema50|ema21)$")
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
           weekly_risk_pct)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'RUNNING',$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,
                $21,$22,$23,$24,$25,$26,$27,$28,
                $29,$30,$31,$32,$33,$34,$35,$36,$37,$38,$39,$40,$41,$42,$43,$44,$45,$46,$47,$48,$49,$50,$51,$52,$53,$54,$55,$56,
                $57,$58,$59,$60,$61,$62,$63,$64,$65,$66,$67,$68,$69,$70)
        RETURNING id
        """,
        body.start_date, body.end_date, body.track_mode, body.capital,
        body.resting_window_days, body.stacking_guard, body.stacking_guard_mode,
        json.dumps(body.exit_config.model_dump()),
        json.dumps({"notes": body.notes}),
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
        body.weekly_risk_pct,
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


@router.get("/backtest/runs")
async def list_runs(request: Request):
    pool = _pool(request)
    # Realized + unrealized P&L are aggregated here so the run list can show
    # them directly — previously you had to open each run's summary to see how
    # it did. Unrealized marks still-OPEN positions to the last close on or
    # before the run's end_date (same convention as get_summary()), via a
    # LATERAL so it stays one pass rather than a per-row subquery.
    rows = await pool.fetch(
        """
        SELECT r.*,
          (SELECT count(*) FROM backtest_trades t WHERE t.run_id = r.id) AS trade_count,
          (SELECT COALESCE(SUM(t.realized_pnl), 0) FROM backtest_trades t
            WHERE t.run_id = r.id AND t.status = 'CLOSED') AS realized_pnl,
          (SELECT COALESCE(SUM((lc.close - t.entry_fill_price) * t.quantity), 0)
             FROM backtest_trades t
             CROSS JOIN LATERAL (
               SELECT o.close FROM ohlcv_data o
               WHERE o.symbol = t.symbol AND o.time::date <= r.end_date
               ORDER BY o.time DESC LIMIT 1
             ) lc
            WHERE t.run_id = r.id AND t.status = 'OPEN'
              AND t.entry_fill_price IS NOT NULL) AS unrealized_pnl
        FROM backtest_runs r ORDER BY r.created_at DESC LIMIT 250
        """
    )
    return [_run_to_json(r) for r in rows]


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
        WHERE symbol = ANY($1) AND time::date <= $2
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


@router.get("/backtest/runs/{run_id}/summary")
async def get_summary(run_id: int, request: Request):
    pool = _pool(request)
    run = await pool.fetchrow(
        "SELECT end_date, start_date, capital, strategy, pf_equity_curve, "
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
    price_map = await _latest_close_map(pool, list({t["symbol"] for t in open_trades}), run["end_date"])

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
                         await _daily_unrealized(pool, run_id, run["end_date"]))

    all_days = sorted(set(realized_by_day) | set(unrealized_by_day))
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

    return {
        "runId": run_id,
        "capital": capital,
        "equityCurve": equity_curve,
        "portfolio": portfolio,
        "portfolioEquity": ([{"date": d, "equity": v} for d, v in pf_curve]
                            if pf_curve else None),
        "quant": _track_stats(closed, open_trades, price_map, "quant_rank", capital),
        "ai": _track_stats(closed, open_trades, price_map, "ai_rank", capital),
        "openCount": len(open_trades),
        "pendingCount": len([t for t in trades if t["status"] == "PENDING"]),
        "totalDeployed": total_deployed,
    }


@router.get("/backtest/runs/{run_id}/trades")
async def get_trades(run_id: int, request: Request, track: str | None = None, status: str | None = None):
    pool = _pool(request)
    run = await pool.fetchrow("SELECT end_date FROM backtest_runs WHERE id = $1", run_id)
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
    return [_trade_to_json(r, price_map.get(r["symbol"])) for r in rows]


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

    from backtest.chart import load_trade_window, render_trade_chart

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

    png = render_trade_chart(df, trade, t["symbol"])
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
