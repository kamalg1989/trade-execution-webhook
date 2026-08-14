"""Orchestrator for the Weekly Consolidation Breakout strategy backtest —
see weekly_breakout.py (signal generation) and weekly_simulator.py (trade
lifecycle). Runs on a WEEKLY cadence, driven off the precomputed
ohlcv_weekly table (sql/025) — completely separate from engine.py's daily
loop, dispatched from engine.run_backtest() based on backtest_runs.strategy.

Two-phase design for efficiency: naively re-scanning the whole ~2800-symbol
universe for a fresh breakout signal on every one of ~550 weeks (a ~1.5M-
call inner loop) would be needlessly slow, since the box-search + indicator
checks only ever need each symbol's OWN price history, never information
from other symbols or from "today". So instead:
  Phase A: for each symbol (in parallel, thread pool — CPU-bound pandas
           work), scan its ENTIRE weekly history ONCE for every valid
           breakout signal, independent of any other symbol.
  Phase B: run the (point-in-time) fundamentals filter only over that much
           smaller signal shortlist, not the whole universe every week.
  Phase C: the actual week-by-week trade-lifecycle loop (fills, MACD-trail
           exits, opening new positions) just looks up
           signals_by_week[week] — O(1), no re-scanning.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date

import pandas as pd

from . import weekly_breakout as wb
from .weekly_simulator import WeeklyTrade, step_exit_weekly, try_fill_weekly

logger = logging.getLogger(__name__)

MIN_HISTORY_WEEKS = wb.BOX_MIN_WEEKS + wb.SMA_TREND_WEEKS + 5


async def run_weekly_backtest(run: dict, pool) -> None:
    run_id = run["id"]
    start_date, end_date = run["start_date"], run["end_date"]
    capital = float(run["capital"])
    risk_pct = float(run.get("weekly_risk_pct") or 1.0)
    resting_window_weeks = run.get("resting_window_days") or 2
    stacking_guard = bool(run.get("stacking_guard"))
    max_picks = int(run.get("max_picks_per_track") or 3)

    cfg = {
        "slippage_pct": float(run["slippage_pct"]),
        "brokerage_per_order": float(run["brokerage_per_order"]),
        "stt_pct": float(run["stt_pct"]),
        "stamp_duty_pct": float(run["stamp_duty_pct"]),
        "exchange_charges_pct": float(run["exchange_charges_pct"]),
        "dp_charge": float(run["dp_charge"]),
    }

    symbols = await _eligible_symbols(pool)
    frames = await _load_all_weekly_frames(pool, symbols, end_date)
    logger.info("weekly run %s: %d/%d symbols have enough history", run_id, len(frames), len(symbols))

    # Phase A — parallel per-symbol full-history signal scan.
    sem = asyncio.Semaphore(4)

    async def _scan_one(sym: str):
        async with sem:
            return await asyncio.to_thread(_scan_symbol_signals, frames[sym], sym, start_date, end_date)

    scanned = await asyncio.gather(*(_scan_one(s) for s in frames))
    raw_signals = [sig for sigs in scanned for sig in sigs]
    logger.info("weekly run %s: %d raw breakout signals before fundamentals filter", run_id, len(raw_signals))

    # Phase B — fundamentals filter, only over the shortlist.
    signals_by_week: dict[date, list] = {}
    for sig in raw_signals:
        if await wb.fundamentals_pass(pool, sig.symbol, sig.signal_week_end):
            signals_by_week.setdefault(sig.signal_week_end, []).append(sig)

    # Phase C — week-by-week trade lifecycle.
    all_week_ends = sorted({
        d for df in frames.values() for d in df.index if start_date <= d <= end_date
    })
    total_weeks = len(all_week_ends)
    await pool.execute("UPDATE backtest_runs SET progress_total_days=$1 WHERE id=$2", total_weeks, run_id)

    active: list[WeeklyTrade] = []

    for week_idx, week_end in enumerate(all_week_ends):
        still_active = []
        for t in active:
            df = frames.get(t.symbol)
            bar = _bar_at(df, week_end) if df is not None else None
            if bar is not None:
                if t.status == "PENDING":
                    since = week_idx - t.signal_week_idx
                    try_fill_weekly(t, week_end, bar, resting_window_weeks, since, cfg)
                if t.status == "OPEN":
                    step_exit_weekly(t, week_end, bar, cfg)
            if t.status in ("PENDING", "OPEN"):
                still_active.append(t)
        if active:
            await asyncio.gather(*(_persist(pool, run_id, t) for t in active))
        active = still_active

        active_symbols = {t.symbol for t in active}
        todays = signals_by_week.get(week_end, [])
        todays = [s for s in todays if not (stacking_guard and s.symbol in active_symbols)]
        todays.sort(key=lambda s: -s.box_weeks)  # prefer bigger/longer bases first
        picks = todays[:max_picks]

        for sig in picks:
            qty = wb.size_position(capital, sig.entry_trigger, sig.initial_stop, risk_pct)
            if qty <= 0:
                continue
            trade = WeeklyTrade(
                symbol=sig.symbol, signal_week_end=sig.signal_week_end,
                entry_trigger_price=sig.entry_trigger, structural_sl=sig.initial_stop,
                risk_per_share=round(sig.entry_trigger - sig.initial_stop, 2), quantity=qty,
                box_weeks=sig.box_weeks, box_depth_pct=sig.box_depth_pct,
            )
            trade.signal_week_idx = week_idx
            await _persist(pool, run_id, trade)
            active.append(trade)

        await pool.execute("UPDATE backtest_runs SET progress_day=$1 WHERE id=$2", week_idx + 1, run_id)

    await pool.execute(
        "UPDATE backtest_runs SET status='COMPLETED', completed_at=NOW() WHERE id=$1", run_id
    )


async def raw_breakout_week_ends(pool, start_date: date, end_date: date) -> dict[str, list[date]]:
    """Weekly consolidation-box breakout weeks per symbol — signal generation
    ONLY (no fundamentals filter; a caller using this as a daily-engine entry
    gate already has its own liquidity/quality gates on top, see
    engine.py's require_weekly_box_breakout). Reuses this module's own Phase
    A exactly (see run_weekly_backtest) so the definition of "breakout" can
    never drift from the WEEKLY_BREAKOUT strategy it's borrowed from.
    Returns {symbol: sorted [week_end, ...]} — symbols with zero signals are
    simply absent, not an empty list."""
    symbols = await _eligible_symbols(pool)
    frames = await _load_all_weekly_frames(pool, symbols, end_date)
    sem = asyncio.Semaphore(4)

    async def _scan_one(sym: str):
        async with sem:
            return await asyncio.to_thread(_scan_symbol_signals, frames[sym], sym, start_date, end_date)

    scanned = await asyncio.gather(*(_scan_one(s) for s in frames))
    out: dict[str, list[date]] = {}
    for sigs in scanned:
        for sig in sigs:
            out.setdefault(sig.symbol, []).append(sig.signal_week_end)
    for sym in out:
        out[sym].sort()
    return out


def _scan_symbol_signals(df: pd.DataFrame, symbol: str, start_date: date, end_date: date) -> list:
    """Only the weeks that pass wb._scan_prep()'s vectorized fast_pass gate
    are ever handed to wb.scan_breakout() — see the perf note in
    weekly_breakout.py's module docstring. `candidates` is np.nonzero() on a
    numpy bool array, so it's already in ascending index order; the
    end_date `break` below is therefore still a valid short-circuit."""
    if len(df) <= MIN_HISTORY_WEEKS:
        return []
    fast_pass, rollups, turnover_4wk = wb._scan_prep(df)
    candidates = fast_pass[MIN_HISTORY_WEEKS:].nonzero()[0] + MIN_HISTORY_WEEKS

    out = []
    for idx in candidates:
        week_end = df.index[idx]
        if week_end < start_date:
            continue
        if week_end > end_date:
            break
        sig = wb.scan_breakout(df, idx, symbol, rollups, turnover_4wk)
        if sig is not None:
            out.append(sig)
    return out


async def _eligible_symbols(pool) -> list[str]:
    rows = await pool.fetch(
        "SELECT symbol FROM symbols_meta WHERE is_active = true AND COALESCE(is_sme, false) = false "
        "AND (series IS NULL OR series = 'EQ')"
    )
    return [r["symbol"] for r in rows]


async def _load_all_weekly_frames(pool, symbols: list[str], upto: date) -> dict[str, pd.DataFrame]:
    rows = await pool.fetch(
        "SELECT symbol, week_end, open, high, low, close, volume FROM ohlcv_weekly "
        "WHERE symbol = ANY($1) AND week_end <= $2 ORDER BY symbol, week_end ASC",
        symbols, upto,
    )
    by_symbol: dict[str, list] = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"], []).append(r)
    frames = {}
    for sym, rs in by_symbol.items():
        if len(rs) < MIN_HISTORY_WEEKS:
            continue
        df = pd.DataFrame([dict(r) for r in rs])
        df = df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                                 "close": "Close", "volume": "Volume"})
        df = df[["Open", "High", "Low", "Close", "Volume", "week_end"]].astype(
            {"Open": float, "High": float, "Low": float, "Close": float, "Volume": float}
        )
        df = df.set_index("week_end")
        frames[sym] = wb.compute_weekly_indicators(df)
    return frames


def _bar_at(df: pd.DataFrame, week_end: date) -> dict | None:
    try:
        pos = df.index.get_loc(week_end)
    except KeyError:
        return None
    if not isinstance(pos, int):
        return None
    row = df.iloc[pos]
    prev = df.iloc[pos - 1] if pos > 0 else None
    return {
        "open": float(row["Open"]), "high": float(row["High"]),
        "low": float(row["Low"]), "close": float(row["Close"]),
        "macd_line": float(row["macd_line"]) if pd.notna(row["macd_line"]) else None,
        "macd_signal": float(row["macd_signal"]) if pd.notna(row["macd_signal"]) else None,
        "macd_line_prev": float(prev["macd_line"]) if prev is not None and pd.notna(prev["macd_line"]) else None,
        "macd_signal_prev": float(prev["macd_signal"]) if prev is not None and pd.notna(prev["macd_signal"]) else None,
    }


async def _persist(pool, run_id: int, t: WeeklyTrade) -> None:
    if t.db_id is None:
        row = await pool.fetchrow(
            """
            INSERT INTO backtest_trades
              (run_id, symbol, quant_rank, signal_date, entry_trigger_price,
               structural_sl, target_price, risk_per_share, quantity, entry_type, status)
            VALUES ($1,$2,1,$3,$4,$5,0.0,$6,$7,$8,$9)
            RETURNING id
            """,
            run_id, t.symbol, t.signal_week_end, t.entry_trigger_price,
            t.structural_sl, t.risk_per_share, t.quantity, "WEEKLY_BOX_BREAKOUT", t.status,
        )
        t.db_id = row["id"]
        return
    await pool.execute(
        """
        UPDATE backtest_trades SET
          status=$2, entry_fill_date=$3, entry_fill_price=$4, trail_sl=$5,
          exit_date=$6, exit_price=$7, exit_reason=$8, realized_pnl=$9,
          r_multiple=$10, holding_days=$11, gross_pnl=$12
        WHERE id=$1
        """,
        t.db_id, t.status, t.entry_fill_date, t.entry_fill_price, t.current_sl,
        t.exit_date, t.exit_price, t.exit_reason, t.realized_pnl,
        (round(t.realized_pnl / (t.risk_per_share * t.quantity), 3)
         if t.status == "CLOSED" and t.risk_per_share and t.quantity else None),
        ((t.exit_date - t.entry_fill_date).days
         if t.status == "CLOSED" and t.exit_date and t.entry_fill_date else None),
        t.gross_pnl,
    )
