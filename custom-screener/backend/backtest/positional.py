"""Low-turnover positional momentum — the strategy the cost structure supports.

WHY THIS EXISTS
The measured problem with the breakout book is not selection, it is frequency:

    avg gross move per trade   +0.704%
    avg cost per trade         -0.522%   (0.2% slippage + 0.2% STT + stamp/exch + DP)
    => net edge                +0.18%

At ~200 trades/year that is ~5.7% of capital paid in frictions annually against
a ~7.4% gross return. Ten structural interventions failed to move it, because
none of them changed the frequency. This does.

    breakout book:  ~200 trades/yr, held 14 days, needs +0.7% to clear 0.52%
    this:           ~30-60 trades/yr, held months, needs +0.5% to clear 0.5%
                    of a move that is targeted in tens of percent

If a position is held for six months for a 25% move, the same 0.52% round-trip
is 2% of the move instead of 74% of it. That is the entire thesis, and the
turnover/cost figures printed per year are the test of it — not the P&L alone.

THE STRATEGY (classic cross-sectional momentum / rotation)
    universe    liquid, and close > SMA200 (long-term uptrend intact)
    rank        `momentum` column, highest first
    rebalance   every `rebalance_days` sessions, NOT daily — daily re-ranking is
                what generates turnover, and turnover is the thing being fixed
    hold        top `top_n`. A holding is only SOLD when it falls out of the
                top `buffer_n` (buffer_n > top_n) or loses its SMA200. The
                buffer is deliberate hysteresis: without it a name oscillating
                around rank N churns every rebalance and re-creates the exact
                cost problem this is meant to solve.
    sizing      equal weight, so no single name dominates

DELIBERATELY NOT REUSING THE MAIN ENGINE
That engine is breakout-specific (base quality, IFP, near-20d-high, base-stage,
R-ladder trailing) and is built around one-signal-per-day-per-symbol. A
rebalancing portfolio has a fundamentally different shape. Cost model, slippage
and next-open fills are kept identical so the numbers are comparable.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date

sys.path.insert(0, "/root/trade-execution-webhook")

SLIPPAGE_PCT = 0.10
STT_PCT = 0.100
STAMP_PCT = 0.015
EXCH_PCT = 0.0030
DP_CHARGE = 14.75


def _leg_cost(value: float, is_sell: bool) -> float:
    c = value * STT_PCT / 100 + value * EXCH_PCT / 100
    c += DP_CHARGE if is_sell else value * STAMP_PCT / 100
    return c


async def run_window(pool, start: date, end: date, capital: float, *,
                     momentum: str, rebalance_days: int, top_n: int,
                     buffer_n: int, min_turnover_cr: float):
    days = [r["d"] for r in await pool.fetch(
        "SELECT DISTINCT time::date AS d FROM ohlcv_data "
        "WHERE time::date BETWEEN $1 AND $2 ORDER BY d", start, end)]
    if not days:
        return [], 0.0

    rank_sql = f"""
        SELECT symbol, close, {momentum} AS mom
        FROM stock_indicators
        WHERE indicator_date = $1
          AND turnover_1m_avg_cr >= $2
          AND close > sma_200
          AND {momentum} IS NOT NULL
        ORDER BY {momentum} DESC
        LIMIT $3
    """

    holdings: dict[str, dict] = {}
    trades: list[dict] = []
    costs_paid = 0.0

    for i, day in enumerate(days):
        if i % rebalance_days != 0 or i + 1 >= len(days):
            continue
        nxt = days[i + 1]

        ranked = await pool.fetch(rank_sql, day, min_turnover_cr, buffer_n)
        keep = {r["symbol"] for r in ranked}
        want = [r["symbol"] for r in ranked][:top_n]

        # ---- SELL: dropped out of the buffer entirely (or lost its SMA200,
        #      which removes it from the ranked set by construction).
        for sym in [s for s in holdings if s not in keep]:
            px = await pool.fetchrow(
                "SELECT open FROM ohlcv_data WHERE symbol=$1 AND time::date=$2", sym, nxt)
            if px is None:
                continue
            h = holdings.pop(sym)
            net = float(px["open"]) * (1 - SLIPPAGE_PCT / 100)
            val = net * h["qty"]
            c = _leg_cost(val, True)
            costs_paid += c
            trades.append({"symbol": sym, "entry_date": h["date"], "exit_date": nxt,
                           "pnl": (net - h["entry"]) * h["qty"] - c,
                           "held_days": (nxt - h["date"]).days})

        # ---- BUY: fill open slots from the top of the ranking
        slots = top_n - len(holdings)
        if slots > 0:
            adds = [s for s in want if s not in holdings][:slots]
            if adds:
                fills = {r["symbol"]: r for r in await pool.fetch(
                    "SELECT symbol, open FROM ohlcv_data WHERE symbol = ANY($1) AND time::date=$2",
                    adds, nxt)}
                # Equal weight across the full book, not just the free slots, so
                # position size doesn't balloon when only one slot is open.
                alloc = capital / top_n
                for sym in adds:
                    f = fills.get(sym)
                    if f is None or float(f["open"]) <= 0:
                        continue
                    entry = float(f["open"]) * (1 + SLIPPAGE_PCT / 100)
                    qty = int(alloc / entry)
                    if qty <= 0:
                        continue
                    c = _leg_cost(entry * qty, False)
                    costs_paid += c
                    holdings[sym] = {"entry": entry, "qty": qty, "date": nxt}
                    trades.append({"symbol": sym, "entry_date": nxt, "exit_date": None,
                                   "pnl": -c, "held_days": 0})

    # ---- mark remaining holdings to the last close (open positions, not closed)
    unreal = 0.0
    for sym, h in holdings.items():
        px = await pool.fetchrow(
            "SELECT close FROM ohlcv_data WHERE symbol=$1 AND time::date<=$2 "
            "ORDER BY time DESC LIMIT 1", sym, days[-1])
        if px is not None:
            unreal += (float(px["close"]) - h["entry"]) * h["qty"]
    return trades, unreal


async def main_async(a):
    from app.db import create_pool
    pool = await create_pool()
    try:
        for y in range(2016, 2027):
            s = date(y, 1, 1)
            e = date(2026, 8, 8) if y == 2026 else date(y, 12, 31)
            trades, unreal = await run_window(
                pool, s, e, a.capital, momentum=a.momentum,
                rebalance_days=a.rebalance, top_n=a.top, buffer_n=a.buffer,
                min_turnover_cr=a.min_turnover)
            closed = [t for t in trades if t["exit_date"] is not None]
            realized = sum(t["pnl"] for t in trades)
            wins = [t for t in closed if t["pnl"] > 0]
            row = {"config": a.label, "window": str(y) if y < 2026 else "2026ytd",
                   "trades": len(closed),
                   "winRate": round(100 * len(wins) / max(len(closed), 1), 1),
                   "realized": round(realized, 2), "unrealized": round(unreal, 2),
                   "total": round(realized + unreal, 2),
                   "avgR": None, "maxDD": 0.0, "costDrag": 0.0,
                   "avgHold": round(sum(t["held_days"] for t in closed) / max(len(closed), 1), 0)}
            print("RESULT " + json.dumps(row), flush=True)
        print("POSITIONAL DONE", flush=True)
    finally:
        await pool.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="POS-default")
    ap.add_argument("--capital", type=float, default=400000)
    ap.add_argument("--momentum", default="pct_chg_6m",
                    choices=["pct_chg_3m", "pct_chg_6m", "pct_chg_1y"])
    ap.add_argument("--rebalance", type=int, default=21, help="sessions between rebalances")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--buffer", type=int, default=20,
                    help="hold until rank falls outside this (hysteresis)")
    ap.add_argument("--min-turnover", type=float, default=5.0)
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
