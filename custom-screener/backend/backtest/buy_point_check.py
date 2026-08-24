"""Spot-check the buy-point detectors against real bars BEFORE any backtest.

WHY THIS RUNS FIRST. Nothing validates that what `detect_buy_points()` calls a
pullback is what a trader would call a pullback. A backtest cannot tell you that
either — it will happily produce a confident-looking P&L from a detector that
fires on the wrong thing. The only available ground truth is a human looking at
the bars, so this prints the raw OHLC around every detection in a form that can
be checked by eye against a chart.

It also answers the question that decides whether entry v2 is viable at all:
HOW MANY signals survive requiring BOTH a buy point and a trigger? If the answer
is "almost none", the design is too strict and that is worth knowing before
spending hours on backtests rather than after.

Run:  python3 -m backtest.buy_point_check --year 2023 --samples 4
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from datetime import date

sys.path.insert(0, "/root/trade-execution-webhook")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2023)
    ap.add_argument("--samples", type=int, default=4,
                    help="example bars to print per buy-point type")
    ap.add_argument("--days", type=int, default=60,
                    help="how many scan days to sample across the year")
    a = ap.parse_args()

    import screen_gpt
    from app.db import create_pool
    from . import funnel
    from .buy_points import detect_buy_points

    screen_gpt.load_tick_sizes()
    screen_gpt.DEBUG = False

    pool = await create_pool()
    try:
        days = [r["d"] for r in await pool.fetch(
            "SELECT DISTINCT time::date AS d FROM ohlcv_data "
            "WHERE time::date BETWEEN $1 AND $2 ORDER BY d",
            date(a.year, 1, 1), date(a.year, 12, 31))]
        step = max(1, len(days) // a.days)
        sample_days = days[::step]

        bp_counts = Counter()
        trig_counts = Counter()
        both_counts = Counter()
        n_survivors = n_with_bp = n_with_trigger = n_with_both = 0
        examples: dict[str, list] = {}

        for d in sample_days:
            survivors = await funnel.funnel_survivors(pool, d)
            if not survivors:
                continue
            syms = [r["symbol"] for r in survivors]
            frames = await funnel.load_ohlcv_frames_batch(pool, syms, d)
            for sym in syms:
                df = frames.get(sym)
                if df is None or len(df) < 30:
                    continue
                n_survivors += 1

                bps = detect_buy_points(df, sym)
                trig = screen_gpt.detect_entry_technique(df, symbol=sym)
                ttype = trig.get("type")

                for b in bps:
                    bp_counts[b] += 1
                if ttype:
                    trig_counts[ttype] += 1
                if bps:
                    n_with_bp += 1
                if ttype:
                    n_with_trigger += 1
                if bps and ttype:
                    n_with_both += 1
                    for b in bps:
                        both_counts[f"{b} + {ttype}"] += 1
                        ex = examples.setdefault(b, [])
                        if len(ex) < a.samples:
                            tail = df.tail(4)
                            ex.append({
                                "symbol": sym, "date": str(d), "trigger": ttype,
                                "bars": [
                                    (str(ix.date()), round(float(r.Open), 2),
                                     round(float(r.High), 2), round(float(r.Low), 2),
                                     round(float(r.Close), 2))
                                    for ix, r in tail.iterrows()],
                            })
    finally:
        await pool.close()

    print("=" * 96)
    print(f"BUY-POINT SPOT CHECK — {a.year}, {len(sample_days)} scan days")
    print("=" * 96)
    print(f"funnel survivors examined      : {n_survivors}")
    print(f"  ...with a BUY POINT          : {n_with_bp:>6}  "
          f"({100*n_with_bp/max(n_survivors,1):.1f}%)")
    print(f"  ...with a TRIGGER            : {n_with_trigger:>6}  "
          f"({100*n_with_trigger/max(n_survivors,1):.1f}%)")
    print(f"  ...with BOTH  (entry v2)     : {n_with_both:>6}  "
          f"({100*n_with_both/max(n_survivors,1):.1f}%)")
    print("\nIf 'BOTH' is near zero the design is too strict to trade — better to")
    print("learn that here than after a day of backtests.\n")

    print("buy points found:")
    for k, v in bp_counts.most_common():
        print(f"   {k:<18}{v:>6}")
    print("\ntriggers found (production's first-match):")
    for k, v in trig_counts.most_common():
        print(f"   {k:<18}{v:>6}")
    print("\ntop combinations:")
    for k, v in both_counts.most_common(10):
        print(f"   {k:<40}{v:>6}")

    print("\n" + "=" * 96)
    print("EXAMPLES — check these against a real chart before trusting anything")
    print("=" * 96)
    for bp, exs in examples.items():
        print(f"\n### {bp}")
        for e in exs:
            print(f"  {e['symbol']} on {e['date']}  (trigger: {e['trigger']})")
            print(f"       {'date':<12}{'open':>9}{'high':>9}{'low':>9}{'close':>9}")
            for b in e["bars"]:
                print(f"       {b[0]:<12}{b[1]:>9}{b[2]:>9}{b[3]:>9}{b[4]:>9}")


if __name__ == "__main__":
    asyncio.run(main())
