"""Backtest orchestrator — one run, day by day, chronological. See
/BACKTEST_ENGINE_SPEC.md (repo root) for the full design and §4 for the
exact per-day flow this implements.
"""
from __future__ import annotations

import logging
import traceback
from datetime import date

from ai_analysis import config as ai_config
from ai_analysis.pipeline import analyze_symbols
from app.db import PgRepo

from . import funnel
from .ai_repo import BacktestAiRepo
from .simulator import SimTrade, step_exit, try_fill

logger = logging.getLogger(__name__)


async def run_backtest(run_id: int, pool) -> None:
    run = await pool.fetchrow("SELECT * FROM backtest_runs WHERE id = $1", run_id)
    if run is None:
        return

    try:
        await _run(dict(run), pool)
    except Exception as e:
        logger.exception("Backtest run %s failed", run_id)
        await pool.execute(
            "UPDATE backtest_runs SET status='FAILED', error=$2, completed_at=NOW() WHERE id=$1",
            run_id, f"{e}\n{traceback.format_exc()[-2000:]}",
        )


async def _run(run: dict, pool) -> None:
    run_id = run["id"]
    start_date, end_date = run["start_date"], run["end_date"]
    track_mode = run["track_mode"]
    capital = float(run["capital"])
    resting_window_days = run["resting_window_days"]
    stacking_guard = run["stacking_guard"]
    stacking_guard_mode = run["stacking_guard_mode"]
    exit_config = run["exit_config"] if isinstance(run["exit_config"], dict) else {}
    import json as _json
    if isinstance(run["exit_config"], str):
        exit_config = _json.loads(run["exit_config"])

    day_rows = await pool.fetch(
        "SELECT DISTINCT time::date AS d FROM ohlcv_data "
        "WHERE time::date BETWEEN $1 AND $2 ORDER BY d",
        start_date, end_date,
    )
    trading_days: list[date] = [r["d"] for r in day_rows]
    total_days = len(trading_days)
    await pool.execute(
        "UPDATE backtest_runs SET progress_total_days=$1 WHERE id=$2", total_days, run_id
    )

    screener_repo = PgRepo(pool)
    ai_repo = BacktestAiRepo(pool)

    active: list[SimTrade] = []          # PENDING or OPEN trades, not yet persisted-final
    db_id: dict[int, int] = {}           # id(trade) -> backtest_trades.id
    signal_day_index: dict[int, int] = {}  # id(trade) -> trading_days index of signal_date

    for day_idx, day in enumerate(trading_days):
        symbols_today = {t.symbol for t in active}
        bars_by_symbol = {}
        if symbols_today:
            rows = await pool.fetch(
                "SELECT symbol, open, high, low, close FROM ohlcv_data "
                "WHERE symbol = ANY($1) AND time::date = $2",
                list(symbols_today), day,
            )
            bars_by_symbol = {
                r["symbol"]: {"open": float(r["open"]), "high": float(r["high"]),
                              "low": float(r["low"]), "close": float(r["close"])}
                for r in rows
            }

        # 1. fills + exits for everything already active
        still_active = []
        for t in active:
            bar = bars_by_symbol.get(t.symbol)
            if bar is not None:
                if t.status == "PENDING":
                    since = day_idx - signal_day_index[id(t)]
                    try_fill(t, day, bar, resting_window_days, since)
                if t.status == "OPEN":
                    step_exit(t, day, bar, exit_config)
            await _persist(pool, run_id, t, db_id)
            if t.status in ("PENDING", "OPEN"):
                still_active.append(t)
        active = still_active

        # 2. today's candidates (quant funnel, always computed — cheap/local)
        candidates = await funnel.build_candidates(pool, day, capital)
        cand_by_symbol = {c["symbol"]: c for c in candidates}

        quant_top3 = candidates[:3] if track_mode in ("QUANT", "BOTH") else []

        ai_top3 = []
        if track_mode in ("AI", "BOTH") and candidates:
            try:
                # chart_scope="daily" (not "both") and a low MAX_CONCURRENT_AI
                # (set via env by the API when it spawns this subprocess) —
                # the VPS is 961MB RAM total; concurrent daily+weekly chart
                # rendering at concurrency 5 OOM-killed the process in testing.
                result = await analyze_symbols(
                    [c["symbol"] for c in candidates], day, screener_repo, ai_repo,
                    ai_mode="gemini", chart_scope="daily", prompt_version=ai_config.PROMPT_VERSION,
                )
                for r in result.get("results", []):
                    if r.get("error") or r["symbol"] not in cand_by_symbol:
                        continue
                    ai_top3.append({
                        **cand_by_symbol[r["symbol"]],
                        "ai_confidence": (r.get("analysis") or {}).get("confidence"),
                        "ai_recommendation": (r.get("analysis") or {}).get("recommendation"),
                    })
                    if len(ai_top3) == 3:
                        break
            except Exception:
                logger.exception("AI analysis failed for %s — continuing quant-only that day", day)

        # 3. merge picks (one trade per symbol, tracked in both ranks if it
        #    appears in both lists — spec §8.1)
        picks: dict[str, dict] = {}
        for i, c in enumerate(quant_top3, 1):
            picks.setdefault(c["symbol"], {"candidate": c})["quant_rank"] = i
        for i, c in enumerate(ai_top3, 1):
            d = picks.setdefault(c["symbol"], {"candidate": c})
            d["ai_rank"] = i
            d["ai_confidence"] = c.get("ai_confidence")
            d["ai_recommendation"] = c.get("ai_recommendation")

        # 4. create new trades, respecting the stacking guard
        for sym, info in picks.items():
            existing = [t for t in active if t.symbol == sym]
            if stacking_guard and existing:
                open_ones = [t for t in existing if t.status == "OPEN"]
                pending_ones = [t for t in existing if t.status == "PENDING"]
                if open_ones:
                    continue  # always skip — can't override a filled position
                if pending_ones:
                    if stacking_guard_mode == "OVERRIDE":
                        for t in pending_ones:
                            t.status = "SUPERSEDED"
                            t.exit_reason = "SUPERSEDED"
                            await _persist(pool, run_id, t, db_id)
                            active.remove(t)
                    else:  # SKIP (default)
                        continue

            c = info["candidate"]
            trade = SimTrade(
                symbol=sym, signal_date=day, entry_trigger_price=c["entry"],
                structural_sl=c["sl"], target_price=c["target"],
                risk_per_share=c["risk_per_share"], quantity=c["qty"],
                entry_type=c["entry_type"], base_stage=c["base_stage"],
                quant_rank=info.get("quant_rank"), ai_rank=info.get("ai_rank"),
                ai_confidence=info.get("ai_confidence"), ai_recommendation=info.get("ai_recommendation"),
            )
            signal_day_index[id(trade)] = day_idx
            await _persist(pool, run_id, trade, db_id)
            active.append(trade)

        await pool.execute(
            "UPDATE backtest_runs SET progress_day=$1 WHERE id=$2", day_idx + 1, run_id
        )

    # Window end: whatever's left in `active` simply stays PENDING/OPEN in
    # the DB (already persisted above every day) — no forced close, per spec.
    await pool.execute(
        "UPDATE backtest_runs SET status='COMPLETED', completed_at=NOW() WHERE id=$1", run_id
    )


async def _persist(pool, run_id: int, t: SimTrade, db_id: dict[int, int]) -> None:
    """Insert on first sight of a trade, update on every subsequent state
    change. Keyed by python id() for this run's in-memory objects only."""
    key = id(t)
    if key not in db_id:
        row = await pool.fetchrow(
            """
            INSERT INTO backtest_trades
              (run_id, symbol, quant_rank, ai_rank, signal_date, entry_trigger_price,
               structural_sl, target_price, risk_per_share, quantity, entry_type,
               base_stage, ai_confidence, ai_recommendation, status)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
            RETURNING id
            """,
            run_id, t.symbol, t.quant_rank, t.ai_rank, t.signal_date, t.entry_trigger_price,
            t.structural_sl, t.target_price, t.risk_per_share, t.quantity, t.entry_type,
            t.base_stage, t.ai_confidence, t.ai_recommendation, t.status,
        )
        db_id[key] = row["id"]
        return

    await pool.execute(
        """
        UPDATE backtest_trades SET
          status=$2, entry_fill_date=$3, entry_fill_price=$4, half_booked=$5, trail_sl=$6,
          exit_date=$7, exit_price=$8, exit_reason=$9, realized_pnl=$10,
          r_multiple=$11, holding_days=$12
        WHERE id=$1
        """,
        db_id[key], t.status, t.entry_fill_date, t.entry_fill_price, t.half_booked, t.current_sl,
        t.exit_date, t.exit_price, t.exit_reason, t.realized_pnl,
        (round(t.realized_pnl / (t.risk_per_share * t.quantity), 3)
         if t.status == "CLOSED" and t.risk_per_share and t.quantity else None),
        ((t.exit_date - t.entry_fill_date).days
         if t.status == "CLOSED" and t.exit_date and t.entry_fill_date else None),
    )
