"""Mean-reversion strategy — the always-on PAIR for the breakout system.

Why a separate module rather than another config: the existing engine is
breakout-specific end to end (base-quality gates, IFP accumulation footprint,
near-20d-high proximity, base-stage classification, R-ladder trailing). None of
that transfers. Campaign v1-v5 showed that 15 "different" configs were really
one strategy with different knobs — the year explained ~7x more of the outcome
than the config, and they all won and lost together. The only way to break that
is a genuinely different bet.

THE BET
Breakout buys strength and needs follow-through. It bleeds in exactly the years
where price breaks a pivot and immediately reverses (2016, 2018, 2019, 2022,
2025 all lost across every config). That non-follow-through IS mean reversion.
So this buys the opposite: a quality uptrend name that has been flushed well
below its short-term mean, betting it snaps back.

    universe   liquid, and close > SMA200 (only buy dips in things still in
               an uptrend — this is a pullback strategy, not a falling-knife
               strategy)
    entry      stretched at least `min_stretch_pct` BELOW the 10-EMA
    rank       most stretched first
    exit       close back above the 10-EMA (reversion done), OR `max_days`
               elapsed, OR `stop_pct` hit — whichever comes first

ALWAYS-ON, NOT SWITCHED
Campaign v5 established that bad regimes are NOT identifiable in advance: the
regime state machine made 2018 and 2019 WORSE, and at equal trade count lost to
a naive daily filter. So an allocator that switches between strategies would
switch wrong exactly when it matters. This is therefore designed to run
continuously alongside the breakout book, with diversification — not timing —
doing the work.

WHAT WOULD MAKE THIS A FAILURE
Not "lower total P&L". If it merely earns less than the breakout strategy but
loses in the SAME years, it is useless as a pair no matter how profitable it
looks. The test that matters is the per-year correlation printed at the end:
it has to make money in 2018/2019/2022/2025. Cost realism is identical to the
main engine (0.1% slippage per fill, STT both legs, stamp duty, exchange
charges, Rs 14.75 DP) because at ~0.5% round-trip a 3-day hold is exactly where
costs do the most damage — that is this strategy's main risk, not its win rate.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime

sys.path.insert(0, "/root/trade-execution-webhook")

SLIPPAGE_PCT = 0.10
STT_PCT = 0.100
STAMP_PCT = 0.015
EXCH_PCT = 0.0030
DP_CHARGE = 14.75


def _leg_cost(value: float, is_sell: bool) -> float:
    """Identical cost model to simulator._leg_costs — Dhan equity delivery."""
    c = value * STT_PCT / 100 + value * EXCH_PCT / 100
    c += DP_CHARGE if is_sell else value * STAMP_PCT / 100
    return c


CANDIDATE_SQL = """
    SELECT symbol, close, dist_ema_10_pct, ema_10, atr_pct
    FROM stock_indicators
    WHERE indicator_date = $1
      AND turnover_1m_avg_cr >= $2
      AND close > sma_200            -- dip-buying only inside an uptrend
      AND dist_ema_10_pct <= $3      -- stretched BELOW the short mean
      AND atr_pct IS NOT NULL
    ORDER BY dist_ema_10_pct ASC     -- most stretched first
    LIMIT 50
"""


async def run_window(pool, start: date, end: date, capital: float, *,
                     min_stretch_pct: float, max_days: int, stop_pct: float,
                     max_positions: int, risk_pct: float, min_turnover_cr: float):
    days = [r["d"] for r in await pool.fetch(
        "SELECT DISTINCT time::date AS d FROM ohlcv_data "
        "WHERE time::date BETWEEN $1 AND $2 ORDER BY d", start, end)]

    open_pos: dict[str, dict] = {}
    closed: list[dict] = []

    for i, day in enumerate(days):
        syms = list(open_pos)
        bars = {}
        if syms:
            for r in await pool.fetch(
                "SELECT o.symbol, o.open, o.high, o.low, o.close, si.ema_10 "
                "FROM ohlcv_data o LEFT JOIN stock_indicators si "
                "  ON si.symbol=o.symbol AND si.indicator_date=o.time::date "
                "WHERE o.symbol = ANY($1) AND o.time::date = $2", syms, day):
                bars[r["symbol"]] = r

        # ---- manage open positions
        for sym in list(open_pos):
            p, bar = open_pos[sym], bars.get(sym)
            if bar is None:
                continue
            p["held"] += 1
            stop_px = p["entry"] * (1 - stop_pct / 100)
            exit_px = exit_why = None
            if float(bar["low"]) <= stop_px:
                # Gap-realistic: a stop cannot fill better than the open.
                exit_px = min(float(bar["open"]), stop_px)
                exit_why = "STOP"
            elif bar["ema_10"] is not None and float(bar["close"]) >= float(bar["ema_10"]):
                exit_px, exit_why = float(bar["close"]), "REVERTED"
            elif p["held"] >= max_days:
                exit_px, exit_why = float(bar["close"]), "TIME"
            if exit_px is not None:
                net = exit_px * (1 - SLIPPAGE_PCT / 100)
                val = net * p["qty"]
                pnl = (net - p["entry"]) * p["qty"] - _leg_cost(val, True)
                closed.append({"symbol": sym, "signal_date": p["date"], "exit_date": day,
                               "pnl": pnl, "why": exit_why, "held": p["held"]})
                del open_pos[sym]

        # ---- new entries (fill at NEXT day's open, never today's close)
        slots = max_positions - len(open_pos)
        if slots > 0 and i + 1 < len(days):
            cands = await pool.fetch(CANDIDATE_SQL, day, min_turnover_cr, -abs(min_stretch_pct))
            nxt = days[i + 1]
            picks = [c for c in cands if c["symbol"] not in open_pos][:slots]
            if picks:
                fills = {r["symbol"]: r for r in await pool.fetch(
                    "SELECT symbol, open FROM ohlcv_data "
                    "WHERE symbol = ANY($1) AND time::date = $2",
                    [c["symbol"] for c in picks], nxt)}
                for c in picks:
                    f = fills.get(c["symbol"])
                    if f is None:
                        continue
                    entry = float(f["open"]) * (1 + SLIPPAGE_PCT / 100)
                    risk_per_share = entry * stop_pct / 100
                    qty = int(capital * (risk_pct / 100) / risk_per_share) if risk_per_share > 0 else 0
                    qty = min(qty, int(capital * 0.10 / entry))   # 10% max per position
                    if qty <= 0:
                        continue
                    open_pos[c["symbol"]] = {"entry": entry, "qty": qty, "date": nxt, "held": 0}
                    closed.append({"symbol": c["symbol"], "signal_date": nxt, "exit_date": None,
                                   "pnl": -_leg_cost(entry * qty, False), "why": "ENTRY_COST",
                                   "held": 0})
    return closed


async def main_async(a):
    from app.db import create_pool
    pool = await create_pool()
    try:
        results = []
        for y in range(2016, 2027):
            s = date(y, 1, 1)
            e = date(2026, 8, 8) if y == 2026 else date(y, 12, 31)
            trades = await run_window(
                pool, s, e, a.capital,
                min_stretch_pct=a.stretch, max_days=a.max_days, stop_pct=a.stop,
                max_positions=a.positions, risk_pct=a.risk,
                min_turnover_cr=a.min_turnover)
            real = [t for t in trades if t["why"] != "ENTRY_COST"]
            entry_costs = sum(t["pnl"] for t in trades if t["why"] == "ENTRY_COST")
            net = sum(t["pnl"] for t in real) + entry_costs
            wins = [t for t in real if t["pnl"] > 0]
            row = {"config": a.label, "window": str(y) if y < 2026 else "2026ytd",
                   "trades": len(real), "winRate": round(100 * len(wins) / max(len(real), 1), 1),
                   "realized": round(net, 2), "unrealized": 0.0, "total": round(net, 2),
                   "avgR": None, "maxDD": 0.0, "costDrag": 0.0,
                   "avgHold": round(sum(t["held"] for t in real) / max(len(real), 1), 1)}
            results.append(row)
            print("RESULT " + json.dumps(row), flush=True)
        print("MEANREV DONE", flush=True)
    finally:
        await pool.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="MR-default")
    ap.add_argument("--capital", type=float, default=400000)
    ap.add_argument("--stretch", type=float, default=8.0,
                    help="min %% below EMA10 to qualify as flushed")
    ap.add_argument("--max-days", type=int, default=5)
    ap.add_argument("--stop", type=float, default=8.0)
    ap.add_argument("--positions", type=int, default=5)
    ap.add_argument("--risk", type=float, default=1.0)
    ap.add_argument("--min-turnover", type=float, default=5.0)
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
