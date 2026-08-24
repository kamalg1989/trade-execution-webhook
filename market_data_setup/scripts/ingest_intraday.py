#!/usr/bin/env python3
"""
Intraday OHLCV Ingestion (5m + 15m) for NIFTY 500, last 2 years.
Dhan intraday charts API: max 90 days per call, rate limit ~1 req/sec.

Run:
    nohup python3 market_data_setup/scripts/ingest_intraday.py > /root/ingest_intraday.log 2>&1 &
"""
import asyncio
import asyncpg
import os
import sys
import time
import logging
from datetime import datetime, timedelta, date, timezone
from pathlib import Path
from dotenv import load_dotenv
import requests

sys.path.insert(0, "/root/trade-execution-webhook")

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler('/root/ingest_intraday.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

load_dotenv("/root/trade-execution-webhook/.env")

DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = int(os.getenv('DB_PORT', 5432))
DB_USER = os.getenv('DB_USER', 'market_data_user')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'secure_market_data_pass_2026')
DB_NAME = os.getenv('DB_NAME', 'market_data')

DHAN_API = "https://api.dhan.co/v2/charts/intraday"
TIMEFRAMES = [("5", "5m"), ("15", "15m")]
CHUNK_DAYS = 85  # under the 90-day API limit
YEARS_BACK = 2
RATE_LIMIT_SLEEP = 1.1  # seconds between Dhan calls


def date_chunks(start: date, end: date, chunk_days: int):
    cur = start
    while cur < end:
        chunk_end = min(cur + timedelta(days=chunk_days), end)
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)


async def get_symbols(pool):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT symbol FROM index_membership WHERE index_name = 'NIFTY500' ORDER BY symbol"
        )
    return [r["symbol"] for r in rows]


async def already_done(pool, symbol, timeframe):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM intraday_ingestion_log WHERE symbol=$1 AND timeframe=$2 AND status='completed' LIMIT 1",
            symbol, timeframe
        )
    return row is not None


def fetch_chunk(token, security_id, interval, from_date, to_date):
    headers = {"access-token": token, "Content-Type": "application/json"}
    payload = {
        "securityId": str(security_id),
        "exchangeSegment": "NSE_EQ",
        "instrument": "EQUITY",
        "interval": interval,
        "fromDate": from_date.isoformat(),
        "toDate": to_date.isoformat(),
    }
    r = requests.post(DHAN_API, json=payload, headers=headers, timeout=25)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:200]}"
    d = r.json()
    if "open" not in d or not d["open"]:
        return [], None
    n = len(d["open"])
    rows = []
    for i in range(n):
        ts = d["timestamp"][i]
        rows.append((
            datetime.fromtimestamp(ts, tz=timezone.utc),
            float(d["open"][i]), float(d["high"][i]), float(d["low"][i]),
            float(d["close"][i]), int(d["volume"][i])
        ))
    return rows, None


async def main():
    pool = await asyncpg.create_pool(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD,
        database=DB_NAME, min_size=1, max_size=3
    )

    from web_api.dhan_client import get_token, get_security_id

    symbols = await get_symbols(pool)
    logger.info(f"NIFTY500 symbols loaded: {len(symbols)}")

    end_date = date.today()
    start_date = end_date - timedelta(days=365 * YEARS_BACK)

    token = get_token()
    if not token:
        logger.error("Could not get Dhan token, aborting")
        return

    total_inserted = 0
    total_symbols_done = 0
    skipped_no_sid = []

    for si, symbol in enumerate(symbols, 1):
        sid = get_security_id(symbol)
        if not sid:
            skipped_no_sid.append(symbol)
            continue

        for interval, tf_label in TIMEFRAMES:
            if await already_done(pool, symbol, tf_label):
                continue

            async with pool.acquire() as conn:
                log_id = await conn.fetchval(
                    """INSERT INTO intraday_ingestion_log (symbol, timeframe, from_date, to_date, status)
                       VALUES ($1,$2,$3,$4,'pending') RETURNING id""",
                    symbol, tf_label, start_date, end_date
                )

            symbol_rows = 0
            had_error = None
            for c_start, c_end in date_chunks(start_date, end_date, CHUNK_DAYS):
                try:
                    rows, err = fetch_chunk(token, sid, interval, c_start, c_end)
                except Exception as e:
                    err = str(e)
                    rows = None

                # token refresh on failure once
                if err and ("401" in err or "DH-906" in err or "token" in err.lower()):
                    token = get_token(force_refresh=True)
                    try:
                        rows, err = fetch_chunk(token, sid, interval, c_start, c_end)
                    except Exception as e:
                        err = str(e)
                        rows = None

                time.sleep(RATE_LIMIT_SLEEP)

                if err:
                    had_error = err
                    logger.warning(f"{symbol} {tf_label} {c_start}->{c_end}: {err}")
                    continue

                if rows:
                    records = [(symbol, tf_label, t, o, h, l, cl, v) for (t, o, h, l, cl, v) in rows]
                    async with pool.acquire() as conn:
                        await conn.executemany(
                            """INSERT INTO intraday_ohlcv (symbol, timeframe, time, open, high, low, close, volume)
                               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                               ON CONFLICT (symbol, timeframe, time) DO NOTHING""",
                            records
                        )
                    symbol_rows += len(records)

            total_inserted += symbol_rows
            async with pool.acquire() as conn:
                await conn.execute(
                    """UPDATE intraday_ingestion_log
                       SET records_inserted=$1, status=$2, error_message=$3, completed_at=NOW()
                       WHERE id=$4""",
                    symbol_rows, 'completed' if not had_error or symbol_rows > 0 else 'failed',
                    had_error, log_id
                )
            logger.info(f"[{si}/{len(symbols)}] {symbol} {tf_label}: {symbol_rows} candles")

        total_symbols_done += 1
        if si % 25 == 0:
            logger.info(f"=== Progress: {si}/{len(symbols)} symbols, {total_inserted:,} total candles ===")

    logger.info(f"DONE. {total_symbols_done} symbols processed, {total_inserted:,} candles inserted.")
    if skipped_no_sid:
        logger.warning(f"Skipped (no security id): {skipped_no_sid}")

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
