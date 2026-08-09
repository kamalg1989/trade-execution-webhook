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


class RunCreate(BaseModel):
    start_date: date
    end_date: date
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
    brokerage_per_order: float = 20.0
    chandelier_atr_mult: float = 3.0


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
           safety_sl_pct, slippage_pct, brokerage_per_order, chandelier_atr_mult)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'RUNNING',$9,$10,$11,$12,$13)
        RETURNING id
        """,
        body.start_date, body.end_date, body.track_mode, body.capital,
        body.resting_window_days, body.stacking_guard, body.stacking_guard_mode,
        json.dumps(body.exit_config.model_dump()),
        json.dumps({"notes": body.notes}),
        body.safety_sl_pct, body.slippage_pct, body.brokerage_per_order, body.chandelier_atr_mult,
    )
    run_id = row["id"]

    # VPS is 961MB RAM total. Chart rendering (matplotlib) is what actually
    # OOM-killed the process in testing, not the Gemini network call itself —
    # so MAX_CONCURRENT_RENDER stays at 1 (serialize renders) while
    # MAX_CONCURRENT_AI can go higher than before to let more symbols overlap
    # while they're just waiting on Gemini. Revert both to "1" if a run OOMs.
    env = {**os.environ, "MAX_CONCURRENT_AI": "4", "MAX_CONCURRENT_RENDER": "1"}

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
    rows = await pool.fetch(
        """
        SELECT r.*,
          (SELECT count(*) FROM backtest_trades t WHERE t.run_id = r.id) AS trade_count
        FROM backtest_runs r ORDER BY r.created_at DESC LIMIT 50
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


def _run_to_json(r) -> dict:
    d = dict(r)
    exit_cfg = d.get("exit_config")
    if isinstance(exit_cfg, str):
        exit_cfg = json.loads(exit_cfg)
    params = d.get("params")
    if isinstance(params, str):
        params = json.loads(params)
    return {
        "id": d["id"], "createdAt": d["created_at"].isoformat() if d.get("created_at") else None,
        "completedAt": d["completed_at"].isoformat() if d.get("completed_at") else None,
        "startDate": str(d["start_date"]), "endDate": str(d["end_date"]),
        "universe": d["universe"], "trackMode": d["track_mode"], "capital": float(d["capital"]),
        "restingWindowDays": d["resting_window_days"], "stackingGuard": d["stacking_guard"],
        "stackingGuardMode": d["stacking_guard_mode"], "exitConfig": exit_cfg,
        "status": d["status"], "progressDay": d["progress_day"], "progressTotalDays": d["progress_total_days"],
        "error": d["error"], "params": params, "tradeCount": d.get("trade_count"),
        "safetySlPct": float(d["safety_sl_pct"]) if d.get("safety_sl_pct") is not None else None,
        "slippagePct": float(d["slippage_pct"]) if d.get("slippage_pct") is not None else None,
        "brokeragePerOrder": float(d["brokerage_per_order"]) if d.get("brokerage_per_order") is not None else None,
        "chandelierAtrMult": float(d["chandelier_atr_mult"]) if d.get("chandelier_atr_mult") is not None else None,
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


async def _daily_unrealized(pool, run_id: int, end_date: date) -> dict:
    """Per-day mark-to-market snapshot (not cumulative — a level, not a
    flow) of unrealized P&L for every day a position was actually open,
    split by track. For each trade, joins its symbol's close price across
    every day from entry_fill_date up to (but excluding) exit_date — or
    through the run's end_date if it's still open — so a position open for
    N days contributes N daily mark-to-market rows."""
    rows = await pool.fetch(
        """
        SELECT o.time::date AS d,
          SUM(CASE WHEN t.quant_rank IS NOT NULL
                    THEN (o.close - t.entry_fill_price) * t.quantity ELSE 0 END) AS quant_unrealized,
          SUM(CASE WHEN t.ai_rank IS NOT NULL
                    THEN (o.close - t.entry_fill_price) * t.quantity ELSE 0 END) AS ai_unrealized
        FROM backtest_trades t
        JOIN ohlcv_data o
          ON o.symbol = t.symbol
         AND o.time::date >= t.entry_fill_date
         AND o.time::date < COALESCE(t.exit_date, $2::date + 1)
        WHERE t.run_id = $1 AND t.entry_fill_date IS NOT NULL
        GROUP BY o.time::date
        """,
        run_id, end_date,
    )
    return {r["d"]: {"quant": float(r["quant_unrealized"] or 0), "ai": float(r["ai_unrealized"] or 0)} for r in rows}


@router.get("/backtest/runs/{run_id}/summary")
async def get_summary(run_id: int, request: Request):
    pool = _pool(request)
    run = await pool.fetchrow("SELECT end_date, capital FROM backtest_runs WHERE id = $1", run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    capital = float(run["capital"])
    trades = [dict(r) for r in await pool.fetch(
        "SELECT * FROM backtest_trades WHERE run_id = $1", run_id
    )]
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

    unrealized_by_day = await _daily_unrealized(pool, run_id, run["end_date"])

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

    return {
        "runId": run_id,
        "capital": capital,
        "equityCurve": equity_curve,
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
