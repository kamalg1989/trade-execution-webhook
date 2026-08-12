"""Follow-up to portfolio_sweep: walk-forward the stop on its OWN, plus the
middle-ground configs the first matrix implies but did not contain.

Why this is a separate run. The first walk-forward evaluated 10/15/20% while the
full control stack (vol scaling + drawdown throttle + sector caps) was switched
ON. The matrix then showed that stack is a net negative - it costs more CAGR
than the drawdown it buys. So that walk-forward measured the stop inside a
configuration we have since rejected, which makes it the wrong test: the stop
level that is best when exposure is being cut to 49% is not necessarily the one
that is best at full exposure.

This re-runs it with the stop as the ONLY control, which is the configuration
actually under consideration.

It also adds the middle grounds. The matrix jumped from "stop only" (Martin 1.03,
39% maxDD) to "everything on" (Martin 0.63, 28% maxDD) with nothing between, so
it cannot say whether some gentler subset lands better than either end. These
fill that gap - and every one of them is a hypothesis the matrix generated, not
a new parameter search.

Run:  nohup python3 -m backtest.portfolio_wf > /root/portfolio_wf.log 2>&1 &
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

MIDDLE = [
    # Reference points, re-run in-batch so nothing is compared across runs.
    ("R0-nostop", {"sl_pct": 0.0}),
    ("R1-stop15only", {}),
    # Does a gentler exposure ladder (never below 60%) keep the drawdown benefit
    # without the CAGR collapse the 25%-floor ladder caused?
    ("M1-stop15+gentlevol", {"vol_mode": "pct", "vol_levels": (1.0, 0.9, 0.75, 0.6)}),
    # Vol scaling that only ever cuts to 75% - the mildest possible version.
    ("M2-stop15+mildvol", {"vol_mode": "pct", "vol_levels": (1.0, 1.0, 0.85, 0.75)}),
    # Sector caps alone, loosened to 3 per sector: is 2 simply too tight for a
    # 20-name book drawn from a universe where only ~55% have a known sector?
    ("M3-stop15+sector3", {"max_stocks_per_sector": 3, "max_per_sector_pct": 30.0}),
    # A deeper, later drawdown throttle: the -10% trigger fired constantly on a
    # book whose normal drawdown is ~20%, so it was throttled most of the time
    # (avg exposure 0.62). -20% should fire only in genuine trouble.
    ("M4-stop15+ddthrottle20", {"dd_throttle_at": 0.20, "dd_restore_at": 0.10}),
    # More positions instead of less exposure: diversification as the risk
    # control rather than cash. Cheaper in CAGR terms if it works.
    ("M5-stop15+top30", {"top_n": 30, "buffer_n": 60}),
    ("M6-stop15+top30+gentlevol", {"top_n": 30, "buffer_n": 60, "vol_mode": "pct",
                                   "vol_levels": (1.0, 0.9, 0.75, 0.6)}),
]

STOPS = [10.0, 15.0, 20.0]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=400000)
    ap.add_argument("--out", default="/root/portfolio_wf.json")
    a = ap.parse_args()

    from app.db import create_pool
    from .portfolio_engine import run_portfolio

    pool = await create_pool()
    out = {"walkforward_stoponly": [], "middle": []}
    try:
        print("=" * 100)
        print("WALK-FORWARD, STOP AS THE ONLY CONTROL (fit 2016-20 / test 2021-26)")
        print("=" * 100)
        print(f"{'stop':<8}{'FIT CAGR%':>11}{'FIT DD%':>9}{'FIT Mrt':>9}"
              f"{'TEST CAGR%':>12}{'TEST DD%':>10}{'TEST Mrt':>10}{'TEST w12m%':>12}")
        for sl in STOPS:
            row = {"stop": sl}
            for name, (s, e) in (("fit", FIT), ("test", TEST)):
                row[name] = await run_portfolio(pool, capital=a.capital,
                                                start=s, end=e, sl_pct=sl)
            out["walkforward_stoponly"].append(row)
            f, t = row["fit"], row["test"]
            print(f"{sl:<8.0f}{f['cagrPct']:>11.2f}{f['maxDDPct']:>9.1f}{str(f['martin']):>9}"
                  f"{t['cagrPct']:>12.2f}{t['maxDDPct']:>10.1f}{str(t['martin']):>10}"
                  f"{t['worst12mPct']:>12.1f}", flush=True)

        print("\n" + "=" * 100)
        print("MIDDLE-GROUND CONFIGS, FULL PERIOD 2016-2026")
        print("=" * 100)
        print(f"{'config':<30}{'CAGR%':>8}{'maxDD%':>8}{'ulcer':>7}{'worst12m%':>11}"
              f"{'Martin':>8}{'turn/yr':>9}{'trades':>8}{'exp':>7}{'final':>12}")
        for label, over in MIDDLE:
            m = await run_portfolio(pool, capital=a.capital,
                                    start=FULL[0], end=FULL[1], **over)
            m["label"] = label
            out["middle"].append(m)
            print(f"{label:<30}{m['cagrPct']:>8.2f}{m['maxDDPct']:>8.1f}{m['ulcer']:>7.2f}"
                  f"{m['worst12mPct']:>11.1f}{str(m['martin']):>8}{m['turnoverPerYr']:>9.2f}"
                  f"{m['trades']:>8}{m['avgExposure']:>7.2f}{m['final']:>12,}", flush=True)
    finally:
        await pool.close()

    with open(a.out, "w") as fh:
        json.dump(out, fh, default=str)
    print("\nALLDONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
