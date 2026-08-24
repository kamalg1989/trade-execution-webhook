"""FINAL bounded diversification test: top 30/35/40/45/50 x stops 15/20.

This is the last parameter test. Research is frozen after it.

THE DECISION RULE IS FIXED IN ADVANCE, which is the only thing that makes the
result meaningful. The previous round picked top-30 because it had the highest
Martin ratio, and it then failed split-sample validation - ranking 6th of 10 out
of sample. Selecting the maximum of a noisy surface is how every dead idea in
this project was born.

So, decided before seeing any output:

  ADOPT a top-N only if a STABLE PLATEAU appears - a contiguous run of values
  that are all good on TEST, in BOTH stop columns, with neighbours within a
  narrow band. Pick the MIDDLE of that plateau, never its peak.

  If no plateau appears - if TEST Martin bounces around, or the two stop columns
  disagree about where the good region is - KEEP TOP-20. Not top-35, not
  whichever cell happens to score best. Top-20 is the incumbent and the default
  on no evidence, because "pick the best-looking cell" is precisely the
  behaviour that has already been falsified here.

The previous round found out-of-sample risk metrics still improving at the edge
of the tested range (top-40), which is why this round extends to 50 - the
question is whether that improvement continues, flattens into a plateau, or
reverses. All three answers are informative and only one of them adopts a value.

Run:  nohup python3 -m backtest.portfolio_topn2 > /root/topn2.log 2>&1 &
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

TOP_NS = [20, 30, 35, 40, 45, 50]     # 20 kept as the incumbent reference
STOPS = [15.0, 20.0]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=400000)
    ap.add_argument("--out", default="/root/topn2.json")
    a = ap.parse_args()

    from app.db import create_pool
    from .portfolio_engine import run_portfolio

    pool = await create_pool()
    rows = []
    try:
        for phase, (s, e) in (("FULL", FULL), ("FIT", FIT), ("TEST", TEST)):
            print("=" * 96)
            print(f"{phase}  {s} .. {e}")
            print("=" * 96)
            print(f"{'stop':>6}{'top_n':>7}{'CAGR%':>9}{'maxDD%':>9}{'ulcer':>8}"
                  f"{'worst12m%':>12}{'Martin':>9}{'turn/yr':>9}{'trades':>8}")
            for sl in STOPS:
                for n in TOP_NS:
                    m = await run_portfolio(pool, capital=a.capital, start=s, end=e,
                                            sl_pct=sl, top_n=n, buffer_n=n * 2)
                    m.update({"phase": phase, "stop": sl, "top_n": n})
                    rows.append({k: v for k, v in m.items()
                                 if not k.startswith("_")})
                    print(f"{sl:>6.0f}{n:>7}{m['cagrPct']:>9.2f}{m['maxDDPct']:>9.1f}"
                          f"{m['ulcer']:>8.2f}{m['worst12mPct']:>12.1f}"
                          f"{str(m['martin']):>9}{m['turnoverPerYr']:>9.2f}"
                          f"{m['trades']:>8}", flush=True)
                print(flush=True)

        # ---- apply the pre-registered rule, mechanically
        print("=" * 96)
        print("PLATEAU CHECK  (rule fixed before the run: middle of a plateau, or keep top-20)")
        print("=" * 96)
        test = {(r["stop"], r["top_n"]): r for r in rows if r["phase"] == "TEST"}
        for sl in STOPS:
            series = [(n, test[(sl, n)]["martin"] or 0) for n in TOP_NS]
            print(f"  stop {sl:.0f}%  TEST Martin by top-N: "
                  + "  ".join(f"{n}:{v:.2f}" for n, v in series))
        # A plateau needs the two stop columns to agree on where the good region
        # is. Rank each column, then look for top-N values that are in the upper
        # half of BOTH.
        good = []
        for sl in STOPS:
            ranked = sorted(TOP_NS, key=lambda n: -(test[(sl, n)]["martin"] or 0))
            good.append(set(ranked[:len(TOP_NS) // 2]))
        agree = sorted(good[0] & good[1])
        print(f"\n  top-N in the upper half of BOTH stop columns: {agree}")
        contiguous = (len(agree) >= 3
                      and TOP_NS.index(agree[-1]) - TOP_NS.index(agree[0]) == len(agree) - 1)
        if contiguous:
            mid = agree[len(agree) // 2]
            print(f"  -> CONTIGUOUS PLATEAU {agree}. Adopt its MIDDLE: top-{mid}")
        else:
            print("  -> NO contiguous plateau across both stops.")
            print("  -> RULE SAYS: keep top-20. Do not pick the best-looking cell.")
    finally:
        await pool.close()

    with open(a.out, "w") as fh:
        json.dump(rows, fh, default=str)
    print("\nALLDONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
