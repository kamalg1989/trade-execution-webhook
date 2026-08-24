"""Print REVERSE_HS detections as an ASCII chart so the SHAPE can be judged.

REVERSE_HS is the detector most likely to be wrong: it is the only genuinely new
pattern, it has the most free parameters (60-session window, 15% shoulder
symmetry, 5-bar fractal pivots), and it has the most complex logic (three
troughs, ordering, neckline construction). It also fires rarely — ~40 times in
the 2023 sample — so each detection carries more weight per trade than the
others.

A table of numbers cannot show whether three lows form a head and shoulders;
only the shape can. So this renders the 60-bar window as an ASCII chart with the
left shoulder, head, right shoulder and neckline marked, and prints the actual
symmetry arithmetic the detector used.

WHAT TO LOOK FOR:
  - Is the head clearly the LOWEST of the three?
  - Are the shoulders roughly level with each other?
  - Is the neckline a real resistance line, or just the highest bar between?
  - Does the structure look like a rounded bottom, or three unrelated dips?

Run:  python3 -m backtest.hs_inspect --year 2023 --n 4
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date

sys.path.insert(0, "/root/trade-execution-webhook")

from .buy_points import HS_SYMMETRY, HS_WINDOW, _swing_lows

ROWS = 18          # vertical resolution of the ASCII chart


def chart(sym, day, df, trigger):
    highs = [float(x) for x in df["High"].tolist()]
    lows = [float(x) for x in df["Low"].tolist()]
    closes = [float(x) for x in df["Close"].tolist()]
    dates = [str(ix.date()) for ix in df.index]

    win_lows = lows[-HS_WINDOW:]
    win_highs = highs[-HS_WINDOW:]
    win_closes = closes[-HS_WINDOW:]
    win_dates = dates[-HS_WINDOW:]

    piv = _swing_lows(win_lows)
    li, hi_, ri = piv[-3], piv[-2], piv[-1]
    left, head, right = win_lows[li], win_lows[hi_], win_lows[ri]
    depth = min(left, right) - head
    neck = max(max(win_highs[li:hi_ + 1]), max(win_highs[hi_:ri + 1]))

    print(f"\n{'='*94}")
    print(f"{sym}   detected {day}   trigger: {trigger}")
    print(f"{'='*94}")
    print(f"  left shoulder  {win_dates[li]}  {left:>10.2f}")
    print(f"  HEAD           {win_dates[hi_]}  {head:>10.2f}   "
          f"{'OK: lowest of the three' if head < left and head < right else 'PROBLEM: not lowest'}")
    print(f"  right shoulder {win_dates[ri]}  {right:>10.2f}")
    print(f"  head depth = min(L,R) - head = {depth:.2f}")
    print(f"  shoulder gap = |L - R| = {abs(left-right):.2f}   "
          f"limit = {HS_SYMMETRY:.0%} of depth = {HS_SYMMETRY*depth:.2f}   "
          f"{'OK' if abs(left-right) <= HS_SYMMETRY*depth else 'PROBLEM'}")
    print(f"  neckline = {neck:.2f}   today high = {highs[-1]:.2f} "
          f"({100*(highs[-1]-neck)/neck:+.1f}% vs neckline)")

    lo, hi = min(win_lows), max(win_highs)
    rng = (hi - lo) or 1.0

    def row_of(v):
        return int((hi - v) / rng * (ROWS - 1))

    grid = [[" "] * len(win_lows) for _ in range(ROWS)]
    for x, c in enumerate(win_closes):
        grid[row_of(c)][x] = "."
    for x, label in ((li, "L"), (hi_, "H"), (ri, "R")):
        grid[row_of(win_lows[x])][x] = label
    neck_row = row_of(neck)

    print()
    for r in range(ROWS):
        edge = f"{hi - r*rng/(ROWS-1):>9.2f} |"
        line = "".join(grid[r])
        if r == neck_row:
            line = "".join(ch if ch != " " else "-" for ch in line) + "  <- neckline"
        print(edge + line)
    print(" " * 10 + "+" + "-" * len(win_lows))
    print(" " * 11 + win_dates[0] + " " * max(1, len(win_lows) - 21) + win_dates[-1])
    print("  legend: . = close   L = left shoulder   H = head   R = right shoulder")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2023)
    ap.add_argument("--n", type=int, default=4)
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
                if df is None or len(df) < HS_WINDOW + 5:
                    continue
                if "REVERSE_HS" not in detect_buy_points(df, sym):
                    continue
                trig = screen_gpt.detect_entry_technique(df, symbol=sym)
                chart(sym, d, df, trig.get("type") or "-")
                shown += 1
    finally:
        await pool.close()
    print(f"\n{shown} examples.")


if __name__ == "__main__":
    asyncio.run(main())
