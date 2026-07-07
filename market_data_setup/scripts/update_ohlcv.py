#!/usr/bin/env python3
"""
Daily OHLCV Data Update Script
Auto-detects gaps in DB and fetches missing candles from Dhan API

Usage:
    # Daily mode (fetch last 3 days - catches weekends/holidays)
    python update_ohlcv.py

    # Backfill recent missing (last 30 days)
    python update_ohlcv.py --days 30

    # Backfill specific date range
    python update_ohlcv.py --from 2026-01-01 --to 2026-06-28

Or scheduled as cron:
    30 12 * * * cd /root/trade-execution-webhook && python market_data_setup/scripts/update_ohlcv.py >> update.log 2>&1
    (Runs daily at 12:30 UTC = 18:00 IST)
"""

import asyncio
import asyncpg
import os
import logging
import argparse
from datetime import datetime, timedelta
from dotenv import load_dotenv
import sys
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler('update.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Load environment
env_paths = [
    Path(__file__).parent.parent.parent.parent / '.env',
    Path('/root/trade-execution-webhook/.env'),
    Path.home() / '.env'
]

for env_file in env_paths:
    if env_file.exists():
        load_dotenv(env_file)
        logger.info(f"✅ Loaded environment from {env_file}")
        break

# Configuration
DHAN_CLIENT_ID = os.getenv('DHAN_CLIENT_ID')
DHAN_PIN = os.getenv('DHAN_PIN')
DHAN_TOTP_SECRET = os.getenv('DHAN_TOTP_SECRET')

DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = int(os.getenv('DB_PORT', 5432))
DB_USER = os.getenv('DB_USER', 'market_data_user')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_NAME = os.getenv('DB_NAME', 'market_data')

if not all([DHAN_CLIENT_ID, DHAN_PIN, DHAN_TOTP_SECRET, DB_PASSWORD]):
    logger.error("❌ Missing required environment variables")
    sys.exit(1)

# ============================================================
# DHAN API HELPERS (reuse from ingest)
# ============================================================

_cached_token = None
_token_generated_time = None

def get_dhan_token(force_refresh=False):
    import requests
    import pyotp
    import time
    import json as _json

    global _cached_token, _token_generated_time

    if _cached_token and not force_refresh:
        if _token_generated_time and (time.time() - _token_generated_time) < 3300:
            return _cached_token

    # Shared file cache (written by web_api / other jobs) — avoids Dhan token rate limits
    _shared_cache = "/root/trade-execution-webhook/.dhan_token_cache.json"
    if not force_refresh:
        try:
            with open(_shared_cache) as _f:
                _c = _json.load(_f)
            if time.time() - _c.get("generated_at", 0) < 23 * 3600 and _c.get("token"):
                logger.info("✅ Reusing shared cached Dhan token")
                _cached_token = _c["token"]
                _token_generated_time = _c["generated_at"]
                return _cached_token
        except Exception:
            pass

    try:
        logger.info("📡 Generating new Dhan access token...")

        for attempt in range(3):
            totp = pyotp.TOTP(DHAN_TOTP_SECRET)
            otp = totp.now()

            response = requests.post(
                "https://auth.dhan.co/app/generateAccessToken",
                params={
                    "dhanClientId": DHAN_CLIENT_ID,
                    "pin": DHAN_PIN,
                    "totp": otp
                },
                timeout=45
            )

            if response.status_code == 200:
                break

            if attempt < 2:
                logger.warning(f"  Attempt {attempt+1} failed, retrying...")
                time.sleep(2)

        if response.status_code != 200:
            logger.error(f"❌ Dhan auth failed: {response.status_code}")
            sys.exit(1)

        data = response.json()
        token = data.get("accessToken")

        if not token:
            logger.error(f"❌ No token in response")
            sys.exit(1)

        _cached_token = token
        _token_generated_time = time.time()
        logger.info("✅ Token obtained (cached for 55 min)")
        return token

    except Exception as e:
        logger.error(f"❌ Auth failed: {e}")
        sys.exit(1)

async def fetch_historical_ohlcv(token: str, security_id: str, from_date: str, to_date: str):
    """Fetch historical OHLCV from Dhan API v2"""
    import requests

    try:
        response = requests.post(
            "https://api.dhan.co/v2/charts/historical",
            headers={"access-token": token},
            json={
                "securityId": security_id,
                "exchangeSegment": "NSE_EQ",
                "instrument": "EQUITY",
                "expiryCode": 0,
                "oi": False,
                "fromDate": from_date,
                "toDate": to_date
            },
            timeout=30
        )

        await asyncio.sleep(0.5)

        if response.status_code == 200:
            data = response.json()
            if data.get("open"):
                candles = []
                for i in range(len(data.get("timestamp", []))):
                    candle = {
                        "timestamp": data["timestamp"][i],
                        "open": data["open"][i],
                        "high": data["high"][i],
                        "low": data["low"][i],
                        "close": data["close"][i],
                        "volume": data["volume"][i]
                    }
                    candles.append(candle)
                return candles

        return []

    except Exception as e:
        logger.warning(f"API error: {e}")
        return []

# ============================================================
# GAP DETECTION & UPDATE
# ============================================================

async def get_symbols_with_gaps(pool, from_date: datetime, to_date: datetime):
    """
    Find symbols and their missing date ranges
    Returns: {symbol: [(gap_start, gap_end), ...], ...}
    """
    async with pool.acquire() as conn:
        # Get all symbols in DB
        symbols = await conn.fetch(
            "SELECT DISTINCT symbol FROM ohlcv_data ORDER BY symbol"
        )

    symbol_gaps = {}

    for row in symbols:
        symbol = row['symbol']

        async with pool.acquire() as conn:
            # Get date range of existing data for this symbol
            result = await conn.fetchrow(
                "SELECT MIN(time) as min_date, MAX(time) as max_date FROM ohlcv_data WHERE symbol = $1",
                symbol
            )

        if not result or result['min_date'] is None:
            # No data for this symbol yet
            symbol_gaps[symbol] = [(from_date, to_date)]
            continue

        db_min = result['min_date'].replace(tzinfo=None)
        db_max = result['max_date'].replace(tzinfo=None)

        # Check for gaps
        gaps = []

        # Gap before earliest data
        if db_min > from_date:
            gaps.append((from_date, db_min - timedelta(days=1)))

        # Gap after latest data
        if db_max < to_date:
            gaps.append((db_max + timedelta(days=1), to_date))

        if gaps:
            symbol_gaps[symbol] = gaps

    return symbol_gaps

def load_security_map():
    """
    Build {SYMBOL: dhan_security_id} from the Dhan instrument master CSV — the
    same source the screener uses. Covers ALL NSE equity symbols (not just the
    handful seeded in symbols_meta, which is why the old updater only touched 10).
    """
    import csv as _csv
    paths = [
        "/root/trade-execution-webhook/api-scrip-master.csv",
        str(Path(__file__).parent.parent.parent / "api-scrip-master.csv"),
    ]
    src = next((p for p in paths if os.path.exists(p)), None)
    mapping = {}
    if not src:
        logger.warning("⚠️ api-scrip-master.csv not found — falling back to symbols_meta")
        return mapping
    try:
        with open(src, newline="") as f:
            reader = _csv.DictReader(f)
            for row in reader:
                if row.get("SEM_EXM_EXCH_ID") == "NSE" and row.get("SEM_SEGMENT") == "E":
                    sym = str(row.get("SEM_TRADING_SYMBOL", "")).strip().upper()
                    sid = str(row.get("SEM_SMST_SECURITY_ID", "")).strip()
                    if sym and sid:
                        mapping[sym] = sid
        logger.info(f"✅ Loaded {len(mapping)} NSE equity security IDs from scrip master")
    except Exception as e:
        logger.error(f"❌ Failed to read scrip master: {e}")
    return mapping


async def update_missing_data(token: str, pool, symbol: str, dhan_id: str, from_date: datetime, to_date: datetime):
    """Fetch and insert missing data for a symbol"""
    try:
        from_str = from_date.strftime("%Y-%m-%d")
        to_str = to_date.strftime("%Y-%m-%d")

        candles = await fetch_historical_ohlcv(token, dhan_id, from_str, to_str)

        if not candles:
            return 0

        # Transform candles
        rows = []
        for candle in candles:
            try:
                ts = candle['timestamp']
                if isinstance(ts, (int, float)):
                    if ts > 1e10:
                        ts = ts / 1000
                    candle_date = datetime.fromtimestamp(ts)
                else:
                    candle_date = datetime.strptime(str(ts), '%Y-%m-%d')

                rows.append((
                    symbol,
                    candle_date,
                    float(candle['open']),
                    float(candle['high']),
                    float(candle['low']),
                    float(candle['close']),
                    int(candle['volume']),
                    int(candle.get('oi', 0)) if candle.get('oi') else None
                ))
            except (KeyError, ValueError, TypeError):
                pass

        if rows:
            async with pool.acquire() as conn:
                await conn.executemany(
                    """INSERT INTO ohlcv_data (symbol, time, open, high, low, close, volume, oi)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                       ON CONFLICT (symbol, time) DO NOTHING
                    """,
                    rows
                )

            return len(rows)

        return 0

    except Exception as e:
        logger.warning(f"Update failed for {symbol}: {e}")
        return 0

async def main():
    # Parse arguments
    parser = argparse.ArgumentParser(description='Update missing OHLCV data')
    parser.add_argument('--from', type=str, dest='from_date', help='From date (YYYY-MM-DD)')
    parser.add_argument('--to', type=str, dest='to_date', help='To date (YYYY-MM-DD)')
    parser.add_argument('--days', type=int, default=3, help='Last N days (default: 3)')

    args = parser.parse_args()

    # Determine date range
    today = datetime.now()

    if args.from_date and args.to_date:
        # Specific range provided
        from_date = datetime.strptime(args.from_date, "%Y-%m-%d")
        to_date = datetime.strptime(args.to_date, "%Y-%m-%d")
        mode = f"backfill ({args.from_date} to {args.to_date})"
    else:
        # Default: last N days
        from_date = today - timedelta(days=args.days)
        to_date = today
        mode = f"daily (last {args.days} days)"

    logger.info("=" * 60)
    logger.info(f"🔄 Update OHLCV Data - {mode}")
    logger.info(f"   Date range: {from_date.strftime('%Y-%m-%d')} to {to_date.strftime('%Y-%m-%d')}")
    logger.info("=" * 60)

    # Get token
    token = get_dhan_token()

    # Connect to DB
    pool = await asyncpg.create_pool(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        min_size=1,
        max_size=3,
        timeout=60
    )

    try:
        # Find gaps
        logger.info("📊 Scanning for missing data...")
        gaps = await get_symbols_with_gaps(pool, from_date, to_date)

        if not gaps:
            logger.info("✅ No gaps found - database is up to date!")
            return

        logger.info(f"📋 Found gaps for {len(gaps)} symbols")

        # Resolve symbol -> Dhan security ID from the instrument master CSV
        # (covers all NSE equities). Fall back to symbols_meta for anything missing.
        symbol_map = load_security_map()
        async with pool.acquire() as conn:
            meta_rows = await conn.fetch(
                "SELECT symbol, dhan_security_id FROM symbols_meta WHERE dhan_security_id IS NOT NULL AND dhan_security_id != ''"
            )
        for row in meta_rows:
            symbol_map.setdefault(row['symbol'], str(row['dhan_security_id']))

        missing_ids = [s for s in gaps if s not in symbol_map]
        if missing_ids:
            logger.info(f"ℹ️ {len(missing_ids)} symbols have no security ID (likely delisted) — skipping")

        # Update missing data
        total_inserted = 0
        count = 0

        for symbol, gap_list in sorted(gaps.items()):
            if symbol not in symbol_map:
                continue

            dhan_id = symbol_map[symbol]
            count += 1

            logger.info(f"[{count}/{len(gaps)}] {symbol}... filling {len(gap_list)} gap(s)")

            for gap_from, gap_to in gap_list:
                inserted = await update_missing_data(token, pool, symbol, dhan_id, gap_from, gap_to)
                total_inserted += inserted

        # Summary
        logger.info("\n" + "=" * 60)
        logger.info("✅ UPDATE COMPLETE!")
        logger.info(f"   Symbols updated: {count}")
        logger.info(f"   Rows inserted: {total_inserted:,}")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"❌ Update failed: {e}")
        logger.exception(e)
    finally:
        await pool.close()

if __name__ == "__main__":
    asyncio.run(main())
