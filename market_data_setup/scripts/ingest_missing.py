#!/usr/bin/env python3
"""
Ingest OHLCV history for NIFTY-500 symbols that are MISSING from market_data.

The daily updater (update_ohlcv.py) only fills gaps for symbols already present
in ohlcv_data. New listings / symbols never ingested (e.g. TRIDENT, POLYCAB,
LICI, GROWW) therefore never appear — and their charts 404. This script finds
those missing symbols and backfills ~6 years of daily candles for each.

Usage:
    python ingest_missing.py                 # auto-detect missing NIFTY-500 symbols
    python ingest_missing.py SYM1 SYM2 ...    # ingest specific symbols
"""

import os
import sys
import csv
import time
import json
import logging
import requests
import psycopg2
from datetime import datetime, timedelta
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

BASE = "/root/trade-execution-webhook"
load_dotenv(f"{BASE}/.env")

DB = dict(host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORT", 5432)),
          user=os.getenv("DB_USER", "market_data_user"), password=os.getenv("DB_PASSWORD"),
          dbname=os.getenv("DB_NAME", "market_data"))
SCRIP = f"{BASE}/api-scrip-master.csv"
TOKEN_CACHE = f"{BASE}/.dhan_token_cache.json"
HISTORY_FROM = "2019-01-01"


def get_token():
    try:
        c = json.load(open(TOKEN_CACHE))
        if time.time() - c.get("generated_at", 0) < 23 * 3600 and c.get("token"):
            # validate
            r = requests.get("https://api.dhan.co/v2/fundlimit",
                             headers={"access-token": c["token"], "client-id": os.getenv("DHAN_CLIENT_ID")}, timeout=10)
            if r.status_code == 200:
                return c["token"]
    except Exception:
        pass
    import pyotp
    totp = pyotp.TOTP(os.getenv("DHAN_TOTP_SECRET")).now()
    r = requests.post("https://auth.dhan.co/app/generateAccessToken",
                      params={"dhanClientId": os.getenv("DHAN_CLIENT_ID"), "pin": os.getenv("DHAN_PIN"), "totp": totp}, timeout=15)
    tk = r.json().get("accessToken")
    if tk:
        json.dump({"token": tk, "generated_at": time.time()}, open(TOKEN_CACHE, "w"))
    return tk


def load_security_map():
    m = {}
    with open(SCRIP, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("SEM_EXM_EXCH_ID") == "NSE" and row.get("SEM_SEGMENT") == "E":
                sym = str(row.get("SEM_TRADING_SYMBOL", "")).strip().upper()
                sid = str(row.get("SEM_SMST_SECURITY_ID", "")).strip()
                if sym and sid:
                    m[sym] = sid
    return m


def nifty500_missing(conn):
    sys.path.insert(0, BASE)
    import screen_gpt as s
    uni = set(x.replace(".NS", "") for x in s.get_stocks())
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT symbol FROM ohlcv_data")
    have = set(r[0] for r in cur.fetchall())
    return sorted(uni - have)


def fetch_history(token, security_id, from_date, to_date):
    r = requests.post("https://api.dhan.co/v2/charts/historical",
                      headers={"access-token": token},
                      json={"securityId": security_id, "exchangeSegment": "NSE_EQ", "instrument": "EQUITY",
                            "expiryCode": 0, "oi": False, "fromDate": from_date, "toDate": to_date}, timeout=30)
    if r.status_code != 200:
        return []
    d = r.json()
    if not d.get("open"):
        return []
    out = []
    for i in range(len(d.get("timestamp", []))):
        ts = d["timestamp"][i]
        dt = datetime.fromtimestamp(ts / 1000 if ts > 1e10 else ts)
        out.append((dt, d["open"][i], d["high"][i], d["low"][i], d["close"][i], int(d["volume"][i])))
    return out


def main():
    conn = psycopg2.connect(**DB)
    conn.autocommit = True
    secmap = load_security_map()
    logger.info(f"Loaded {len(secmap)} security IDs from scrip master")

    symbols = [s.upper() for s in sys.argv[1:]] or nifty500_missing(conn)
    logger.info(f"Symbols to ingest: {len(symbols)} -> {symbols}")

    token = get_token()
    if not token:
        logger.error("No Dhan token"); return

    today = datetime.now().strftime("%Y-%m-%d")
    total_rows, done, skipped = 0, 0, []
    cur = conn.cursor()

    for i, sym in enumerate(symbols, 1):
        sid = secmap.get(sym)
        if not sid:
            skipped.append(sym); logger.warning(f"[{i}/{len(symbols)}] {sym}: no security ID — skip"); continue
        candles = fetch_history(token, sid, HISTORY_FROM, today)
        time.sleep(0.4)
        if not candles:
            skipped.append(sym); logger.warning(f"[{i}/{len(symbols)}] {sym}: no data returned — skip"); continue
        rows = [(sym, dt, o, h, l, c, v) for (dt, o, h, l, c, v) in candles]
        cur.executemany(
            "INSERT INTO ohlcv_data (symbol, time, open, high, low, close, volume) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (symbol, time) DO NOTHING", rows)
        # register in symbols_meta (best-effort — never let a meta conflict lose the OHLCV)
        try:
            cur.execute(
                "INSERT INTO symbols_meta (symbol, dhan_security_id, is_active) VALUES (%s,%s,true) "
                "ON CONFLICT (symbol) DO UPDATE SET dhan_security_id = EXCLUDED.dhan_security_id",
                (sym, sid))
        except Exception as e:
            logger.warning(f"   {sym}: symbols_meta upsert skipped ({str(e)[:60]})")
        total_rows += len(rows); done += 1
        logger.info(f"[{i}/{len(symbols)}] {sym}: inserted {len(rows)} rows ({candles[0][0].date()}..{candles[-1][0].date()})")

    logger.info("=" * 50)
    logger.info(f"✅ DONE. Ingested {done} symbols, {total_rows:,} rows. Skipped {len(skipped)}: {skipped}")
    conn.close()


if __name__ == "__main__":
    main()
