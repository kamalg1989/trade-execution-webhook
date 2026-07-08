"""
Nightly / backfill compute for the Custom Screener.

Reads ohlcv_data (read-only), computes indicators per symbol (vectorized over the
whole series in one pass), upserts stock_indicators, then aggregates market_snapshot.

Usage:
    python -m compute.compute_stock_indicators                 # incremental: latest bar date
    python -m compute.compute_stock_indicators --date 2026-07-08
    python -m compute.compute_stock_indicators --backfill-years 15
    python -m compute.compute_stock_indicators --from 2011-01-01 --to 2026-07-08
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import math
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from app import config
from compute.indicators import PERSIST_COLUMNS, compute_indicators
from compute.snapshot import compute_snapshot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("compute")


def _clean(v):
    if v is None:
        return None
    # normalize numpy scalar types (bool_, int64, float64) to native Python
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


async def _universe(con) -> list[str]:
    rows = await con.fetch("SELECT DISTINCT symbol FROM ohlcv_data ORDER BY symbol")
    return [r["symbol"] for r in rows]


async def _load_series(con, symbol: str) -> pd.DataFrame:
    rows = await con.fetch(
        "SELECT time, open, high, low, close, volume FROM ohlcv_data "
        "WHERE symbol = $1 ORDER BY time ASC",
        symbol,
    )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df["symbol"] = symbol
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    return df


async def _upsert_indicators(con, df: pd.DataFrame, frm: date, to: date):
    sub = df[(df["indicator_date"] >= frm) & (df["indicator_date"] <= to)]
    if sub.empty:
        return 0
    records = []
    for _, row in sub[PERSIST_COLUMNS].iterrows():
        records.append(tuple(_clean(row[c]) for c in PERSIST_COLUMNS))
    cols = ", ".join(PERSIST_COLUMNS)
    ph = ", ".join(f"${i+1}" for i in range(len(PERSIST_COLUMNS)))
    updates = ", ".join(
        f"{c} = EXCLUDED.{c}" for c in PERSIST_COLUMNS
        if c not in ("symbol", "indicator_date")
    )
    sql = (
        f"INSERT INTO stock_indicators ({cols}, updated_at) VALUES ({ph}, NOW()) "
        f"ON CONFLICT (symbol, indicator_date) DO UPDATE SET {updates}, updated_at = NOW()"
    )
    await con.executemany(sql, records)
    return len(records)


async def _aggregate_snapshot(con, d: date):
    rows = await con.fetch(
        "SELECT dist_sma_50_pct, dist_sma_200_pct, sma_50, sma_200, "
        "dist_52w_high_pct, dist_52w_low_pct, pct_chg_1d, pct_chg_1m, pct_chg_3m, pct_chg_6m, "
        "is_new_52w_high, is_new_52w_low "
        "FROM stock_indicators WHERE indicator_date = $1",
        d,
    )
    dicts = [dict(r) for r in rows]  # persisted flags read directly (exact)
    snap = compute_snapshot(dicts, processed_count=len(dicts),
                            complete_threshold=config.COMPLETE_THRESHOLD)
    cols = list(snap.keys())
    ph = ", ".join(f"${i+2}" for i in range(len(cols)))
    setter = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
    sql = (
        f"INSERT INTO market_snapshot (snapshot_date, {', '.join(cols)}, updated_at) "
        f"VALUES ($1, {ph}, NOW()) "
        f"ON CONFLICT (snapshot_date) DO UPDATE SET {setter}, updated_at = NOW()"
    )
    await con.execute(sql, d, *[_clean(snap[c]) for c in cols])
    return snap


async def run(frm: date, to: date):
    import asyncpg
    pool = await asyncpg.create_pool(
        host=config.DB_HOST, port=config.DB_PORT, user=config.DB_USER,
        password=config.DB_PASSWORD, database=config.DB_NAME, min_size=1, max_size=4,
    )
    processed = 0
    try:
        async with pool.acquire() as con:
            symbols = await _universe(con)
            log.info("Universe: %d symbols. Computing %s..%s", len(symbols), frm, to)
            for i, sym in enumerate(symbols, 1):
                try:
                    df = await _load_series(con, sym)
                    if df.empty:
                        continue
                    ind = compute_indicators(df)
                    n = await _upsert_indicators(con, ind, frm, to)
                    processed += 1
                    if i % 250 == 0:
                        log.info("  %d/%d symbols (last: %s, %d rows)", i, len(symbols), sym, n)
                except Exception as e:
                    log.warning("  %s failed: %s", sym, str(e)[:120])
            # snapshot per date in range
            d = frm
            while d <= to:
                snap = await _aggregate_snapshot(con, d)
                if snap["total_stocks"]:
                    log.info("Snapshot %s: regime=%s trend=%.2f complete=%s (%d stocks)",
                             d, snap["regime"], snap["trend_score"],
                             snap["is_complete"], snap["total_stocks"])
                d += timedelta(days=1)
        log.info("Done. Symbols processed: %d", processed)
    finally:
        await pool.close()


def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--date", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date())
    p.add_argument("--from", dest="frm", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date())
    p.add_argument("--to", dest="to", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date())
    p.add_argument("--backfill-years", type=int)
    return p.parse_args()


def main():
    a = _parse_args()
    today = date.today()
    if a.date:
        frm = to = a.date
    elif a.backfill_years:
        to = today
        frm = today - timedelta(days=365 * a.backfill_years)
    elif a.frm and a.to:
        frm, to = a.frm, a.to
    else:
        frm = to = today  # incremental (compute writes only dates that have bars)
    asyncio.run(run(frm, to))


if __name__ == "__main__":
    main()
