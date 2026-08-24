#!/usr/bin/env python3
"""
Ingest 2 years of Sensex weekly options rolling data (ATM-6..ATM+6, CE & PE, 5m)
via Dhan's /charts/rollingoption endpoint.

Note: expiryCode=0 (near/current) has a Dhan-side bug that rejects it as "required".
Using expiryCode=1 (next) as the workaround for continuous historical rolling series.
"""
import asyncio, asyncpg, os, sys, time, logging, json
from datetime import date, timedelta
import requests
from dotenv import load_dotenv

sys.path.insert(0, "/root/trade-execution-webhook")
load_dotenv("/root/trade-execution-webhook/.env")

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s',
                     handlers=[logging.FileHandler('/root/ingest_sensex_opt.log'), logging.StreamHandler()])
logger = logging.getLogger(__name__)

DB_HOST=os.getenv('DB_HOST','localhost'); DB_PORT=int(os.getenv('DB_PORT',5432))
DB_USER=os.getenv('DB_USER','market_data_user'); DB_PASSWORD=os.getenv('DB_PASSWORD','secure_market_data_pass_2026')
DB_NAME=os.getenv('DB_NAME','market_data')

URL = "https://api.dhan.co/v2/charts/rollingoption"
STRIKE_OFFSETS = list(range(-6, 7))  # ATM-6 .. ATM+6
OPTION_TYPES = ["CALL", "PUT"]
CHUNK_DAYS = 28
RATE_SLEEP = 1.1


def strike_label(off):
    if off == 0: return "ATM"
    return f"ATM{'+' if off>0 else ''}{off}"


def date_chunks(start, end, chunk_days):
    cur = start
    while cur < end:
        ce = min(cur + timedelta(days=chunk_days), end)
        yield cur, ce
        cur = ce + timedelta(days=1)


def fetch(token, off, opt_type, start, end):
    headers = {"access-token": token, "Content-Type": "application/json"}
    payload = {
        "exchangeSegment": "BSE_FNO", "interval": "5", "securityId": "51",
        "instrument": "OPTIDX", "expiryFlag": "WEEK", "expiryCode": 1,
        "strike": f"ATM{'+' if off>0 else ''}{off}" if off != 0 else "ATM",
        "drvOptionType": opt_type,
        "requiredData": ["open","high","low","close","iv","volume","strike","oi","spot"],
        "fromDate": start.isoformat(), "toDate": end.isoformat()
    }
    r = requests.post(URL, json=payload, headers=headers, timeout=30)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:200]}"
    d = r.json().get("data", {})
    side = d.get("ce") if opt_type == "CALL" else d.get("pe")
    if not side or not side.get("close"):
        return [], None
    n = len(side["close"])
    rows = []
    from datetime import datetime, timezone
    for i in range(n):
        ts = side["timestamp"][i]
        rows.append((
            datetime.fromtimestamp(ts, tz=timezone.utc),
            side.get("strike", [None]*n)[i] if side.get("strike") else None,
            side.get("spot", [None]*n)[i] if side.get("spot") else None,
            side["open"][i], side["high"][i], side["low"][i], side["close"][i],
            int(side["volume"][i]) if side.get("volume") else 0,
            int(side["oi"][i]) if side.get("oi") else None,
            side.get("iv", [None]*n)[i] if side.get("iv") else None,
        ))
    return rows, None


async def main():
    pool = await asyncpg.create_pool(host=DB_HOST, port=DB_PORT, user=DB_USER,
                                      password=DB_PASSWORD, database=DB_NAME, min_size=1, max_size=3)
    from web_api.dhan_client import get_token
    token = get_token()
    end_date = date.today()
    start_date = end_date - timedelta(days=365*2)

    total = 0
    for off in STRIKE_OFFSETS:
        for opt_type in OPTION_TYPES:
            label = strike_label(off)
            sym_total = 0
            for c_start, c_end in date_chunks(start_date, end_date, CHUNK_DAYS):
                try:
                    rows, err = fetch(token, off, opt_type, c_start, c_end)
                except Exception as e:
                    rows, err = None, str(e)
                if err and ("401" in err or "DH-906" in err or "token" in err.lower()):
                    token = get_token(force_refresh=True)
                    try:
                        rows, err = fetch(token, off, opt_type, c_start, c_end)
                    except Exception as e:
                        rows, err = None, str(e)
                time.sleep(RATE_SLEEP)
                if err:
                    logger.warning(f"{label} {opt_type} {c_start}->{c_end}: {err}")
                    continue
                if rows:
                    ot = "CE" if opt_type == "CALL" else "PE"
                    records = [(t, label, ot, sp, spot, o, h, l, cl, v, oi, iv)
                               for (t, sp, spot, o, h, l, cl, v, oi, iv) in rows]
                    async with pool.acquire() as conn:
                        await conn.executemany(
                            """INSERT INTO sensex_options_ohlcv
                               (time, strike_label, option_type, strike_price, spot, open, high, low, close, volume, oi, iv)
                               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                               ON CONFLICT (strike_label, option_type, time) DO NOTHING""",
                            records
                        )
                    sym_total += len(records)
            total += sym_total
            logger.info(f"{label} {opt_type}: {sym_total} candles (total so far {total})")

    logger.info(f"DONE. Total candles: {total}")
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
