#!/usr/bin/env python3
"""Targeted ETF history backfill (2011 -> current earliest bar).

Every ETF in ohlcv_data starts 2019-01-01 while equities go back to 2011,
because the ETFs were not in the universe when the original 15-year historical
ingest ran. That capped the INDEX_TF sleeve bake-off at 7.6 years.

Safety: ONLY inserts candles dated before the symbol's current earliest bar,
with ON CONFLICT DO NOTHING. No existing row is updated or deleted.
"""
import asyncio, importlib.util
from datetime import datetime
import asyncpg

SCRIPTS = "/root/trade-execution-webhook/market_data_setup/scripts"
spec = importlib.util.spec_from_file_location("ingest_ohlcv", SCRIPTS + "/ingest_ohlcv.py")
ing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ing)

ETFS = ["GOLDBEES", "SETFGOLD", "JUNIORBEES", "NIFTYBEES", "BANKBEES",
        "CPSEETF", "NV20BEES", "MIDSELIETF", "BSE500IETF"]
START_YEAR = 2011


async def main():
    token = ing.get_dhan_token()
    pool = await asyncpg.create_pool(host=ing.DB_HOST, port=ing.DB_PORT,
                                     user=ing.DB_USER, password=ing.DB_PASSWORD,
                                     database=ing.DB_NAME, min_size=1, max_size=2)
    rows = await pool.fetch(
        "SELECT m.symbol, m.dhan_security_id::text AS sid, "
        "(SELECT min(o.time)::date FROM ohlcv_data o WHERE o.symbol = m.symbol) AS cur_min "
        "FROM symbols_meta m WHERE m.symbol = ANY($1)", ETFS)
    grand = 0
    for r in rows:
        sym, sid, cur_min = r["symbol"], r["sid"], r["cur_min"]
        print("\n=== %s (dhan %s) existing history starts %s" % (sym, sid, cur_min), flush=True)
        added = 0
        for year in range(START_YEAR, (cur_min.year if cur_min else 2027) + 1):
            candles = await ing.fetch_historical_ohlcv(
                token, sid, "%d-01-01" % year, "%d-12-31" % year)
            if not candles:
                print("  %d: no data" % year, flush=True)
                continue
            batch = []
            for c in candles:
                ts = c["timestamp"]
                if isinstance(ts, (int, float)):
                    ts = datetime.fromtimestamp(ts / (1000 if ts > 1e11 else 1))
                else:
                    ts = datetime.fromisoformat(str(ts).replace("Z", ""))
                if cur_min and ts.date() >= cur_min:
                    continue
                batch.append((sym, ts, float(c["open"]), float(c["high"]),
                              float(c["low"]), float(c["close"]), int(c["volume"] or 0)))
            if not batch:
                print("  %d: nothing older than %s" % (year, cur_min), flush=True)
                continue
            await pool.executemany(
                "INSERT INTO ohlcv_data (symbol, time, open, high, low, close, volume) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING", batch)
            added += len(batch)
            print("  %d: +%d" % (year, len(batch)), flush=True)
        grand += added
        new_min = await pool.fetchval("SELECT min(time)::date FROM ohlcv_data WHERE symbol=$1", sym)
        print("  --> %s: +%d rows, history now starts %s" % (sym, added, new_min), flush=True)
    print("\nTOTAL ROWS ADDED: %d" % grand, flush=True)
    await pool.close()

asyncio.run(main())
