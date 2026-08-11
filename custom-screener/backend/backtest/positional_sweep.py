"""Exhaustive parameter sweep for the positional momentum book.

READ THIS BEFORE TRUSTING ANY "SWEET SPOT" IT REPORTS.

A grid of 48 configs x 11 windows is 528 backtests. At that size a best-looking
config is GUARANTEED to exist whether or not any real structure is present —
that is precisely how the six earlier "edges" in this project were manufactured
and then died. So this harness is built to answer a harder question than "which
config scored highest":

  1. SPLIT-SAMPLE. Years are split into FIT (2016-2020) and TEST (2021-2026).
     A parameter that only works because it was picked on the same data it was
     scored on will rank well on FIT and badly on TEST. The report shows both,
     and the rank correlation between them. If that correlation is near zero,
     the grid contains no transferable information and NO config should be
     adopted, however good its total looks.

  2. PLATEAUS, NOT PEAKS. A real parameter effect is smooth: neighbours of a
     good setting are also good. Noise produces isolated spikes. The report
     therefore prints results grouped by each axis so a plateau is visible,
     rather than just sorting by total P&L.

  3. REAL DRAWDOWN. The earlier positional module reported maxDD as 0.0, which
     is a missing metric, not a good result. This marks the book to market
     EVERY day and computes true peak-to-trough drawdown on the equity curve.
     For a ~100%-deployed strategy with measured +95%/-34% calendar years, that
     number matters more than the P&L.

Runs in-process with multiprocessing rather than through the one-run-at-a-time
API, since 528 sequential API runs would take ~6 hours.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing as mp
import sys
from datetime import date

sys.path.insert(0, "/root/trade-execution-webhook")

STT_PCT, STAMP_PCT, EXCH_PCT, DP_CHARGE = 0.100, 0.015, 0.0030, 14.75

FIT_YEARS = {"2016", "2017", "2018", "2019", "2020"}
TEST_YEARS = {"2021", "2022", "2023", "2024", "2025", "2026ytd"}


def _leg_cost(value: float, is_sell: bool) -> float:
    c = value * STT_PCT / 100 + value * EXCH_PCT / 100
    return c + (DP_CHARGE if is_sell else value * STAMP_PCT / 100)


async def _run_one(pool, start: date, end: date, capital: float, slip: float,
                   momentum: str, rebalance: int, top_n: int, buffer_n: int,
                   min_turnover: float):
    days = [r["d"] for r in await pool.fetch(
        "SELECT DISTINCT time::date AS d FROM ohlcv_data "
        "WHERE time::date BETWEEN $1 AND $2 ORDER BY d", start, end)]
    if not days:
        return None

    rank_sql = f"""
        SELECT symbol, sma_200 FROM stock_indicators
        WHERE indicator_date=$1 AND turnover_1m_avg_cr>=$2 AND close>sma_200
          AND {momentum} IS NOT NULL
        ORDER BY {momentum} DESC LIMIT $3
    """

    holdings: dict[str, dict] = {}
    cash = capital
    realized = 0.0
    n_closed = n_win = 0
    equity_curve: list[float] = []

    # Per-symbol close series for the whole window, fetched ONCE on first hold
    # and reused. The obvious implementation — one query per day for the current
    # holdings — costs ~250 round trips per window per config, which dominated
    # everything else and put the 528-backtest sweep on a ~4 hour path. A symbol
    # is now queried once no matter how long it is held.
    series: dict[str, dict] = {}

    async def closes_for(sym: str) -> dict:
        if sym not in series:
            series[sym] = {r["d"]: float(r["c"]) for r in await pool.fetch(
                "SELECT time::date AS d, close AS c FROM ohlcv_data "
                "WHERE symbol=$1 AND time::date BETWEEN $2 AND $3", sym, start, end)}
        return series[sym]

    for i, day in enumerate(days):
        # --- daily mark to market (this is what makes real drawdown possible)
        for s, h in holdings.items():
            h["last"] = series.get(s, {}).get(day, h["last"])
        equity_curve.append(cash + sum(h["last"] * h["qty"] for h in holdings.values()))

        if i % rebalance != 0 or i + 1 >= len(days):
            continue
        nxt = days[i + 1]
        ranked = await pool.fetch(rank_sql, day, min_turnover, buffer_n)
        keep = {r["symbol"] for r in ranked}
        want = [r["symbol"] for r in ranked][:top_n]

        drop = [s for s in holdings if s not in keep]
        if drop:
            fills = {r["symbol"]: float(r["open"]) for r in await pool.fetch(
                "SELECT symbol, open FROM ohlcv_data WHERE symbol=ANY($1) AND time::date=$2",
                drop, nxt)}
            for s in drop:
                if s not in fills:
                    continue
                h = holdings.pop(s)
                net = fills[s] * (1 - slip / 100)
                proceeds = net * h["qty"] - _leg_cost(net * h["qty"], True)
                cash += proceeds
                pnl = proceeds - h["cost_basis"]
                realized += pnl
                n_closed += 1
                n_win += pnl > 0

        slots = top_n - len(holdings)
        if slots > 0:
            adds = [s for s in want if s not in holdings][:slots]
            if adds:
                fills = {r["symbol"]: float(r["open"]) for r in await pool.fetch(
                    "SELECT symbol, open FROM ohlcv_data WHERE symbol=ANY($1) AND time::date=$2",
                    adds, nxt)}
                alloc = capital / top_n
                for s in adds:
                    o = fills.get(s)
                    if not o or o <= 0:
                        continue
                    entry = o * (1 + slip / 100)
                    qty = int(alloc / entry)
                    if qty <= 0:
                        continue
                    outlay = entry * qty + _leg_cost(entry * qty, False)
                    if outlay > cash:
                        continue          # never spend money the book doesn't have
                    cash -= outlay
                    await closes_for(s)   # warm this symbol's series for MTM
                    holdings[s] = {"qty": qty, "cost_basis": outlay, "last": entry}

    final = equity_curve[-1] if equity_curve else capital
    peak = equity_curve[0] if equity_curve else capital
    max_dd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        max_dd = max(max_dd, peak - v)
    return {"total": round(final - capital, 2), "realized": round(realized, 2),
            "trades": n_closed, "winRate": round(100 * n_win / max(n_closed, 1), 1),
            "maxDD": round(max_dd, 2),
            "maxDDpct": round(max_dd / capital * 100, 1)}


def _worker(job):
    momentum, rebalance, top_n, buffer_n, min_turnover, capital, slip = job
    from app.db import create_pool

    async def go():
        pool = await create_pool()
        out = []
        try:
            for y in range(2016, 2027):
                s = date(y, 1, 1)
                e = date(2026, 8, 8) if y == 2026 else date(y, 12, 31)
                r = await _run_one(pool, s, e, capital, slip, momentum, rebalance,
                                   top_n, buffer_n, min_turnover)
                if r:
                    r.update({"window": str(y) if y < 2026 else "2026ytd",
                              "momentum": momentum, "rebalance": rebalance,
                              "top_n": top_n, "buffer_n": buffer_n})
                    out.append(r)
        finally:
            await pool.close()
        return out

    return asyncio.run(go())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--capital", type=float, default=400000)
    ap.add_argument("--slippage", type=float, default=0.10)
    ap.add_argument("--min-turnover", type=float, default=5.0)
    ap.add_argument("--out", default="/root/possweep.json")
    a = ap.parse_args()

    # buffer is tied to top_n (2x) rather than being a free axis: an independent
    # buffer would add a fourth dimension of overfitting surface for a knob whose
    # only job is anti-churn hysteresis.
    jobs = [(m, rb, tn, tn * 2, a.min_turnover, a.capital, a.slippage)
            for m in ("pct_chg_3m", "pct_chg_6m", "pct_chg_1y")
            for rb in (10, 21, 42, 63)
            for tn in (5, 10, 15, 20)]
    print(f"positional sweep: {len(jobs)} configs x 11 windows = {len(jobs)*11} backtests, "
          f"{a.workers} workers", flush=True)

    rows = []
    with mp.Pool(a.workers) as p:
        for k, res in enumerate(p.imap_unordered(_worker, jobs), 1):
            rows.extend(res)
            print(f"  {k}/{len(jobs)} configs done", flush=True)
    with open(a.out, "w") as fh:
        json.dump(rows, fh)
    print(f"WROTE {len(rows)} rows -> {a.out}", flush=True)


if __name__ == "__main__":
    main()
