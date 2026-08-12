"""Print BREAKOUT_RETEST detections with enough context to judge them by eye.

BREAKOUT_RETEST is now the most common buy point (535 of 972 in the 2023 sample),
so it will drive most of entry v2's trades. Four bars of context is not enough to
tell whether a detection is a real retest: you need to see the base that formed,
the bar that cleared it, and the return to the level. This prints ~28 bars with
the base-high level marked and each bar tagged, so the sequence is checkable
against a chart without trusting the detector's own verdict.

Run:  python3 -m backtest.retest_inspect --year 2023 --n 6
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date

sys.path.insert(0, "/root/trade-execution-webhook")

from .buy_points import BASE_LOOKBACK_BARS, NEAR_PCT, RETEST_MAX_BARS


def render(sym, day, df, trigger):
    highs = [float(x) for x in df["High"].tolist()]
    lows = [float(x) for x in df["Low"].tolist()]
    opens = [float(x) for x in df["Open"].tolist()]
    closes = [float(x) for x in df["Close"].tolist()]
    dates = [str(ix.date()) for ix in df.index]

    # Recompute exactly what the detector used, so the printout cannot drift
    # from the decision it is meant to explain.
    prior = highs[-(BASE_LOOKBACK_BARS + RETEST_MAX_BARS + 1):-(RETEST_MAX_BARS + 1)]
    base_high = max(prior)
    base_start = len(highs) - (BASE_LOOKBACK_BARS + RETEST_MAX_BARS + 1)
    base_end = len(highs) - (RETEST_MAX_BARS + 1)

    print(f"\n{'='*92}")
    print(f"{sym}   detected {day}   trigger: {trigger}")
    print(f"base high (bars {base_start}..{base_end-1}) = {base_high:.2f}"
          f"   retest band = low <= {base_high*(1+NEAR_PCT):.2f}, close > {base_high:.2f}")
    print(f"{'='*92}")
    print(f"{'#':>3} {'date':<12}{'open':>9}{'high':>9}{'low':>9}{'close':>9}   marker")

    n = len(highs)
    start = max(0, n - (BASE_LOOKBACK_BARS + RETEST_MAX_BARS + 4))
    for i in range(start, n):
        mark = ""
        if base_start <= i < base_end:
            mark = "base"
        elif i >= base_end and i < n - 1:
            mark = "BROKE OUT" if highs[i] > base_high else "  ·"
        if i == n - 1:
            mark = ">>> RETEST BAR (today)"
        rel = "  " if highs[i] <= base_high else " ^"   # ^ = above base high
        print(f"{i-n+1:>3} {dates[i]:<12}{opens[i]:>9.2f}{highs[i]:>9.2f}"
              f"{lows[i]:>9.2f}{closes[i]:>9.2f}{rel}  {mark}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2023)
    ap.add_argument("--n", type=int, default=6, help="examples to print")
    ap.add_argument("--days", type=int, default=25)
    a = ap.parse_args()

    import screen_gpt
    from app.db import create_pool
    from . import funnel
    from .buy_points import detect_buy_points

    screen_gpt.load_tick_sizes()
    screen_gpt.DEBUG = False

    pool = await create_pool()
    shown = 0
    try:
        days = [r["d"] for r in await pool.fetch(
            "SELECT DISTINCT time::date AS d FROM ohlcv_data "
            "WHERE time::date BETWEEN $1 AND $2 ORDER BY d",
            date(a.year, 1, 1), date(a.year, 12, 31))]
        step = max(1, len(days) // a.days)
        for d in days[::step]:
            if shown >= a.n:
                break
            survivors = await funnel.funnel_survivors(pool, d)
            if not survivors:
                continue
            syms = [r["symbol"] for r in survivors]
            frames = await funnel.load_ohlcv_frames_batch(pool, syms, d)
            for sym in syms:
                if shown >= a.n:
                    break
                df = frames.get(sym)
                if df is None or len(df) < 60:
                    continue
                bps = detect_buy_points(df, sym)
                if "BREAKOUT_RETEST" not in bps:
                    continue
                trig = screen_gpt.detect_entry_technique(df, symbol=sym)
                if not trig.get("type"):
                    continue
                render(sym, d, df, trig["type"])
                shown += 1
    finally:
        await pool.close()
    print(f"\n{shown} examples. What to check: did price genuinely CLEAR the base "
          f"high,\nthen come back DOWN to it and hold? If the 'BROKE OUT' bars are "
          f"absent or\nthe retest bar never approaches the level, the detector is "
          f"wrong.")


if __name__ == "__main__":
    asyncio.run(main())
