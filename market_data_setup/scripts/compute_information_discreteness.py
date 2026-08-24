#!/usr/bin/env python3
"""Compute information discreteness (frog-in-the-pan) into stock_information_discreteness.

ID = sign(cumulative return over N sessions) x (%negative days - %positive days)
  (Da, Gurun & Warachka 2014). A CONTINUOUS path (many small moves) gives a low
  or negative ID and predicts stronger momentum continuation; a few big jumps
  gives a high ID.

The table was populated once during the H4 research and never refreshed - it is
not part of the nightly compute job, so it goes stale and any live consumer of
pos_id_score_w silently gets NULLs (neutralised at the cross-sectional mean,
i.e. the factor does nothing). This script makes it maintainable.

Usage:
  python compute_information_discreteness.py                # incremental, last 30 sessions
  python compute_information_discreteness.py --since 2026-08-19
  python compute_information_discreteness.py --verify 2026-08-18
"""
import argparse, asyncio, os, sys
from datetime import date as _date
from pathlib import Path
import asyncpg
from dotenv import load_dotenv

for env_file in (Path('/root/trade-execution-webhook/.env'), Path.home()/'.env'):
    if env_file.exists():
        load_dotenv(env_file); break

DSN = os.getenv("MARKET_DSN", "postgresql://postgres:postgres@localhost:5432/market_data")

# One SQL pass: per-symbol daily returns, then rolling windows.
# LOOKBACK_SRC keeps the scan bounded - 252-session window needs ~400 calendar days.
SQL = """
WITH src AS (
    SELECT symbol, time::date AS d, close
    FROM ohlcv_data
    WHERE close > 0 AND time::date > $1::date - INTERVAL '600 days'
), r AS (
    SELECT symbol, d, close,
           close / NULLIF(LAG(close) OVER w, 0) - 1 AS ret
    FROM src
    WINDOW w AS (PARTITION BY symbol ORDER BY d)
), agg AS (
    SELECT symbol, d,
           close / NULLIF(LAG(close, 126) OVER w, 0) - 1 AS cum126,
           close / NULLIF(LAG(close, 252) OVER w, 0) - 1 AS cum252,
           AVG(CASE WHEN ret > 0 THEN 1.0 ELSE 0 END)
               OVER (PARTITION BY symbol ORDER BY d ROWS BETWEEN 125 PRECEDING AND CURRENT ROW) AS p126,
           AVG(CASE WHEN ret < 0 THEN 1.0 ELSE 0 END)
               OVER (PARTITION BY symbol ORDER BY d ROWS BETWEEN 125 PRECEDING AND CURRENT ROW) AS n126,
           AVG(CASE WHEN ret > 0 THEN 1.0 ELSE 0 END)
               OVER (PARTITION BY symbol ORDER BY d ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS p252,
           AVG(CASE WHEN ret < 0 THEN 1.0 ELSE 0 END)
               OVER (PARTITION BY symbol ORDER BY d ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS n252,
           COUNT(*) OVER (PARTITION BY symbol ORDER BY d ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS nobs
    FROM r
    WINDOW w AS (PARTITION BY symbol ORDER BY d)
)
SELECT symbol, d,
       CASE WHEN cum126 IS NULL THEN NULL
            ELSE sign(cum126) * (n126 - p126) END AS id_126,
       CASE WHEN cum252 IS NULL OR nobs < 252 THEN NULL
            ELSE sign(cum252) * (n252 - p252) END AS id_252
FROM agg
WHERE d >= $1::date
"""


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="compute from this date (default: 30 sessions back)")
    ap.add_argument("--verify", help="recompute this date and compare to stored values")
    a = ap.parse_args()

    pool = await asyncpg.create_pool(dsn=DSN, min_size=1, max_size=2)
    if a.verify:
        vd = _date.fromisoformat(a.verify)
        rows = await pool.fetch(SQL, vd)
        rows = [r for r in rows if str(r["d"]) == a.verify and r["id_126"] is not None]
        stored = {r["symbol"]: float(r["id_126"]) for r in await pool.fetch(
            "SELECT symbol, id_126 FROM stock_information_discreteness "
            "WHERE indicator_date=$1 AND id_126 IS NOT NULL", vd)}
        both = [(float(r["id_126"]), stored[r["symbol"]]) for r in rows if r["symbol"] in stored]
        if not both:
            print("VERIFY: no overlap"); await pool.close(); return
        n = len(both)
        mx = max(abs(x - y) for x, y in both)
        mean_abs = sum(abs(x - y) for x, y in both) / n
        exact = sum(1 for x, y in both if abs(x - y) < 1e-6)
        print(f"VERIFY {a.verify}: {n} symbols overlap · exact(<1e-6) {exact}/{n} "
              f"· mean|diff| {mean_abs:.6f} · max|diff| {mx:.6f}")
        await pool.close(); return

    since = a.since
    if not since:
        since = str(await pool.fetchval(
            "SELECT min(d) FROM (SELECT DISTINCT time::date d FROM ohlcv_data "
            "ORDER BY d DESC LIMIT 30) t"))
    print(f"computing information discreteness from {since} ...", flush=True)
    rows = await pool.fetch(SQL, _date.fromisoformat(str(since)))
    payload = [(r["symbol"], r["d"],
                float(r["id_126"]) if r["id_126"] is not None else None,
                float(r["id_252"]) if r["id_252"] is not None else None)
               for r in rows if r["id_126"] is not None or r["id_252"] is not None]
    print(f"  {len(payload)} rows to upsert", flush=True)
    await pool.executemany(
        "INSERT INTO stock_information_discreteness (symbol, indicator_date, id_126, id_252) "
        "VALUES ($1,$2,$3,$4) ON CONFLICT (symbol, indicator_date) "
        "DO UPDATE SET id_126=EXCLUDED.id_126, id_252=EXCLUDED.id_252",
        payload)
    mx = await pool.fetchval("SELECT max(indicator_date) FROM stock_information_discreteness")
    print(f"done · table now current through {mx}", flush=True)
    await pool.close()

asyncio.run(main())
