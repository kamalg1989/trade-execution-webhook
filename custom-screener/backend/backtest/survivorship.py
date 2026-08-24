"""Quantify the survivorship bias in this dataset, and bound its effect.

THE FINDING THAT MOTIVATES THIS. Of 3,261 symbols in ohlcv_data, 3,258 have
price history running to the last trading day. Three do not. A real equity
universe loses companies continuously - to compulsory delisting, prolonged
suspension, merger and liquidation - so a universe with essentially zero
attrition is not a universe, it is a list of survivors. The eligible pool also
only ever GROWS (1,012 symbols in 2011 to 3,258 in 2026), which is what a
survivor list looks like: new listings enter and nothing ever leaves.

The consequence is specific, not vague: the backtest CANNOT buy a stock that
later collapses to zero, because such stocks are absent from the table. Every
figure in BACKTEST_REPORT is therefore an upper bound on what was achievable.

WHY THIS IS A BOUND AND NOT A CORRECTION. Fixing it properly needs
point-in-time universe snapshots - the actual list of tradeable NSE symbols on
each historical date, with delisting dates and final prices. That data is not in
the database and is not freely available. What CAN be done rigorously is to
inject the missing losses at a range of plausible rates and observe how fast the
result degrades. If the strategy survives a pessimistic rate, the bias is not
decisive; if it collapses at a realistic one, nothing else in the report matters.

CALIBRATING THE HAZARD RATE. NSE compulsory delistings run at roughly 5-50 per
year against ~1,800-2,700 listed companies, i.e. ~0.3-2.7% a year, and 132 NSE
companies had been suspended over seven years as of 2016. Applied unconditionally
that is an OVERSTATEMENT for this strategy, because delisting candidates are
overwhelmingly illiquid and falling - they would fail the Rs.5cr turnover gate and
the close>SMA200 filter long before the book could buy them. But the channel that
does hit a momentum book is real and is not rare: a stock that runs hard on
manipulation or fraud, passes every liquidity and trend filter, and is then
suspended. Using the unconditional rate deliberately errs pessimistic, which is
the correct direction for a bound.

The grid therefore spans 0% to 4% a year, straddling the empirical range with
room either side, at two recovery assumptions (total loss, and 30% recovered).
Five seeds per cell, because a single seed on a rare event is anecdote - what
matters is the spread across seeds as much as the mean.

Run:  nohup python3 -m backtest.survivorship > /root/survivorship.log 2>&1 &
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics as st
import sys
from datetime import date

sys.path.insert(0, "/root/trade-execution-webhook")

FULL = (date(2016, 1, 1), date(2026, 8, 8))

HAZARDS = [0.0, 0.005, 0.01, 0.02, 0.04]
RECOVERIES = [0.0, 0.30]
SEEDS = [1, 2, 3, 4, 5]

# Held at the configuration under consideration, so the stress is measured on
# the candidate rather than on some other book.
CONFIG = dict(momentum="pct_chg_6m", rebalance_days=63, top_n=35, buffer_n=70,
              sl_pct=15.0, min_turnover=5.0)


async def universe_facts(pool) -> dict:
    """The empirical evidence that the bias exists, printed alongside the
    stress test so the two are never separated."""
    dead = await pool.fetchval(
        "SELECT count(*) FROM (SELECT symbol, max(time)::date lb FROM ohlcv_data "
        "GROUP BY 1) t WHERE lb < (SELECT max(time)::date - 90 FROM ohlcv_data)")
    total = await pool.fetchval("SELECT count(DISTINCT symbol) FROM ohlcv_data")
    pool_by_year = {r["yr"]: r["n"] for r in await pool.fetch(
        "SELECT yr, count(*) n FROM ("
        "  SELECT symbol, min(time)::date fb, max(time)::date lb"
        "  FROM ohlcv_data GROUP BY 1) t, generate_series(2016,2026) yr "
        "WHERE extract(year from fb)<=yr AND extract(year from lb)>=yr "
        "GROUP BY yr ORDER BY yr")}
    return {"total_symbols": total, "dead_symbols": dead,
            "attrition_pct": round(100 * dead / max(total, 1), 2),
            "pool_by_year": pool_by_year}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=400000)
    ap.add_argument("--out", default="/root/survivorship.json")
    a = ap.parse_args()

    from app.db import create_pool
    from .portfolio_engine import run_portfolio

    pool = await create_pool()
    results = []
    try:
        facts = await universe_facts(pool)
        print("=" * 96)
        print("1. DOES THE BIAS EXIST?  (measured, not assumed)")
        print("=" * 96)
        print(f"symbols in ohlcv_data                : {facts['total_symbols']}")
        print(f"whose price series ENDS early (dead) : {facts['dead_symbols']}"
              f"  ({facts['attrition_pct']}%)")
        print("eligible pool by year                : "
              + ", ".join(f"{y}:{n}" for y, n in facts["pool_by_year"].items()))
        print("\nA real universe loses names every year. This one only gains them.")
        print("Every figure in BACKTEST_REPORT is therefore an UPPER BOUND.\n")

        print("=" * 96)
        print("2. HOW FAST DOES THE RESULT DEGRADE?  (mean of 5 seeds; spread in brackets)")
        print("=" * 96)
        print(f"{'hazard/yr':>10}{'recovery':>10}{'CAGR%':>9}{'(min-max)':>16}"
              f"{'maxDD%':>9}{'ulcer':>8}{'Martin':>8}{'blowups':>9}")
        for rec in RECOVERIES:
            for hz in HAZARDS:
                # The zero-hazard row is identical under every recovery
                # assumption (nothing blows up, so nothing is recovered), so it
                # is run once as the reference rather than repeated per column.
                if hz == 0 and rec != RECOVERIES[0]:
                    continue
                runs = []
                for seed in (SEEDS if hz > 0 else [1]):
                    m = await run_portfolio(pool, capital=a.capital,
                                            start=FULL[0], end=FULL[1], **CONFIG,
                                            delist_hazard_pa=hz,
                                            delist_recovery=rec,
                                            delist_seed=seed)
                    runs.append(m)
                cg = [r["cagrPct"] for r in runs]
                row = {"hazard": hz, "recovery": rec,
                       "cagr_mean": round(st.mean(cg), 2),
                       "cagr_min": round(min(cg), 2), "cagr_max": round(max(cg), 2),
                       "maxDD": round(st.mean(r["maxDDPct"] for r in runs), 1),
                       "ulcer": round(st.mean(r["ulcer"] for r in runs), 2),
                       "martin": round(st.mean(r["martin"] or 0 for r in runs), 2),
                       "blowups": round(st.mean(r["delisted"] for r in runs), 1)}
                results.append(row)
                spread = f"({row['cagr_min']:.1f}-{row['cagr_max']:.1f})"
                print(f"{hz*100:>9.1f}%{rec*100:>9.0f}%{row['cagr_mean']:>9.2f}"
                      f"{spread:>16}{row['maxDD']:>9.1f}{row['ulcer']:>8.2f}"
                      f"{row['martin']:>8.2f}{row['blowups']:>9.1f}", flush=True)
            print(flush=True)
    finally:
        await pool.close()

    with open(a.out, "w") as fh:
        json.dump({"facts": facts, "stress": results}, fh, default=str)
    print("ALLDONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
