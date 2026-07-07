#!/usr/bin/env python3
"""
Same-day OHLCV update for the NIFTY-500 universe.

Dhan's *historical daily* API only publishes a trading day's EOD candle the NEXT
morning, so after market close today's candle exists only in the *intraday* feed.
This script aggregates today's intraday bars into a single daily OHLCV candle and
upserts it into ohlcv_data — bounded to ~491 NIFTY-500 symbols so it finishes in a
couple of minutes. Triggered by the dashboard 'Update Data' button (after the
historical gap-fill) so the screener can run on today's data.
"""

import os
import sys
import csv
import io
import time
import json
import logging
import datetime as dt

import requests
import psycopg2

BASE = "/root/trade-execution-webhook"
try:
    from dotenv import load_dotenv
    load_dotenv(f"{BASE}/.env")
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
log = logging.getLogger(__name__)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DB = dict(host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORT", 5432)),
          user=os.getenv("DB_USER", "market_data_user"), password=os.getenv("DB_PASSWORD"),
          dbname=os.getenv("DB_NAME", "market_data"))
TOKEN_CACHE = f"{BASE}/.dhan_token_cache.json"
SCRIP = f"{BASE}/api-scrip-master.csv"


def get_token():
    try:
        c = json.load(open(TOKEN_CACHE))
        tk = c.get("token")
        if tk:
            r = requests.get("https://api.dhan.co/v2/fundlimit",
                             headers={"access-token": tk, "client-id": os.getenv("DHAN_CLIENT_ID")}, timeout=10)
            if r.status_code == 200:
                return tk
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


def security_map():
    m = {}
    with open(SCRIP, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("SEM_EXM_EXCH_ID") == "NSE" and row.get("SEM_SEGMENT") == "E":
                m[str(row.get("SEM_TRADING_SYMBOL", "")).strip().upper()] = str(row.get("SEM_SMST_SECURITY_ID", "")).strip()
    return m


def nifty500():
    """Fetch the NIFTY-500 constituent list from NSE (symbols without .NS)."""
    try:
        r = requests.get("https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
                         headers={"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*",
                                  "Referer": "https://www.nseindia.com/"}, timeout=20)
        rows = list(csv.DictReader(io.StringIO(r.text)))
        syms = [str(x.get("Symbol", "")).strip().upper() for x in rows if x.get("Symbol")]
        if syms:
            return syms
    except Exception as e:
        log.warning(f"NIFTY-500 fetch failed ({e}); falling back to all DB symbols")
    return None


def today_candle(tk, sid, today):
    """Aggregate today's intraday 60-min bars into one daily OHLCV. Returns tuple or None."""
    try:
        r = requests.post("https://api.dhan.co/v2/charts/intraday",
                          headers={"access-token": tk},
                          json={"securityId": str(sid), "exchangeSegment": "NSE_EQ", "instrument": "EQUITY",
                                "interval": "60", "oi": False,
                                "fromDate": (today - dt.timedelta(days=1)).isoformat(),
                                "toDate": today.isoformat()}, timeout=20)
        if r.status_code != 200:
            return None
        d = r.json()
        if not d.get("open"):
            return None
        o = h = l = c = None
        v = 0
        found = False
        for i in range(len(d.get("timestamp", []))):
            if dt.datetime.fromtimestamp(d["timestamp"][i], IST).date() != today:
                continue
            hi, lo, cl = d["high"][i], d["low"][i], d["close"][i]
            if not found:
                o = d["open"][i]; h = hi; l = lo; found = True
            h = max(h, hi); l = min(l, lo); c = cl; v += d["volume"][i]
        if not found:
            return None
        return (float(o), float(h), float(l), float(c), int(v))
    except Exception:
        return None


def main():
    today = dt.datetime.now(IST).date()
    if today.weekday() >= 5:
        log.info("Weekend — no trading session today.")
        return

    tk = get_token()
    if not tk:
        log.error("No Dhan token")
        return
    smap = security_map()
    uni = nifty500()

    conn = psycopg2.connect(**DB)
    conn.autocommit = True
    cur = conn.cursor()
    if not uni:
        cur.execute("SELECT DISTINCT symbol FROM ohlcv_data")
        uni = [r[0] for r in cur.fetchall()]

    log.info(f"Same-day update for {len(uni)} symbols (date {today})")
    ts_today = dt.datetime(today.year, today.month, today.day, tzinfo=IST)
    stored = 0
    for i, sym in enumerate(uni, 1):
        sid = smap.get(sym)
        if not sid:
            continue
        cndl = today_candle(tk, sid, today)
        time.sleep(0.25)
        if not cndl:
            continue
        o, h, l, c, v = cndl
        cur.execute(
            """INSERT INTO ohlcv_data (symbol, time, open, high, low, close, volume)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (symbol, time) DO UPDATE
               SET open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                   close=EXCLUDED.close, volume=EXCLUDED.volume""",
            (sym, ts_today, o, h, l, c, v))
        stored += 1
        if i % 100 == 0:
            log.info(f"  {i}/{len(uni)} ({stored} stored)")

    log.info(f"✅ Same-day update complete: {stored} candles stored for {today}")
    conn.close()


if __name__ == "__main__":
    main()
