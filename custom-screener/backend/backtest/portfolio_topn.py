"""Top-N plateau test with split-sample validation.

top-30 currently sits in the report as the best risk-adjusted result found
(Martin 1.09). It is ONE post-hoc cell. Every dead idea in this project looked
exactly like that at the same stage - the absolute breadth cap, the VCP gate,
earnings rule M - and each died the moment its neighbours and its out-of-sample
behaviour were checked. So top-30 is a hypothesis until this test says otherwise,
and the report has been reworded to say so.

Two independent ways for it to fail, and it must survive both:

  PLATEAU. If 20/25/30/35/40 trace a smooth curve, the effect is real and the
  exact value barely matters. If 30 is a spike between poorer neighbours, it is
  noise and no value should be adopted.

  TRANSFER. Rank on 2016-2020, then on 2021-2026. The FIT-best config that lands
  mid-table or worse on TEST is the signature this project has been burned by
  before: in the positional rotation sweep the FIT-best config ranked 48th of 48
  on TEST.

Both stops in the supported range are carried through, since 15 vs 20 was not
separable in the earlier walk-forward and collapsing it to one now would be
asserting a distinction the data does not support.

Run:  nohup python3 -m backtest.portfolio_topn > /root/portfolio_topn.log 2>&1 &
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

TOP_NS = [20, 25, 30, 35, 40]
STOPS = [15.0, 20.0]


def _spearman(a: dict, b: dict) -> float:
    common = [k for k in a if k in b]
    if len(common) < 3:
        return 0.0
    ra = {k: i for i, k in enumerate(sorted(common, key=lambda k: -a[k]))}
    rb = {k: i for i, k in enumerate(sorted(common, key=lambda k: -b[k]))}
    n = len(common)
    d2 = sum((ra[k] - rb[k]) ** 2 for k in common)
    return 1 - 6 * d2 / (n * (n * n - 1))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=400000)
    ap.add_argument("--out", default="/root/portfolio_topn.json")
    a = ap.parse_args()

    from app.db import create_pool
    from .portfolio_engine import run_portfolio

    pool = await create_pool()
    rows = []
    try:
        print("=" * 104)
        print("TOP-N PLATEAU, FULL PERIOD 2016-2026  (buffer_n held at 2x top_n)")
        print("=" * 104)
        print(f"{'stop':>6}{'top_n':>7}{'CAGR%':>9}{'maxDD%':>9}{'ulcer':>8}"
              f"{'worst12m%':>12}{'Martin':>9}{'turn/yr':>9}{'trades':>8}{'final':>13}")
        for sl in STOPS:
            for n in TOP_NS:
                m = await run_portfolio(pool, capital=a.capital, start=FULL[0],
                                        end=FULL[1], sl_pct=sl, top_n=n,
                                        buffer_n=n * 2)
                m.update({"stop": sl, "top_n": n, "phase": "full"})
                rows.append(m)
                print(f"{sl:>6.0f}{n:>7}{m['cagrPct']:>9.2f}{m['maxDDPct']:>9.1f}"
                      f"{m['ulcer']:>8.2f}{m['worst12mPct']:>12.1f}"
                      f"{str(m['martin']):>9}{m['turnoverPerYr']:>9.2f}"
                      f"{m['trades']:>8}{m['final']:>13,}", flush=True)
            print(flush=True)

        print("=" * 104)
        print("SPLIT SAMPLE  (fit 2016-2020 / test 2021-2026)")
        print("=" * 104)
        print(f"{'stop':>6}{'top_n':>7}{'FIT CAGR%':>11}{'FIT DD%':>9}{'FIT Mrt':>9}"
              f"{'TEST CAGR%':>12}{'TEST DD%':>10}{'TEST Mrt':>10}{'TEST w12m%':>12}")
        fit_m, test_m = {}, {}
        for sl in STOPS:
            for n in TOP_NS:
                out = {}
                for name, (s, e) in (("fit", FIT), ("test", TEST)):
                    out[name] = await run_portfolio(pool, capital=a.capital,
                                                    start=s, end=e, sl_pct=sl,
                                                    top_n=n, buffer_n=n * 2)
                key = f"stop{sl:.0f}/top{n}"
                fit_m[key] = out["fit"]["martin"] or 0
                test_m[key] = out["test"]["martin"] or 0
                rows.append({"stop": sl, "top_n": n, "phase": "split",
                             "fit": out["fit"], "test": out["test"]})
                f, t = out["fit"], out["test"]
                print(f"{sl:>6.0f}{n:>7}{f['cagrPct']:>11.2f}{f['maxDDPct']:>9.1f}"
                      f"{str(f['martin']):>9}{t['cagrPct']:>12.2f}"
                      f"{t['maxDDPct']:>10.1f}{str(t['martin']):>10}"
                      f"{t['worst12mPct']:>12.1f}", flush=True)

        rho = _spearman(fit_m, test_m)
        print(f"\nSpearman rank correlation (Martin), FIT vs TEST: {rho:+.2f}")
        print("   > +0.5  the top-N ranking transfers - a plateau is meaningful")
        print("   ~  0    ranking is noise - adopt no particular top-N")
        print("   < -0.3  inverted - the FIT-best value is a trap")
        best_fit = max(fit_m, key=lambda k: fit_m[k])
        order = sorted(test_m, key=lambda k: -test_m[k])
        print(f"\nBest on FIT: {best_fit}  ->  its TEST rank: "
              f"{order.index(best_fit)+1}/{len(order)}")
        print(f"Best on TEST: {order[0]}")
    finally:
        await pool.close()

    with open(a.out, "w") as fh:
        json.dump(rows, fh, default=str)
    print("\nALLDONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
