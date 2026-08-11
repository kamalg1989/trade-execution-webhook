"""Multi-day batch: download + parse every filing's XBRL into
earnings_fundamentals, giving genuine point-in-time quarterly fundamentals.

Scale: ~113k filings in earnings_filings, XBRL documents ~25 KB each. At a
polite request rate this is tens of hours of wall-clock, so it is built to run
detached for days:

  * RESUMABLE — each pass selects only filings that have no row yet
    (LEFT JOIN), so killing and relaunching never redoes work and never
    double-inserts. Safe to run from cron/systemd.
  * PARSE-AND-DISCARD — the XML is deleted immediately after parsing. Hoarding
    113k files would be ~3 GB for no benefit; only the extracted numbers matter.
  * FAILURES ARE RECORDED, NOT RETRIED FOREVER — a filing whose XBRL is
    missing/corrupt gets a row with parse_status='error:...' so the next pass
    skips it instead of hammering a dead URL. Re-runnable by deleting those
    rows if NSE later fixes them.
  * RATE-LIMITED with backoff, because NSE throttles bulk clients. The whole
    point of a days-long batch is that it can afford to be slow and polite.

Usage (typical: run under nohup/setsid and check progress with --status):
    python3 -m backtest.backfill_fundamentals --limit 5000
    python3 -m backtest.backfill_fundamentals --status
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, "/root/trade-execution-webhook")

DELAY_SEC = 1.2          # between XBRL downloads
BACKOFF_SEC = 20         # after a network failure
DOWNLOAD_DIR = Path("/root/nsedl")

# Ind-AS XBRL element -> our column. Local names only (namespace prefixes vary
# between the "Old"/"New" formats and between taxonomy versions, so matching on
# the local name is far more robust than binding to a prefix).
FIELD_TAGS = {
    "revenue": ("RevenueFromOperations",),
    "other_income": ("OtherIncome",),
    "net_profit": ("ProfitLossForPeriod",),
    "profit_continuing": ("ProfitLossForPeriodFromContinuingOperations",),
    "eps_basic": ("BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
                   "BasicEarningsLossPerShareFromContinuingOperations",
                   "BasicEarningsPerShare"),
    "eps_diluted": ("DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
                     "DilutedEarningsLossPerShareFromContinuingOperations",
                     "DilutedEarningsPerShare"),
}


def _first_number(txt: str, local_names: tuple[str, ...]) -> float | None:
    """First numeric value for any of these XBRL local element names.

    Deliberately takes the FIRST occurrence: these documents repeat elements
    per context (current quarter, year-ago quarter, year-to-date, segments),
    and in the sampled Ind-AS layout the primary current-period context comes
    first. Segment breakdowns carry non-numeric text (e.g. SegmentRevenue =
    'Abrasives'), so anything unparseable is skipped rather than trusted.
    """
    for name in local_names:
        # The namespace prefix MUST allow hyphens and dots: these documents use
        # `in-bse-fin:RevenueFromOperations`. An earlier version of this pattern
        # only accepted [A-Za-z0-9_]+ as the prefix and therefore matched
        # nothing at all, silently recording every filing as 'no_data'.
        for m in re.finditer(
            r"<(?:[A-Za-z0-9_.\-]+:)?" + re.escape(name) + r"\b[^>]*>\s*([^<]+?)\s*<", txt):
            raw = m.group(1).strip()
            try:
                return float(raw)
            except ValueError:
                continue
    return None


def parse_xbrl(path: Path) -> dict:
    txt = path.read_text(errors="ignore")
    out = {k: _first_number(txt, tags) for k, tags in FIELD_TAGS.items()}
    out["parse_status"] = "ok" if any(v is not None for v in out.values()) else "no_data"
    return out


async def fetch_pending(pool, limit: int):
    return await pool.fetch(
        """
        SELECT f.symbol, f.broadcast_date, f.period_from, f.period_to,
               f.consolidated, f.xbrl_url
        FROM earnings_filings f
        LEFT JOIN earnings_fundamentals u
          ON u.symbol = f.symbol AND u.period_to = f.period_to
         AND u.broadcast_date = f.broadcast_date
         AND COALESCE(u.consolidated,'') = COALESCE(f.consolidated,'')
        -- Must end in .xml. NSE stores a placeholder '-' as the document name
        -- for filings with no XBRL, which yields the valid-looking but dead
        -- URL '.../corporate/xbrl/-'. Those are the majority before mid-2018
        -- (XBRL filing simply was not in force yet), and each one would
        -- otherwise burn a download attempt plus a 20s backoff.
        WHERE f.xbrl_url LIKE '%.xml' AND u.id IS NULL
        -- Oldest first: the usable history is 2011-2024, while 2025-26 is
        -- sparse, so this front-loads the years the backtest can actually use.
        ORDER BY f.broadcast_date ASC
        LIMIT $1
        """,
        limit,
    )


async def store(pool, row, parsed: dict):
    await pool.execute(
        """
        INSERT INTO earnings_fundamentals
          (symbol, period_to, period_from, broadcast_date, consolidated,
           revenue, other_income, net_profit, profit_continuing,
           eps_basic, eps_diluted, xbrl_url, parse_status)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
        ON CONFLICT DO NOTHING
        """,
        row["symbol"], row["period_to"], row["period_from"], row["broadcast_date"],
        row["consolidated"], parsed.get("revenue"), parsed.get("other_income"),
        parsed.get("net_profit"), parsed.get("profit_continuing"),
        parsed.get("eps_basic"), parsed.get("eps_diluted"),
        row["xbrl_url"], parsed["parse_status"],
    )


async def show_status(pool):
    r = await pool.fetchrow(
        """
        SELECT (SELECT count(*) FROM earnings_filings WHERE xbrl_url LIKE '%.xml') total,
               (SELECT count(*) FROM earnings_fundamentals) done,
               (SELECT count(*) FROM earnings_fundamentals WHERE parse_status='ok') ok,
               (SELECT count(*) FROM earnings_fundamentals WHERE parse_status<>'ok') bad,
               (SELECT count(DISTINCT symbol) FROM earnings_fundamentals WHERE parse_status='ok') syms,
               (SELECT min(period_to) FROM earnings_fundamentals WHERE parse_status='ok') lo,
               (SELECT max(period_to) FROM earnings_fundamentals WHERE parse_status='ok') hi
        """)
    pct = (r["done"] / r["total"] * 100) if r["total"] else 0
    print(f"fundamentals: {r['done']}/{r['total']} filings processed ({pct:.1f}%)  "
          f"ok={r['ok']} failed={r['bad']}  symbols={r['syms']}  periods {r['lo']}..{r['hi']}",
          flush=True)


async def run(limit: int):
    from app.db import create_pool
    from nse import NSE

    pool = await create_pool()
    try:
        pending = await fetch_pending(pool, limit)
        print(f"pending this pass: {len(pending)}", flush=True)
        ok = failed = 0
        with NSE(download_folder=str(DOWNLOAD_DIR), server=True) as n:
            for i, row in enumerate(pending, 1):
                path = None
                try:
                    path = n.download_document(row["xbrl_url"])
                    parsed = parse_xbrl(path)
                    ok += 1
                except Exception as e:
                    parsed = {"parse_status": f"error:{type(e).__name__}"[:40]}
                    failed += 1
                    time.sleep(BACKOFF_SEC)
                finally:
                    # Parse-and-discard: never accumulate 113k XML files.
                    if path is not None:
                        try:
                            Path(path).unlink(missing_ok=True)
                        except OSError:
                            pass
                await store(pool, row, parsed)
                if i % 200 == 0:
                    print(f"  {i}/{len(pending)}  ok={ok} failed={failed}", flush=True)
                    await show_status(pool)
                time.sleep(DELAY_SEC)
        print(f"pass complete: ok={ok} failed={failed}", flush=True)
        await show_status(pool)
    finally:
        await pool.close()


async def status_only():
    from app.db import create_pool
    pool = await create_pool()
    try:
        await show_status(pool)
    finally:
        await pool.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5000,
                    help="filings to process this pass")
    ap.add_argument("--status", action="store_true", help="print progress and exit")
    ap.add_argument("--loop", action="store_true",
                    help="keep going pass after pass until nothing is pending")
    a = ap.parse_args()
    if a.status:
        asyncio.run(status_only())
        return
    if a.loop:
        while True:
            before = time.time()
            asyncio.run(run(a.limit))
            # If a pass finished suspiciously fast it almost certainly found
            # nothing left to do; stop rather than spin.
            if time.time() - before < 30:
                print("nothing pending — done", flush=True)
                break
    else:
        asyncio.run(run(a.limit))


if __name__ == "__main__":
    main()
