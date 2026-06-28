#!/usr/bin/env python3
"""
Daily OHLCV Update Script
Fetch latest candles from Dhan API (last 5 days)
Update database with new/modified data

Usage (manual):
    python update_daily_ohlcv.py

Usage (automated - add to crontab):
    # At 18:00 IST every weekday (12:30 UTC)
    30 12 * * 1-5 cd /root/trade-execution-webhook && source venv/bin/activate && python market_data_setup/scripts/update_daily_ohlcv.py >> /var/log/update_ohlcv.log 2>&1
"""

import asyncio
import asyncpg
import os
import logging
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import sys
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s'
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

# Validate
if not all([DHAN_CLIENT_ID, DHAN_PIN, DHAN_TOTP_SECRET, DB_PASSWORD]):
    logger.error("❌ Missing environment variables")
    sys.exit(1)

# ============================================================
# DHAN API HELPERS
# ============================================================

def get_dhan_token():
    """Get JWT token from Dhan API"""
    import requests
    import pyotp

    try:
        totp = pyotp.TOTP(DHAN_TOTP_SECRET)
        otp = totp.now()

        response = requests.post(
            "https://api-gw.shoonya.com/auth/login",
            json={
                "userId": DHAN_CLIENT_ID,
                "password": DHAN_PIN,
                "twoFA": otp
            },
            timeout=30
        )

        return response.json().get("authToken")

    except Exception as e:
        logger.error(f"❌ Authentication failed: {e}")
        return None

async def fetch_historical_ohlcv(token: str, security_id: str, from_date: str, to_date: str):
    """Fetch OHLCV from Dhan API"""
    import requests
    import time

    try:
        response = requests.get(
            "https://api-gw.shoonya.com/historical",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "exchangeTokens": security_id,
                "from": from_date,
                "to": to_date,
                "resolution": "1d"
            },
            timeout=30
        )

        await asyncio.sleep(0.05)  # Rate limit

        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "success":
                return data.get("data", [])

        return []

    except Exception as e:
        logger.warning(f"API error: {e}")
        return []

# ============================================================
# UPDATE FUNCTION
# ============================================================

async def update_daily():
    """
    Fetch last 5 days of OHLCV for all active symbols
    Update database with new/modified candles
    """
    # Get token
    token = get_dhan_token()
    if not token:
        logger.error("❌ Failed to authenticate")
        return False

    # Database connection
    pool = await asyncpg.create_pool(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        min_size=1,
        max_size=2,
        timeout=60
    )

    try:
        # Get all active symbols
        async with pool.acquire() as conn:
            symbols = await conn.fetch(
                "SELECT symbol, dhan_security_id FROM symbols_meta WHERE is_active = TRUE ORDER BY symbol"
            )

        if not symbols:
            logger.warning("⚠️ No active symbols found")
            return False

        # Calculate date range (last 5 days)
        to_date = datetime.now(timezone.utc).date()
        from_date = to_date - timedelta(days=5)

        logger.info(f"📊 Updating {len(symbols)} symbols ({from_date} to {to_date})")

        updated_count = 0
        error_count = 0

        for idx, sym_row in enumerate(symbols):
            symbol = sym_row['symbol']
            dhan_id = str(sym_row['dhan_security_id'])

            try:
                # Fetch from Dhan
                candles = await fetch_historical_ohlcv(
                    token,
                    dhan_id,
                    str(from_date),
                    str(to_date)
                )

                if candles:
                    # Prepare records
                    records = []
                    for candle in candles:
                        try:
                            candle_date = datetime.strptime(candle['date'], '%Y-%m-%d')
                            records.append((
                                symbol,
                                candle_date,
                                float(candle['open']),
                                float(candle['high']),
                                float(candle['low']),
                                float(candle['close']),
                                int(candle['volume']),
                                int(candle.get('oi', 0)) if candle.get('oi') else None
                            ))
                        except (KeyError, ValueError):
                            continue

                    # Upsert to database
                    if records:
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
                                records
                            )

                        updated_count += len(records)
                        logger.info(f"[{idx+1}/{len(symbols)}] {symbol}: ✅ {len(records)} candles")
                else:
                    logger.info(f"[{idx+1}/{len(symbols)}] {symbol}: ⚠️ No data")

            except Exception as e:
                error_count += 1
                logger.error(f"[{idx+1}/{len(symbols)}] {symbol}: ❌ {e}")

        # Summary
        logger.info("="*60)
        logger.info(f"✅ Update complete")
        logger.info(f"   Updated: {updated_count:,} candles")
        logger.info(f"   Errors: {error_count}")
        logger.info("="*60)

        return True

    except Exception as e:
        logger.error(f"❌ Update failed: {e}")
        return False

    finally:
        await pool.close()

# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    logger.info("🔄 Daily OHLCV Update")

    try:
        success = asyncio.run(update_daily())
        sys.exit(0 if success else 1)
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        sys.exit(1)
