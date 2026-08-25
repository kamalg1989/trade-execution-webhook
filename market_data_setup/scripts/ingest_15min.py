#!/usr/bin/env python3
"""Ingest 15-min OHLCV from Dhan intraday API for NIFTY50 into ohlcv_15min.

Resumable: per symbol, fetches only ranges older than existing min(time) and
newer than existing max(time); ON CONFLICT DO NOTHING absorbs overlaps.
Throttled ~3s/call (DH-904); retries 904 with backoff, aborts loudly on any
other error. Run on VPS:
  nohup venv/bin/python market_data_setup/scripts/ingest_15min.py \
        >> /root/ingest_15min.log 2>&1 &
"""
import json, os, sys, time, datetime as dt
import requests, psycopg2
from psycopg2.extras import execute_values

REPO = "/root/trade-execution-webhook"
def _db_password():
    vals = [l.split("=",1)[1].strip() for l in open(f"{REPO}/.env")
            if l.startswith("DB_PASSWORD")]
    return vals

def db_connect():
    last = None
    for pw in _db_password():
        try:
            return psycopg2.connect(dbname="market_data",
                user="market_data_user", host="localhost", password=pw)
        except psycopg2.OperationalError as e:
            last = e
    sys.exit(f"ABORT: DB auth failed: {last}")
START_DATE = dt.date(2020, 9, 1)   # verified available from ~2020-09-28
CHUNK_DAYS = 89
THROTTLE_S = 3.0
URL = "https://api.dhan.co/v2/charts/intraday"
INTERVAL = "15"

def _load_env():
    for line in open(f"{REPO}/.env"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)

def headers(force_refresh=False):
    _load_env()
    sys.path.insert(0, REPO)
    from web_api.dhan_client import get_token
    return {"access-token": get_token(force_refresh=force_refresh),
            "client-id": os.environ["DHAN_CLIENT_ID"],
            "Content-Type": "application/json"}

def fetch_chunk(H, sec_id, frm, to):
    body = {"securityId": str(sec_id), "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY", "interval": INTERVAL,
            "fromDate": frm.isoformat(), "toDate": to.isoformat()}
    for attempt in range(5):
        r = requests.post(URL, headers=H, json=body, timeout=30)
        if r.status_code == 200:
            return r.json()
        if "DH-904" in r.text:            # rate limit: back off and retry
            time.sleep(15 * (attempt + 1))
            continue
        if "DH-906" in r.text:            # token expired mid-run
            H.clear(); H.update(headers(force_refresh=True))
            continue
        if r.status_code in (400, 404) and "DH-905" in r.text:
            return None                    # no data for range
        sys.exit(f"ABORT: {sec_id} {frm}->{to} HTTP {r.status_code}: {r.text[:200]}")
    sys.exit(f"ABORT: rate-limited 5x on {sec_id} {frm}->{to}")

def rows_from(sym, d):
    ts = d.get("timestamp") or []
    if not ts:
        return []
    o, h, l, c, v = d["open"], d["high"], d["low"], d["close"], d["volume"]
    return [(sym, dt.datetime.fromtimestamp(ts[i], dt.timezone.utc),
             o[i], h[i], l[i], c[i], int(v[i])) for i in range(len(ts))]

def ingest_range(cur, H, sym, sec_id, frm_date, to_date):
    total = 0
    to = to_date
    while to >= frm_date:
        frm = max(frm_date, to - dt.timedelta(days=CHUNK_DAYS))
        d = fetch_chunk(H, sec_id, frm, to)
        rows = rows_from(sym, d) if d else []
        if rows:
            execute_values(cur,
                "INSERT INTO ohlcv_15min (symbol,time,open,high,low,close,volume) "
                "VALUES %s ON CONFLICT DO NOTHING", rows)
            total += len(rows)
        elif to < to_date:
            break            # walked past start of available history
        to = frm - dt.timedelta(days=1)
        time.sleep(THROTTLE_S)
    return total

def main():
    H = headers()
    conn = db_connect()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS ohlcv_15min (
        symbol text NOT NULL, time timestamptz NOT NULL,
        open numeric, high numeric, low numeric, close numeric, volume bigint,
        PRIMARY KEY (symbol, time))""")
    cur.execute("""SELECT im.symbol, sm.dhan_security_id
                   FROM index_membership im JOIN symbols_meta sm USING (symbol)
                   WHERE im.index_name='NIFTY50'
                     AND sm.dhan_security_id IS NOT NULL ORDER BY 1""")
    universe = cur.fetchall()
    if len(universe) < 45:
        sys.exit(f"ABORT: NIFTY50 universe only {len(universe)} symbols")
    today = dt.date.today()
    print(f"[{dt.datetime.now()}] {len(universe)} symbols, target {START_DATE}->{today}", flush=True)
    for i, (sym, sec_id) in enumerate(universe, 1):
        cur.execute("SELECT min(time)::date, max(time)::date FROM ohlcv_15min "
                    "WHERE symbol=%s", (sym,))
        mn, mx = cur.fetchone()
        n = 0
        if mn is None:
            n = ingest_range(cur, H, sym, sec_id, START_DATE, today)
        else:
            if mx < today:
                n += ingest_range(cur, H, sym, sec_id, mx, today)
            if mn > START_DATE + dt.timedelta(days=7):
                n += ingest_range(cur, H, sym, sec_id, START_DATE,
                                  mn - dt.timedelta(days=1))
        print(f"[{i}/{len(universe)}] {sym}: +{n} rows", flush=True)
    cur.execute("SELECT count(*), count(DISTINCT symbol), min(time), max(time) "
                "FROM ohlcv_15min")
    print("DONE:", cur.fetchone(), flush=True)

if __name__ == "__main__":
    main()
