#!/usr/bin/env python3
"""Reconstruct price history for delisted NSE symbols from daily bhavcopy archives.

Why: ohlcv_data contains only companies that survived to 2026. NSE's own delisted
list shows 269 wipeout-type delistings inside the 2011-2026 backtest window, none
of which are in our universe (SURVIVORSHIP_QUANTIFIED.md, 2026-08-19). Every CAGR
this project has produced is therefore optimistic by an estimated 3-7 points.

A bhavcopy is a snapshot of everything that traded on a given day, so a company
delisted in 2016 still appears in every bhavcopy from 2011 to 2016. Downloading
the archive rebuilds their history from the official source, for free.

Rows are written with data_source='nse_bhavcopy' so they stay distinguishable
from the Dhan-sourced panel.

Usage:
  python backfill_delisted.py --probe 2016-01-04      # test one date, write nothing
  python backfill_delisted.py --start 2011-01-01 --end 2026-08-24
  python backfill_delisted.py --resume                # continue from the state file
"""
import argparse, asyncio, csv, io, os, sys, time, zipfile
from datetime import date, timedelta
from pathlib import Path
import requests, asyncpg
from dotenv import load_dotenv

for f in (Path('/root/trade-execution-webhook/.env'), Path.home()/'.env'):
    if f.exists():
        load_dotenv(f); break

DSN = os.getenv("MARKET_DSN", "postgresql://postgres:postgres@localhost:5432/market_data")
SYMS_CSV = "/root/trade-execution-webhook/missing_delisted_symbols.csv"
STATE = Path("/root/.bhavcopy_state")
MONTHS = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HDRS = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/"}


def urls_for(d: date):
    """NSE changed format around Jul-2024; try both, newest style first for recent dates."""
    old = (f"https://nsearchives.nseindia.com/content/historical/EQUITIES/"
           f"{d.year}/{MONTHS[d.month-1]}/cm{d.day:02d}{MONTHS[d.month-1]}{d.year}bhav.csv.zip")
    new = (f"https://nsearchives.nseindia.com/content/cm/"
           f"BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip")
    return [new, old] if d >= date(2024, 7, 1) else [old, new]


def fetch_day(sess, d: date):
    """-> list of dicts, or None if the day has no bhavcopy (holiday/weekend)."""
    for url in urls_for(d):
        for attempt in range(3):
            try:
                r = sess.get(url, headers=HDRS, timeout=30)
            except requests.RequestException:
                time.sleep(2 * (attempt + 1)); continue
            if r.status_code == 404:
                break                       # wrong format for this date, try the other
            if r.status_code != 200:
                time.sleep(2 * (attempt + 1)); continue
            try:
                z = zipfile.ZipFile(io.BytesIO(r.content))
            except zipfile.BadZipFile:
                break
            name = z.namelist()[0]
            return list(csv.DictReader(io.TextIOWrapper(z.open(name), "utf-8")))
    return None


def extract(rows, wanted):
    """Normalise old and UDiFF bhavcopy layouts to (symbol, o, h, l, c, vol)."""
    out = []
    for r in rows:
        r = { (k or "").strip(): (v.strip() if isinstance(v, str) else v)
              for k, v in r.items() }
        sym = r.get("SYMBOL") or r.get("TckrSymb")
        ser = (r.get("SERIES") or r.get("SctySrs") or "").strip()
        if not sym or sym not in wanted or ser != "EQ":
            continue
        try:
            o = float(r.get("OPEN") or r.get("OpnPric"))
            h = float(r.get("HIGH") or r.get("HghPric"))
            lo = float(r.get("LOW") or r.get("LwPric"))
            c = float(r.get("CLOSE") or r.get("ClsPric"))
            v = int(float(r.get("TOTTRDQTY") or r.get("TtlTradgVol") or 0))
        except (TypeError, ValueError):
            continue
        if c <= 0:
            continue
        out.append((sym, o, h, lo, c, v))
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start"); ap.add_argument("--end")
    ap.add_argument("--probe"); ap.add_argument("--resume", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.35)
    a = ap.parse_args()

    wanted = {r["symbol"].strip() for r in csv.DictReader(open(SYMS_CSV)) if r.get("symbol")}
    print(f"{len(wanted)} delisted symbols to recover", flush=True)
    sess = requests.Session()
    try:
        sess.get("https://www.nseindia.com", headers=HDRS, timeout=15)   # cookie
    except requests.RequestException:
        pass

    if a.probe:
        d = date.fromisoformat(a.probe)
        rows = fetch_day(sess, d)
        if rows is None:
            print(f"PROBE {d}: no bhavcopy (holiday, or both URL formats failed)"); return
        hits = extract(rows, wanted)
        print(f"PROBE {d}: bhavcopy has {len(rows)} rows; {len(hits)} match the delisted list")
        for h in hits[:8]:
            print("   ", h)
        return

    start = date.fromisoformat(a.start) if a.start else date(2011, 1, 1)
    end = date.fromisoformat(a.end) if a.end else date.today()
    done = set()
    if a.resume and STATE.exists():
        done = {l.strip() for l in STATE.read_text().splitlines() if l.strip()}
        print(f"resuming; {len(done)} dates already processed", flush=True)

    pool = await asyncpg.create_pool(dsn=DSN, min_size=1, max_size=2)
    total = 0
    d = start
    with STATE.open("a") as state:
        while d <= end:
            if d.weekday() >= 5 or str(d) in done:
                d += timedelta(days=1); continue
            rows = fetch_day(sess, d)
            if rows is not None:
                hits = extract(rows, wanted)
                if hits:
                    await pool.executemany(
                        "INSERT INTO ohlcv_data (symbol, time, open, high, low, close, volume, data_source) "
                        "VALUES ($1,$2,$3,$4,$5,$6,$7,'nse_bhavcopy') ON CONFLICT DO NOTHING",
                        [(s, d, o, h, l, c, v) for (s, o, h, l, c, v) in hits])
                    total += len(hits)
                if d.day <= 3 or len(hits) == 0:
                    print(f"  {d}: {len(hits)} delisted rows (running total {total})", flush=True)
            state.write(f"{d}\n"); state.flush()
            time.sleep(a.sleep)
            d += timedelta(days=1)

    n = await pool.fetchval(
        "SELECT count(*) FROM ohlcv_data WHERE data_source='nse_bhavcopy'")
    syms = await pool.fetchval(
        "SELECT count(DISTINCT symbol) FROM ohlcv_data WHERE data_source='nse_bhavcopy'")
    print(f"\nDONE. inserted {total} this run · table now holds {n} bhavcopy rows "
          f"across {syms} recovered symbols", flush=True)
    await pool.close()

asyncio.run(main())
