"""Test matrix for the continuous portfolio, plus walk-forward validation.

Structured to answer three questions in order, and to stop at the first one that
fails, because a control that does not survive walk-forward should not be carried
into a combination test:

  A. BASELINE vs CONTROLS, one control at a time. Each control is expected to
     cost CAGR and buy drawdown. The question for each is whether the trade is
     worth taking - measured by the Martin ratio (CAGR / ulcer index), which
     unlike CAGR/maxDD accounts for how LONG the book spends underwater, not
     only how deep the single worst hole was.

  B. THE RECOMMENDED STACK, all controls together, versus baseline.

  C. WALK-FORWARD on the stop only (10/15/20). Fit on 2016-2020, evaluate on
     2021-2026, and check the ordering holds. This is deliberately the ONLY
     parameter given a sweep: the supported finding is "a moderate fixed stop
     helps", not "15% is optimal" - 15% ranked 1st in-sample and 12th of 26
     out-of-sample, which is exactly the pattern of a fitted parameter.

Every run is one continuous 2016->2026 simulation with capital carried forward,
so the numbers here are NOT comparable to the summed-annual-P&L figures in
BACKTEST_REPORT.md. That incomparability is the point of the exercise.

Run:  nohup python3 -m backtest.portfolio_sweep > /root/portfolio.log 2>&1 &
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

# --- A: one control at a time, on top of an otherwise unconstrained book ------
CONTROLS = [
    ("00-baseline-nostop", {"sl_pct": 0.0}),
    ("01-stop15", {}),
    ("02-stop15+volpct", {"vol_mode": "pct"}),
    ("03-stop15+volabs", {"vol_mode": "abs"}),
    ("04-stop15+ddthrottle10", {"dd_throttle_at": 0.10}),
    ("05-stop15+ddthrottle12", {"dd_throttle_at": 0.12}),
    ("05b-stop15+perstock5", {"max_per_stock_pct": 5.0}),
    ("06-stop15+sectorcaps", {"max_stocks_per_sector": 2, "max_per_sector_pct": 25.0}),
    ("07-stop15+sectorcaps-strict", {"max_stocks_per_sector": 2,
                                     "max_per_sector_pct": 25.0,
                                     "require_sector": True}),
    # --- B: the recommended stack, and two ablations of it
    ("10-STACK", {"vol_mode": "pct", "dd_throttle_at": 0.10,
                  "max_stocks_per_sector": 2, "max_per_sector_pct": 25.0}),
    ("11-STACK-noDD", {"vol_mode": "pct",
                       "max_stocks_per_sector": 2, "max_per_sector_pct": 25.0}),
    ("12-STACK-noVol", {"dd_throttle_at": 0.10,
                        "max_stocks_per_sector": 2, "max_per_sector_pct": 25.0}),
    ("13-STACK-strictsector", {"vol_mode": "pct", "dd_throttle_at": 0.10,
                               "max_stocks_per_sector": 2, "max_per_sector_pct": 25.0,
                               "require_sector": True}),
    # A gentler exposure ladder: is the benefit from cutting exposure at all, or
    # from cutting it as hard as 25%? If the gentle ladder captures most of it,
    # the aggressive one is fitting noise.
    ("14-STACK-gentlevol", {"vol_mode": "pct", "vol_levels": (1.0, 0.9, 0.75, 0.6),
                            "dd_throttle_at": 0.10,
                            "max_stocks_per_sector": 2, "max_per_sector_pct": 25.0}),
]

STOPS = [10.0, 15.0, 20.0]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=400000)
    ap.add_argument("--out", default="/root/portfolio.json")
    a = ap.parse_args()

    from app.db import create_pool
    from .portfolio_engine import run_portfolio

    pool = await create_pool()
    results = {"controls": [], "walkforward": []}
    try:
        print("=" * 108)
        print("A/B  CONTINUOUS PORTFOLIO 2016-2026  (capital carried forward, daily MTM)")
        print("=" * 108)
        hdr = (f"{'config':<28}{'CAGR%':>8}{'maxDD%':>8}{'ulcer':>7}{'worst12m%':>11}"
               f"{'Martin':>8}{'turn/yr':>9}{'trades':>8}{'exp':>7}{'final':>12}")
        print(hdr)
        for label, over in CONTROLS:
            m = await run_portfolio(pool, capital=a.capital,
                                    start=FULL[0], end=FULL[1], **over)
            m["label"] = label
            results["controls"].append(m)
            print(f"{label:<28}{m['cagrPct']:>8.2f}{m['maxDDPct']:>8.1f}{m['ulcer']:>7.2f}"
                  f"{m['worst12mPct']:>11.1f}{str(m['martin']):>8}{m['turnoverPerYr']:>9.2f}"
                  f"{m['trades']:>8}{m['avgExposure']:>7.2f}{m['final']:>12,}", flush=True)

        print("\nCalendar-year returns (%), continuous equity curve:")
        yrs = sorted(results["controls"][0]["calendar"])
        print(f"{'config':<28}" + "".join(f"{y:>8}" for y in yrs))
        for m in results["controls"]:
            print(f"{m['label']:<28}"
                  + "".join(f"{m['calendar'].get(y, 0):>8.1f}" for y in yrs), flush=True)

        print("\n" + "=" * 108)
        print("C  WALK-FORWARD ON THE STOP ONLY  (fit 2016-20, evaluate 2021-26)")
        print("=" * 108)
        print(f"{'stop':<10}{'FIT CAGR%':>11}{'FIT DD%':>9}{'FIT Mrt':>9}"
              f"{'TEST CAGR%':>12}{'TEST DD%':>10}{'TEST Mrt':>10}")
        for sl in STOPS:
            row = {"stop": sl}
            for name, (s, e) in (("fit", FIT), ("test", TEST)):
                m = await run_portfolio(pool, capital=a.capital, start=s, end=e,
                                        sl_pct=sl, vol_mode="pct",
                                        dd_throttle_at=0.10,
                                        max_stocks_per_sector=2,
                                        max_per_sector_pct=25.0)
                row[name] = m
            results["walkforward"].append(row)
            f, t = row["fit"], row["test"]
            print(f"{sl:<10.0f}{f['cagrPct']:>11.2f}{f['maxDDPct']:>9.1f}{str(f['martin']):>9}"
                  f"{t['cagrPct']:>12.2f}{t['maxDDPct']:>10.1f}{str(t['martin']):>10}",
                  flush=True)
    finally:
        await pool.close()

    with open(a.out, "w") as fh:
        json.dump(results, fh, default=str)
    print(f"\nWROTE {a.out}", flush=True)
    print("ALLDONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
