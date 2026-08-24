"""Parallel cache warmer — makes a BRAND-NEW backtest window fast.

Why this exists
---------------
A backtest over an already-tested window finishes in ~1.5 min because every
(symbol, signal_date) Stage 2 result is already cached. A window nobody has
touched takes ~9-13 min, and profiling showed where that goes:

    gate SQL      ~1%
    OHLCV load   ~30%
    Stage 2 compute ~69%   (classify_base_stage + resolve_entry + compute_target)

The Stage 2 compute is genuine CPU work in pandas, and inside a single run it
is driven by a thread pool — which the GIL largely serialises, so it cannot
use both of the VPS's cores. But the work is embarrassingly parallel ACROSS
DAYS: each (symbol, date) result depends only on that symbol's own price
history, never on any other day's result or on the order days are processed.

So instead of speeding up one run's internal loop, this pre-populates the very
same cache tables the engine reads, using several OS processes (real
parallelism, no GIL). Afterwards the actual backtest run is a cache-hit run
and finishes in the usual ~1.5 min.

Correctness
-----------
This calls exactly the same funnel code paths the engine calls, so it writes
exactly the rows the engine would have written itself — it is a pure
"do the work earlier, in parallel" optimisation, not a reimplementation.
Both cache tables upsert with ON CONFLICT DO NOTHING, so overlapping day
ranges between workers are harmless.

Usage
-----
    python3 -m backtest.warm_cache --start 2024-01-01 --end 2024-08-08 \
        [--workers 2] [--base-stage-max 2]

--base-stage-max is the Stage 2 override the run will use (omit for
production defaults). It must match the run's config or the warmed rows will
be under a different config_hash and won't be hit.
"""
from __future__ import annotations

import argparse
import asyncio
import multiprocessing as mp
import os
import sys
import time
from datetime import date, datetime

sys.path.insert(0, "/root/trade-execution-webhook")


def _worker(args) -> tuple[int, int, float]:
    """One OS process: warms its own slice of days. Returns (days, symbols, secs)."""
    days_iso, base_stage_max, capital = args
    import screen_gpt
    from app.db import create_pool
    from backtest import funnel, funnel_stage2

    # Same two backtest-only speedups the engine applies (see engine.py's
    # _prepare_screen_gpt_for_backtest): warm the tick CSV once per process,
    # and silence per-symbol debug printing.
    screen_gpt.load_tick_sizes()
    screen_gpt.DEBUG = False

    run_cfg = {}
    if base_stage_max is not None:
        run_cfg["stage2_base_stage_max_allowed"] = base_stage_max
    stage2_active = funnel_stage2.apply_overrides(run_cfg)
    chash = funnel_stage2.config_hash() if stage2_active else None

    async def go():
        pool = await create_pool()
        n_sym = 0
        try:
            for iso in days_iso:
                d = datetime.strptime(iso, "%Y-%m-%d").date()
                if stage2_active:
                    cands = await funnel_stage2.build_candidates(pool, d, capital, chash)
                else:
                    cands = await funnel.build_candidates(pool, d, capital)
                n_sym += len(cands)
        finally:
            await pool.close()
        return n_sym

    t0 = time.time()
    n = asyncio.run(go())
    return len(days_iso), n, time.time() - t0


async def _trading_days(start: date, end: date) -> list[str]:
    from app.db import create_pool
    pool = await create_pool()
    try:
        rows = await pool.fetch(
            "SELECT DISTINCT time::date AS d FROM ohlcv_data "
            "WHERE time::date BETWEEN $1 AND $2 ORDER BY d", start, end)
        return [r["d"].isoformat() for r in rows]
    finally:
        await pool.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    # The VPS is 2 vCPU; 2 workers saturates it. Going higher just adds
    # context-switching and memory pressure alongside the other services.
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--base-stage-max", type=int, default=None)
    ap.add_argument("--capital", type=float, default=400000)
    a = ap.parse_args()

    start = datetime.strptime(a.start, "%Y-%m-%d").date()
    end = datetime.strptime(a.end, "%Y-%m-%d").date()
    days = asyncio.run(_trading_days(start, end))
    if not days:
        print("no trading days in range")
        return

    # Round-robin the days across workers rather than giving each a contiguous
    # block: symbols/'days' are not uniformly expensive across a window (more
    # survivors in some months than others), so interleaving keeps the workers
    # finishing at roughly the same time.
    slices = [days[i::a.workers] for i in range(a.workers)]
    print(f"warming {len(days)} trading days across {a.workers} processes "
          f"(base_stage_max={a.base_stage_max}) ...", flush=True)

    t0 = time.time()
    with mp.Pool(a.workers) as pool:
        results = pool.map(_worker, [(s, a.base_stage_max, a.capital) for s in slices])
    total = time.time() - t0

    for i, (nd, ns, secs) in enumerate(results):
        print(f"  worker {i}: {nd} days, {ns} candidates, {secs/60:.1f} min")
    print(f"DONE in {total/60:.1f} min for {len(days)} days "
          f"({total/max(len(days),1):.2f}s/day wall)", flush=True)


if __name__ == "__main__":
    main()
