#!/usr/bin/env python3
"""
Historical OHLCV Data Ingestion Script
Fetch 15 years of daily data from Dhan API for all NSE symbols
And store in PostgreSQL + TimescaleDB

Usage:
    python ingest_ohlcv.py

Or run in background:
    nohup python ingest_ohlcv.py > ingest.log 2>&1 &

Estimated runtime: 20-30 hours for 2000 symbols
"""

import asyncio
import asyncpg
import os
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
import sys
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler('ingest.log'),
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

# Validate configuration
if not all([DHAN_CLIENT_ID, DHAN_PIN, DHAN_TOTP_SECRET, DB_PASSWORD]):
    logger.error("❌ Missing required environment variables")
    logger.error("   Required: DHAN_CLIENT_ID, DHAN_PIN, DHAN_TOTP_SECRET, DB_PASSWORD")
    sys.exit(1)

# ============================================================
# DHAN API HELPERS
# ============================================================

# Global token cache to avoid regenerating every call
_cached_token = None
_token_generated_time = None

def get_dhan_token(force_refresh=False):
    """
    Authenticate with Dhan API using generateAccessToken endpoint
    Caches token to avoid 2-minute rate limit
    """
    import requests
    import pyotp
    import time

    global _cached_token, _token_generated_time

    # Return cached token if available and not forced to refresh
    if _cached_token and not force_refresh:
        # Check if token is less than 55 minutes old (tokens valid for ~1 hour)
        if _token_generated_time and (time.time() - _token_generated_time) < 3300:
            return _cached_token

    try:
        totp = pyotp.TOTP(DHAN_TOTP_SECRET)
        otp = totp.now()

        logger.info("📡 Generating new Dhan access token...")

        # Correct Dhan authentication endpoint
        response = requests.post(
            "https://auth.dhan.co/app/generateAccessToken",
            params={
                "dhanClientId": DHAN_CLIENT_ID,
                "pin": DHAN_PIN,
                "totp": otp
            },
            timeout=30
        )

        if response.status_code != 200:
            logger.error(f"❌ Dhan auth failed: {response.status_code} - {response.text}")
            sys.exit(1)

        data = response.json()
        token = data.get("accessToken")

        if not token:
            logger.error(f"❌ No token in response: {data}")
            sys.exit(1)

        # Cache the token
        _cached_token = token
        _token_generated_time = time.time()

        logger.info("✅ Dhan access token obtained (cached for 55 min)")
        return token

    except Exception as e:
        logger.error(f"❌ Authentication failed: {e}")
        sys.exit(1)

async def fetch_historical_ohlcv(token: str, security_id: str, from_date: str, to_date: str):
    """
    Fetch historical OHLCV from Dhan API v2
    Returns: List of candles or empty list on error
    """
    import requests

    try:
        # Dhan API v2 uses POST with JSON body
        response = requests.post(
            "https://api.dhan.co/v2/charts/historical",
            headers={"access-token": token},  # v2 uses access-token header
            json={
                "securityId": security_id,
                "exchangeSegment": "NSE_EQ",  # NSE Equity segment
                "instrument": "EQUITY",
                "expiryCode": 0,
                "oi": False,
                "fromDate": from_date,
                "toDate": to_date
            },
            timeout=30
        )

        # Rate limiting - respect API limits
        await asyncio.sleep(0.5)

        if response.status_code == 200:
            data = response.json()
            # v2 API returns arrays for OHLCV
            if data.get("open"):  # If open array exists, we have data
                # Convert to candle format
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

    except requests.exceptions.Timeout:
        logger.warning(f"  ⚠️ Timeout for {security_id} ({from_date} to {to_date})")
        return []
    except Exception as e:
        logger.warning(f"  ⚠️ API error for {security_id}: {e}")
        return []

async def get_nse_symbols(token: str):
    """
    Fetch all 2,953 NSE equity stocks from Dhan instrument CSV
    Filters: NSE exchange, E segment, ES instrument type (stocks only)
    Returns: List of dicts with {'symbol': 'INFY', 'dhan_security_id': '1234', 'name': 'Company Name'}
    """
    import requests
    import pandas as pd

    try:
        logger.info("📊 Fetching NSE equity symbols...")

        url = "https://images.dhan.co/api-data/api-scrip-master.csv"
        df = pd.read_csv(url, low_memory=False)

        # Filter: NSE Exchange, E segment, ES instrument type (equity stocks only)
        # This gives us 2,953 real stocks (excludes options, futures, debt, MFs, etc.)
        df = df[
            (df['SEM_EXM_EXCH_ID'] == 'NSE') &
            (df['SEM_SEGMENT'] == 'E') &
            (df['SEM_EXCH_INSTRUMENT_TYPE'] == 'ES')  # Equity Stocks only
        ]

        logger.info(f"📋 Found {len(df)} NSE equity stocks")

        symbols = df[[
            'SEM_TRADING_SYMBOL',
            'SEM_SMST_SECURITY_ID',  # Correct column name
            'SM_SYMBOL_NAME'  # Correct column name
        ]].rename(columns={
            'SEM_TRADING_SYMBOL': 'symbol',
            'SEM_SMST_SECURITY_ID': 'dhan_security_id',
            'SM_SYMBOL_NAME': 'name'
        }).to_dict('records')

        logger.info(f"✅ Loaded {len(symbols)} NSE equity symbols (ES type)")
        return symbols

    except Exception as e:
        logger.error(f"❌ Failed to fetch symbols: {e}")
        logger.info("⚠️ Using fallback symbol list (limited to 3 symbols)")
        # Fallback: Common symbols only
        return [
            {'symbol': 'INFY', 'dhan_security_id': '10099', 'name': 'Infosys'},
            {'symbol': 'TCS', 'dhan_security_id': '11536', 'name': 'Tata Consultancy Services'},
            {'symbol': 'RELIANCE', 'dhan_security_id': '10999', 'name': 'Reliance Industries'},
        ]

# ============================================================
# DATABASE OPERATIONS
# ============================================================

async def ingest_all_historical():
    """
    Main ingestion loop
    Fetches 15 years of OHLCV for all NSE symbols
    """
    # Get Dhan token
    token = get_dhan_token()

    # Get NSE symbols
    symbols = await get_nse_symbols(token)

    # Database connection pool
    logger.info("📍 Connecting to database...")
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

    logger.info(f"🚀 Starting ingestion for {len(symbols)} symbols (15 years)")
    logger.info("   This will take 20-30 hours. You can safely interrupt with Ctrl+C")

    total_symbols = len(symbols)
    total_records = 0
    start_time = datetime.now()

    try:
        for idx, sym_info in enumerate(symbols):
            symbol = sym_info['symbol']
            dhan_id = str(sym_info['dhan_security_id'])
            all_candles = []

            logger.info(f"[{idx+1}/{total_symbols}] {symbol}... fetching")

            # Fetch year by year
            for year in range(2010, 2025):
                from_date = f"{year}-01-01"
                to_date = f"{year}-12-31"

                try:
                    candles = await fetch_historical_ohlcv(token, dhan_id, from_date, to_date)

                    # Transform to DB format
                    for candle in candles:
                        try:
                            candle_date = datetime.strptime(candle['timestamp'], '%Y-%m-%d')
                            all_candles.append((
                                symbol,
                                candle_date,
                                float(candle['open']),
                                float(candle['high']),
                                float(candle['low']),
                                float(candle['close']),
                                int(candle['volume']),
                                int(candle.get('oi', 0)) if candle.get('oi') else None
                            ))
                        except (KeyError, ValueError) as e:
                            logger.debug(f"  Skipping malformed candle: {e}")

                except Exception as e:
                    logger.warning(f"Year {year}: {e}")

            # Bulk insert
            if all_candles:
                try:
                    async with pool.acquire() as conn:
                        await conn.executemany(
                            """INSERT INTO ohlcv_data (symbol, time, open, high, low, close, volume, oi)
                               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                               ON CONFLICT (symbol, time) DO UPDATE SET
                               open=EXCLUDED.open,
                               high=EXCLUDED.high,
                               low=EXCLUDED.low,
                               close=EXCLUDED.close,
                               volume=EXCLUDED.volume,
                               oi=EXCLUDED.oi,
                               updated_at=NOW()
                            """,
                            all_candles
                        )

                    total_records += len(all_candles)
                    logger.info(f"✅ {len(all_candles)} candles")

                except asyncpg.exceptions.UniqueViolationError:
                    logger.warning(f"⚠️ Duplicate records skipped for {symbol}")
                except Exception as e:
                    logger.error(f"❌ Insert failed: {e}")
            else:
                logger.info("⚠️ No data")

        # Summary
        elapsed = datetime.now() - start_time
        logger.info("\n" + "="*60)
        logger.info("🎉 INGESTION COMPLETE!")
        logger.info(f"   Total records: {total_records:,}")
        logger.info(f"   Time elapsed: {elapsed}")
        logger.info(f"   Rate: {total_records / elapsed.total_seconds():.0f} records/sec")
        logger.info("="*60)

    except KeyboardInterrupt:
        logger.info("\n⚠️ Ingestion interrupted by user")
        logger.info(f"   Inserted {total_records:,} records before interruption")
    except Exception as e:
        logger.error(f"\n❌ Ingestion failed: {e}")
        logger.exception(e)
    finally:
        await pool.close()
        logger.info("Database pool closed")

# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    logger.info("="*60)
    logger.info("Market Data Ingestion - Historical OHLCV")
    logger.info("="*60)

    try:
        asyncio.run(ingest_all_historical())
    except KeyboardInterrupt:
        logger.info("\n❌ Ingestion cancelled")
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        sys.exit(1)
