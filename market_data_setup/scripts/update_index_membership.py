"""
Refresh index_membership + symbols_meta.sector/mcap_bucket from niftyindices.com.

Single feed provides: universe filter (index membership), sector (Industry
column), and market-cap bucket (Nifty 100 = large, Midcap 150 = mid,
Smallcap 250 = small, Microcap 250 = micro — index-derived, SEBI-aligned).

Cron (weekly, after symbols_meta refresh):
  10 10 * * 0 cd /root/trade-execution-webhook && ./venv/bin/python market_data_setup/scripts/update_index_membership.py >> market_data_setup/scripts/meta.log 2>&1
"""
import asyncio
import csv
import io
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import asyncpg
import urllib.request
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

BASE = "https://niftyindices.com/IndexConstituent"
INDICES = {
    "NIFTY50": "ind_nifty50list.csv",
    "NIFTY100": "ind_nifty100list.csv",
    "NIFTY200": "ind_nifty200list.csv",
    "NIFTY500": "ind_nifty500list.csv",
    "MIDCAP150": "ind_niftymidcap150list.csv",
    "SMALLCAP250": "ind_niftysmallcap250list.csv",
    "MICROCAP250": "ind_niftymicrocap250list.csv",
}
BUCKET_BY_INDEX = {"NIFTY100": "large", "MIDCAP150": "mid",
                   "SMALLCAP250": "small", "MICROCAP250": "micro"}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def fetch_csv(filename: str) -> list[dict]:
    req = urllib.request.Request(f"{BASE}/{filename}", headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


async def main():
    load_dotenv("/root/trade-execution-webhook/.env")
    pool = await asyncpg.create_pool(
        host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORT", "5432")),
        user=os.getenv("DB_USER", "market_data_user"), password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME", "market_data"), min_size=1, max_size=2,
    )

    membership: list[tuple] = []
    sector_by_symbol: dict[str, str] = {}
    bucket_by_symbol: dict[str, str] = {}

    for index_name, filename in INDICES.items():
        try:
            rows = fetch_csv(filename)
        except Exception as e:
            logger.error("❌ %s fetch failed: %s (keeping previous membership)", index_name, e)
            continue
        n = 0
        for r in rows:
            sym = (r.get("Symbol") or "").strip().upper()
            if not sym:
                continue
            membership.append((sym, index_name))
            n += 1
            ind = (r.get("Industry") or "").strip()
            if ind:
                sector_by_symbol.setdefault(sym, ind)
            b = BUCKET_BY_INDEX.get(index_name)
            if b:
                bucket_by_symbol[sym] = b
        logger.info("%s: %d constituents", index_name, n)

    if not membership:
        raise SystemExit("No index data fetched — aborting without changes")

    fetched_indices = {ix for _, ix in membership}
    async with pool.acquire() as con:
        async with con.transaction():
            # replace membership only for indices we successfully fetched
            await con.execute("DELETE FROM index_membership WHERE index_name = ANY($1)",
                              list(fetched_indices))
            await con.executemany(
                "INSERT INTO index_membership (symbol, index_name) VALUES ($1, $2) "
                "ON CONFLICT DO NOTHING", membership)
            await con.executemany(
                "UPDATE symbols_meta SET sector = $2, last_updated = NOW() WHERE symbol = $1",
                list(sector_by_symbol.items()))
            await con.executemany(
                "UPDATE symbols_meta SET mcap_bucket = $2, last_updated = NOW() WHERE symbol = $1",
                list(bucket_by_symbol.items()))
        total = await con.fetchval("SELECT count(*) FROM index_membership")
        buckets = await con.fetch(
            "SELECT mcap_bucket, count(*) FROM symbols_meta WHERE mcap_bucket IS NOT NULL GROUP BY 1")
    logger.info("✅ membership rows: %d | buckets: %s | sectors set: %d",
                total, {r["mcap_bucket"]: r["count"] for r in buckets}, len(sector_by_symbol))
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
