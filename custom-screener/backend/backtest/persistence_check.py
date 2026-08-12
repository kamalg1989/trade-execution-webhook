"""Do the buy-point detectors fire ONCE per setup, or every day while a
condition stays true?

The distinction matters more than it sounds. A buy point should be an EVENT — the
bar where the setup becomes actionable. A detector whose condition is a STATE
("price is above the neckline") keeps firing for as long as the state holds, so
the same setup is re-signalled day after day while price runs away. In a backtest
that inflates trade counts and quietly turns a breakout system into a chase-the-
move system, and nothing in the P&L looks wrong.

REVERSE_HS is the suspect: its neckline is fixed by pivots inside a 60-bar
window, so once price clears it the condition can stay true for many sessions.
HIGH_BREAKOUT self-corrects because its base high is recomputed from the trailing
20 bars and rises with price. The others are checked too rather than assumed.

Method: walk EVERY consecutive session for a sample of symbols and record runs of
consecutive days on which each detector fires. A healthy detector is dominated by
runs of length 1.

Run:  python3 -m backtest.persistence_check --year 2023 --symbols 60
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter, defaultdict
from datetime import date

sys.path.insert(0, "/root/trade-execution-webhook")

TYPES = ("HIGH_BREAKOUT", "PULLBACK", "BREAKOUT_RETEST", "REVERSE_HS")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2023)
    ap.add_argument("--symbols", type=int, default=60)
    a = ap.parse_args()

    import screen_gpt
    from app.db import create_pool
    from . import funnel
    from .buy_points import detect_buy_points

    screen_gpt.load_tick_sizes()
    screen_gpt.DEBUG = False

    pool = await create_pool()
    try:
        # Symbols that actually pass the funnel, so this measures the population
        # the strategy would really see rather than the whole exchange.
        mid = date(a.year, 6, 15)
        survivors = await funnel.funnel_survivors(pool, mid)
        syms = [r["symbol"] for r in survivors][:a.symbols]
        if not syms:
            print("no survivors on the sample day")
            return

        days = [r["d"] for r in await pool.fetch(
            "SELECT DISTINCT time::date AS d FROM ohlcv_data "
            "WHERE time::date BETWEEN $1 AND $2 ORDER BY d",
            date(a.year, 1, 1), date(a.year, 12, 31))]

        # Fire history per symbol per type, walking every session.
        fires: dict[str, dict[str, list[bool]]] = {
            s: {t: [] for t in TYPES} for s in syms}

        for d in days:
            frames = await funnel.load_ohlcv_frames_batch(pool, syms, d)
            for s in syms:
                df = frames.get(s)
                bps = set(detect_buy_points(df, s)) if df is not None else set()
                for t in TYPES:
                    fires[s][t].append(t in bps)
    finally:
        await pool.close()

    print("=" * 88)
    print(f"BUY-POINT PERSISTENCE — {a.year}, {len(syms)} symbols, {len(days)} sessions")
    print("=" * 88)
    print("A buy point should be an EVENT. Runs of length 1 = fires once per setup.")
    print("Long runs = the detector is reporting a STATE and re-signalling a stale")
    print("setup while price runs away.\n")

    print(f"{'detector':<18}{'fires':>8}{'setups':>9}{'fires/setup':>13}"
          f"{'run=1':>8}{'run 2-3':>9}{'run 4+':>8}{'longest':>9}")
    for t in TYPES:
        runs: Counter = Counter()
        total_fires = 0
        for s in syms:
            run = 0
            for hit in fires[s][t]:
                if hit:
                    run += 1
                    total_fires += 1
                elif run:
                    runs[run] += 1
                    run = 0
            if run:
                runs[run] += 1
        n_setups = sum(runs.values())
        if not n_setups:
            print(f"{t:<18}{0:>8}{0:>9}{'-':>13}{'-':>8}{'-':>9}{'-':>8}{'-':>9}")
            continue
        r1 = runs.get(1, 0)
        r23 = sum(v for k, v in runs.items() if 2 <= k <= 3)
        r4 = sum(v for k, v in runs.items() if k >= 4)
        print(f"{t:<18}{total_fires:>8}{n_setups:>9}"
              f"{total_fires/n_setups:>13.2f}"
              f"{100*r1/n_setups:>7.0f}%{100*r23/n_setups:>8.0f}%"
              f"{100*r4/n_setups:>7.0f}%{max(runs):>9}")

    print("\nfires/setup near 1.0 is healthy. Well above 1.0 means the same setup")
    print("is being re-signalled, which inflates trade counts and turns a breakout")
    print("system into a chase — invisible in a P&L.")


if __name__ == "__main__":
    asyncio.run(main())
