"""Harvest NSE's quarterly results-filing calendar into earnings_filings.

Fetches filing METADATA (symbol + broadcast date + period covered) for a date
range, in chunks, and upserts it. See sql/016 for why dates-only, and for the
look-ahead caveat that governs how this data may legitimately be used.

Verified reachable history: a probe returned 719 filings for Jul-Aug 2016 and
2,602 for Jul-Aug 2019, so an 11-year harvest is feasible.

Politeness: NSE throttles aggressive clients. Chunks are ~45 days with a delay
between requests, and failures are retried once then skipped rather than
hammered — a missing chunk degrades the filter for those dates, it does not
corrupt anything.

Usage:
    python3 -m backtest.harvest_earnings --start 2015-06-01 --end 2026-08-10
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import date, datetime, timedelta

sys.path.insert(0, "/root/trade-execution-webhook")

CHUNK_DAYS = 45
DELAY_SEC = 2.5


def _parse_dt(s: str | None):
    """NSE returns dates like '01-Apr-2019' (and occasionally with a time)."""
    if not s:
        return None
    s = str(s).strip().split(" ")[0]
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def fetch_range(start: date, end: date) -> list[dict]:
    from nse import NSE

    out: list[dict] = []
    with NSE(download_folder="/root/nsedl", server=True) as n:
        cur = start
        while cur <= end:
            chunk_end = min(cur + timedelta(days=CHUNK_DAYS), end)
            rows = None
            for attempt in (1, 2):
                try:
                    rows = n.financial_results(
                        segment="equities", period="quarterly",
                        from_date=datetime.combine(cur, datetime.min.time()),
                        to_date=datetime.combine(chunk_end, datetime.min.time()))
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f"  {cur}..{chunk_end}: FAILED {type(e).__name__} {str(e)[:80]}",
                              flush=True)
                    else:
                        time.sleep(5)
            if rows:
                kept = 0
                for r in rows:
                    bc = _parse_dt(r.get("broadCastDate")) or _parse_dt(r.get("filingDate"))
                    sym = (r.get("symbol") or "").strip().upper()
                    if not bc or not sym:
                        continue
                    out.append({
                        "symbol": sym, "broadcast_date": bc,
                        "period_from": _parse_dt(r.get("fromDate")),
                        "period_to": _parse_dt(r.get("toDate")),
                        "relating_to": (r.get("relatingTo") or "")[:64] or None,
                        "audited": (r.get("audited") or "")[:32] or None,
                        "consolidated": (r.get("consolidated") or "")[:32] or None,
                        # sql/018 — the XBRL document is what actually carries
                        # revenue/profit/EPS; without this link the filing row
                        # is just a date and the fundamentals backfill has
                        # nothing to fetch.
                        "xbrl_url": (r.get("xbrl") or None),
                        "isin": (r.get("isin") or "")[:24] or None,
                        "seq_number": (r.get("seqNumber") or "")[:32] or None,
                    })
                    kept += 1
                print(f"  {cur}..{chunk_end}: {len(rows)} rows, {kept} usable", flush=True)
            cur = chunk_end + timedelta(days=1)
            time.sleep(DELAY_SEC)
    return out


async def store(rows: list[dict]) -> int:
    from app.db import create_pool
    if not rows:
        return 0
    pool = await create_pool()
    try:
        # Dedupe in-process first: overlapping chunks and standalone/consolidated
        # pairs mean the same natural key can appear more than once per batch,
        # and executemany would otherwise fight the unique index row by row.
        seen, uniq = set(), []
        for r in rows:
            k = (r["symbol"], r["broadcast_date"], r["period_to"], r["consolidated"] or "")
            if k in seen:
                continue
            seen.add(k)
            uniq.append(r)
        # DO UPDATE rather than DO NOTHING on the xbrl columns: the first
        # harvest pass predated sql/018 and stored dates only, so re-running
        # must be able to backfill xbrl_url onto those existing rows instead of
        # silently skipping them as duplicates.
        await pool.executemany(
            """
            INSERT INTO earnings_filings
              (symbol, broadcast_date, period_from, period_to, relating_to,
               audited, consolidated, xbrl_url, isin, seq_number)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (symbol, broadcast_date, period_to, COALESCE(consolidated, ''))
            DO UPDATE SET xbrl_url   = COALESCE(EXCLUDED.xbrl_url, earnings_filings.xbrl_url),
                          isin       = COALESCE(EXCLUDED.isin, earnings_filings.isin),
                          seq_number = COALESCE(EXCLUDED.seq_number, earnings_filings.seq_number)
            """,
            [(r["symbol"], r["broadcast_date"], r["period_from"], r["period_to"],
              r["relating_to"], r["audited"], r["consolidated"],
              r["xbrl_url"], r["isin"], r["seq_number"]) for r in uniq],
        )
        row = await pool.fetchrow("SELECT count(*) c, min(broadcast_date) lo, max(broadcast_date) hi "
                                  "FROM earnings_filings")
        print(f"stored: table now has {row['c']} filings, {row['lo']} .. {row['hi']}", flush=True)
        return len(uniq)
    finally:
        await pool.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    a = ap.parse_args()
    start = datetime.strptime(a.start, "%Y-%m-%d").date()
    end = datetime.strptime(a.end, "%Y-%m-%d").date()
    print(f"harvesting NSE quarterly filings {start} .. {end}", flush=True)
    rows = fetch_range(start, end)
    print(f"fetched {len(rows)} filing records", flush=True)
    asyncio.run(store(rows))
    print("HARVEST DONE", flush=True)


if __name__ == "__main__":
    main()
