"""
Refresh symbols_meta from the Dhan instrument master CSV.

Upserts every NSE equity-segment symbol with: security_name, series, lot_size,
is_sme (series SM/ST = NSE EMERGE SME board), dhan_security_id.

Run weekly via cron (master file is refreshed by the OHLCV updater):
  0 10 * * 0 cd /root/trade-execution-webhook && ./venv/bin/python market_data_setup/scripts/update_symbols_meta.py >> market_data_setup/scripts/meta.log 2>&1
"""
import asyncio
import csv
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import asyncpg
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

MASTER_PATHS = [
    "/root/trade-execution-webhook/api-scrip-master.csv",
    str(Path(__file__).parent.parent.parent / "api-scrip-master.csv"),
]

SME_SERIES = {"SM", "ST", "SZ"}          # NSE EMERGE (SME) board series
EQUITY_SERIES = {"EQ", "BE", "BZ"} | SME_SERIES  # what we track in symbols_meta


def parse_master() -> list[tuple]:
    src = next((p for p in MASTER_PATHS if os.path.exists(p)), None)
    if not src:
        raise SystemExit("api-scrip-master.csv not found")
    rows = []
    with open(src, newline="") as f:
        for r in csv.DictReader(f):
            if r.get("SEM_EXM_EXCH_ID") != "NSE" or r.get("SEM_SEGMENT") != "E":
                continue
            series = (r.get("SEM_SERIES") or "").strip().upper()
            if series not in EQUITY_SERIES:
                continue  # bonds, SGBs, mutual funds, T-bills, etc.
            sym = (r.get("SEM_TRADING_SYMBOL") or "").strip().upper()
            if not sym:
                continue
            try:
                lot = int(float(r.get("SEM_LOT_UNITS") or 1))
            except ValueError:
                lot = 1
            rows.append((
                sym,
                (r.get("SM_SYMBOL_NAME") or "").strip() or None,
                series,
                lot,
                series in SME_SERIES or lot > 1,
                (r.get("SEM_SMST_SECURITY_ID") or "").strip() or None,
            ))
    return rows


async def main():
    load_dotenv("/root/trade-execution-webhook/.env")
    pool = await asyncpg.create_pool(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        user=os.getenv("DB_USER", "market_data_user"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME", "market_data"),
        min_size=1, max_size=2,
    )
    rows = parse_master()
    sme = sum(1 for r in rows if r[4])
    logger.info("Master parsed: %d equity symbols (%d SME/lot-traded)", len(rows), sme)

    async with pool.acquire() as con:
        await con.executemany(
            """
            INSERT INTO symbols_meta (symbol, security_name, series, lot_size, is_sme,
                                      dhan_security_id, is_active, last_updated)
            VALUES ($1, $2, $3, $4, $5, $6, TRUE, NOW())
            ON CONFLICT (symbol) DO UPDATE SET
                security_name = COALESCE(EXCLUDED.security_name, symbols_meta.security_name),
                series = EXCLUDED.series,
                lot_size = EXCLUDED.lot_size,
                is_sme = EXCLUDED.is_sme,
                dhan_security_id = COALESCE(EXCLUDED.dhan_security_id, symbols_meta.dhan_security_id),
                last_updated = NOW()
            """,
            rows,
        )
        n = await con.fetchval("SELECT count(*) FROM symbols_meta WHERE is_sme")
    logger.info("✅ symbols_meta refreshed; %d symbols flagged SME/lot-traded", n)
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
