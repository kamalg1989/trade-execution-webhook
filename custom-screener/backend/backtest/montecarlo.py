"""Monte Carlo survivorship stress: many paths per cell, reported by percentile.

WHAT THIS IS, AND WHAT IT IS NOT. It is a deliberately simplified random-loss
stress test. It is NOT a correction for survivorship bias and its output must not
be quoted as one. Two reasons, both structural:

  * Losses are injected as an INDEPENDENT uniform hazard on names already held.
    Real delistings and suspensions cluster - in time (the 2018 NBFC crisis, the
    2019 promoter-pledge unwinds) and by sector. Clustered losses hit a
    concentrated book far harder than independent ones, so the drawdowns here
    are optimistic even at a given hazard rate.
  * It cannot touch UNIVERSE COMPOSITION. The ~550 companies missing from the
    2016 pool did not merely fail to be bought; their absence changed which
    stocks ranked top-N in the first place. No amount of injecting losses into
    the stocks that WERE bought recovers that.

So the correct reading is "under this stress, CAGR falls by roughly X" - never
"survivorship costs X". Actual live results could be materially worse.

WHY THE EVENT IS APPLIED BEFORE THE STOP CHECK. A fraud halt or a suspension
gaps straight through a stop; there is no session at which the position could
have been exited at -15% first. An earlier version ran the stop first, which let
the book escape most blow-ups at a controlled loss and understated the harm -
defeating the point of a stress test. The engine now applies the shock first.

WHY PERCENTILES, NOT MEANS. On a rare, heavy-tailed event the mean is close to
useless: it hides the paths that matter. What a position-sizing decision needs is
the bad case, so this reports the MEDIAN and the 5th PERCENTILE of CAGR.

WHY THE SAMPLED WORST IS NOT A SIZING INPUT. max() over N paths is an ORDER
STATISTIC: it rises mechanically as N grows, because more sampling finds more
extreme draws. Quoting "worst drawdown across 1,000 paths" as a planning figure
therefore says as much about the path count as about the strategy, and doubling
N would "discover" a worse number without anything having changed. So maxDD is
reported at p50/p90/p95/p99 as well - p95 is the practical stress-sizing
reference, and the sampled worst is retained only as an illustrative disaster
scenario, explicitly labelled as such.

Raw per-path values are written to disk alongside the summary so any other
percentile can be recovered later without re-running.

Run:  nohup python3 -m backtest.montecarlo --paths 500 > /root/mc.log 2>&1 &
"""
from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing as mp
import sys
from datetime import date

sys.path.insert(0, "/root/trade-execution-webhook")

FULL = (date(2016, 1, 1), date(2026, 8, 8))
HAZARDS = [0.0, 0.005, 0.01, 0.02, 0.04]
RECOVERIES = [0.0, 0.30]

# Held at the frozen baseline. The point is to stress the CANDIDATE, not to
# re-open the parameter search that has just been closed.
CONFIG = dict(momentum="pct_chg_6m", rebalance_days=63, top_n=20, buffer_n=40,
              sl_pct=15.0, min_turnover=5.0)

_DATA = None      # populated per worker process, once


def _init(payload):
    """Each worker deserialises the market data ONCE, then runs thousands of
    paths against it with no further I/O. This is the whole reason the engine
    was split into load_market_data() + simulate()."""
    global _DATA
    _DATA = payload


def _one(args) -> dict:
    hz, rec, seed = args
    from .portfolio_engine import simulate
    m = simulate(_DATA, {**CONFIG, "start": FULL[0], "end": FULL[1],
                         "delist_hazard_pa": hz, "delist_recovery": rec,
                         "delist_seed": seed})
    return {"cagr": m["cagrPct"], "maxDD": m["maxDDPct"], "ulcer": m["ulcer"],
            "worst12m": m["worst12mPct"], "blowups": m["delisted"]}


def pct(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    k = max(0, min(len(sorted_vals) - 1, int(round(p / 100 * (len(sorted_vals) - 1)))))
    return sorted_vals[k]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--paths", type=int, default=500)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--capital", type=float, default=400000)
    ap.add_argument("--out", default="/root/mc.json")
    a = ap.parse_args()

    from app.db import create_pool
    from .portfolio_engine import load_market_data

    pool = await create_pool()
    try:
        data = await load_market_data(pool, {**CONFIG, "start": FULL[0],
                                             "end": FULL[1]})
    finally:
        await pool.close()
    print(f"loaded {len(data['days'])} sessions, "
          f"{len(data['sym_ix'])} candidate symbols, "
          f"{len(data['ranks'])} rebalances", flush=True)

    jobs = []
    for rec in RECOVERIES:
        for hz in HAZARDS:
            if hz == 0 and rec != RECOVERIES[0]:
                continue
            n = 1 if hz == 0 else a.paths     # zero hazard is deterministic
            jobs += [(hz, rec, s) for s in range(n)]
    print(f"{len(jobs)} paths across {a.workers} workers", flush=True)

    out = {}
    with mp.Pool(a.workers, initializer=_init, initargs=(data,)) as p:
        for job, res in zip(jobs, p.imap(_one, jobs, chunksize=8)):
            out.setdefault((job[0], job[1]), []).append(res)

    print("\n" + "=" * 104)
    print(f"SURVIVORSHIP STRESS — {a.paths} paths per cell, reported by PERCENTILE")
    print("A simplified random-loss stress test. NOT a survivorship correction.")
    print("=" * 104)
    print(f"{'hazard':>8}{'recov':>7}{'CAGRp50':>9}{'CAGRp5':>8}"
          f"{'DDp50':>8}{'DDp90':>8}{'DDp95':>8}{'DDp99':>8}"
          f"{'DD(worst)':>11}{'w12m p5':>9}{'blowups':>9}")
    rows, raw = [], {}
    for (hz, rec), rs in out.items():
        cg = sorted(r["cagr"] for r in rs)
        dd = sorted(r["maxDD"] for r in rs)
        w12 = sorted(r["worst12m"] for r in rs)
        raw[f"{hz}_{rec}"] = {"cagr": cg, "maxDD": dd, "worst12m": w12}
        row = {"hazard": hz, "recovery": rec, "n": len(rs),
               "cagr_p50": pct(cg, 50), "cagr_p5": pct(cg, 5),
               "cagr_p95": pct(cg, 95),
               "maxdd_p50": pct(dd, 50), "maxdd_p90": pct(dd, 90),
               "maxdd_p95": pct(dd, 95), "maxdd_p99": pct(dd, 99),
               # Kept for illustration ONLY. It is an order statistic and grows
               # with path count; it is not a planning figure.
               "maxdd_sampled_worst": dd[-1],
               "w12m_p5": w12[0] if len(w12) < 20 else pct(w12, 5),
               "blowups": round(sum(r["blowups"] for r in rs) / len(rs), 1)}
        rows.append(row)
        print(f"{hz*100:>7.1f}%{rec*100:>6.0f}%{row['cagr_p50']:>9.2f}"
              f"{row['cagr_p5']:>8.2f}{row['maxdd_p50']:>8.1f}"
              f"{row['maxdd_p90']:>8.1f}{row['maxdd_p95']:>8.1f}"
              f"{row['maxdd_p99']:>8.1f}{row['maxdd_sampled_worst']:>11.1f}"
              f"{row['w12m_p5']:>9.1f}{row['blowups']:>9.1f}", flush=True)

    print("\np95 maxDD is the stress-sizing reference. The sampled worst is an")
    print("order statistic - it grows with path count and is illustrative only.")

    with open(a.out, "w") as fh:
        json.dump(rows, fh, default=str)
    with open(a.out.replace(".json", "_raw.json"), "w") as fh:
        json.dump(raw, fh)
    print("\nALLDONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
