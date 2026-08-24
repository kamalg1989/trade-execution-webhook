"""Does production's SELECTION beat momentum when turnover is held equal?

THE QUESTION. The cost thesis concluded the breakout book failed on TURNOVER,
not on stock picking: avg gross move +0.704%/trade against 0.522% round-trip
cost, so ~74% of the edge went to frictions across 2,914 trades. That was an
inference, never a direct test — production's selection had only ever been run
inside production's high-turnover wrapper, so "bad picks" and "too many trades"
were confounded.

This separates them. Production's own funnel (Stage 1 SQL gates, Stage 2
base/trigger classification, ranked -ifp_score then base_range_pct, exactly as
screen_gpt.rank_candidates orders them) fills a top-20 book rebalanced every 63
sessions, with the same stop and the same costs as the frozen momentum strategy.
Every mechanic is identical; only the ranking differs.

WHAT THIS IS NOT. It is not "production, backtested". Production waits for an
intraday entry TRIGGER and sizes by risk; this buys the ranked names at the next
open in equal weight. What is being tested is whether the funnel's choice of
STOCKS carries information, not whether its entry mechanics do.

A CONSTRAINT WORTH KNOWING BEFORE READING THE RESULTS. The funnel passes only
~10-18 candidates a day in 2016-2020 (rising to ~58 by 2026), so a 20-name book
frequently cannot be filled and runs partly in cash. avgDeployedPct reports how
full it actually was. A half-deployed book earns roughly half the return of a
full one at roughly half the risk, so comparing its CAGR to a fully-invested
momentum book without that column would be meaningless.

The stop interpretation is deliberately not guessed at: fixed 15%, safety at 2x
the structural distance, and both together are all run, so the conclusion does
not depend on which reading was intended.

Run:  nohup python3 -m backtest.selection_test > /root/selection.log 2>&1 &
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date

sys.path.insert(0, "/root/trade-execution-webhook")

FULL = (date(2016, 1, 1), date(2026, 8, 8))
FIT = (date(2016, 1, 1), date(2020, 12, 31))
TEST = (date(2021, 1, 1), date(2026, 8, 8))

BASE = dict(rebalance_days=63, top_n=20, buffer_n=40, min_turnover=5.0)

RUNS = [
    # --- reference: the frozen momentum book, same wrapper
    ("momentum + 15% stop  [FROZEN REFERENCE]",
     dict(selection="momentum", momentum="pct_chg_6m", sl_pct=15.0)),
    ("momentum, no stop",
     dict(selection="momentum", momentum="pct_chg_6m", sl_pct=0.0)),

    # --- production selection, the three stop readings
    ("breakout + 15% stop",
     dict(selection="breakout", sl_pct=15.0)),
    ("breakout + 2x structural safety only",
     dict(selection="breakout", sl_pct=0.0, safety_struct_mult=2.0)),
    ("breakout + 15% AND 2x structural",
     dict(selection="breakout", sl_pct=15.0, safety_struct_mult=2.0)),
    ("breakout, no stop at all",
     dict(selection="breakout", sl_pct=0.0)),

    # --- does the funnel's shortfall explain the result? a narrower book can
    #     actually be filled, so this separates "bad picks" from "too few picks"
    ("breakout + 15% stop, top 10",
     dict(selection="breakout", sl_pct=15.0, top_n=10, buffer_n=20)),
]


def line(label, m):
    return (f"{label:<42}{m['cagrPct']:>8.2f}{m['maxDDPct']:>8.1f}{m['ulcer']:>7.2f}"
            f"{m['worst12mPct']:>10.1f}{str(m['martin']):>8}{m['turnoverPerYr']:>8.2f}"
            f"{m['trades']:>7}{m['avgDeployedPct']:>9.1f}%{m['final']:>12,}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=400000)
    ap.add_argument("--out", default="/root/selection.json")
    a = ap.parse_args()

    from app.db import create_pool
    from .portfolio_engine import load_market_data, simulate

    pool = await create_pool()
    out = []
    try:
        for phase, (s, e) in (("FULL 2016-2026", FULL), ("FIT 2016-2020", FIT),
                              ("TEST 2021-2026", TEST)):
            print("=" * 118)
            print(f"{phase}   — every mechanic identical, only the RANKING differs")
            print("=" * 118)
            print(f"{'config':<42}{'CAGR%':>8}{'maxDD%':>8}{'ulcer':>7}{'w12m%':>10}"
                  f"{'Martin':>8}{'turn':>8}{'trades':>7}{'deployed':>10}{'final':>12}")
            for label, over in RUNS:
                cfg = {**BASE, **over, "capital": a.capital, "start": s, "end": e}
                data = await load_market_data(pool, cfg)
                if not data:
                    continue
                m = simulate(data, cfg)
                m = {k: v for k, v in m.items() if not k.startswith("_")}
                m.update({"phase": phase, "label": label})
                out.append(m)
                print(line(label, m), flush=True)
            print(flush=True)
    finally:
        await pool.close()

    with open(a.out, "w") as fh:
        json.dump(out, fh, default=str)
    print("ALLDONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
