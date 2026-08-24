"""Paper-trading v2 read API — the three preset books, for the UI comparison
panel. Read-only over trading_platform.paper2_* tables; connects lazily with
its own DSN because the app's main pool is the market_data database."""
from __future__ import annotations

import os
import time

import asyncpg
from fastapi import APIRouter, HTTPException

router = APIRouter()

TRADE_DSN = os.getenv("DATABASE_URL",
                      "postgresql://postgres:postgres@localhost:5432/trading_platform")

_CACHE: dict = {"at": 0.0, "data": None}
BOOK_META = {
    "recommended": {"label": "Recommended (#909)", "color": "#34d399"},
    "aggressive":  {"label": "Aggressive (#1062)", "color": "#fbbf24"},
    "combo":       {"label": "Combo (#1079)",      "color": "#f472b6"},
    "etf_blend":   {"label": "Combo 80/20 + ETF",   "color": "#38bdf8"},
}


@router.get("/paper/summary")
async def paper_summary():
    now = time.monotonic()
    if _CACHE["data"] is not None and now - _CACHE["at"] < 60:
        return _CACHE["data"]
    try:
        con = await asyncpg.connect(TRADE_DSN, timeout=10)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"paper DB unavailable: {e}")
    try:
        books = []
        for book, meta in BOOK_META.items():
            eq = [dict(d=str(r["d"]), equity=float(r["equity"]),
                       dd=float(r["drawdown_pct"] or 0), nOpen=r["n_open"])
                  for r in await con.fetch(
                      "SELECT d, equity, drawdown_pct, n_open FROM paper2_equity "
                      "WHERE book=$1 ORDER BY d", book)]
            row = await con.fetchrow(
                "SELECT start_capital, cash_credit, created_at FROM paper2_books "
                "WHERE book=$1", book)
            pos = [dict(symbol=p["symbol"], entryDate=str(p["entry_date"]),
                        entryPrice=float(p["entry_price"]), qty=p["quantity"],
                        rank=p["entry_rank"],
                        slippageBps=float(p["slippage_bps"]) if p["slippage_bps"] is not None else None)
                   for p in await con.fetch(
                       "SELECT * FROM paper2_positions WHERE book=$1 AND status='OPEN' "
                       "ORDER BY entry_rank NULLS LAST", book)]
            closed = await con.fetchrow(
                "SELECT count(*) AS n, COALESCE(sum(realized_pnl),0) AS pnl, "
                "count(*) FILTER (WHERE realized_pnl > 0) AS wins "
                "FROM paper2_positions WHERE book=$1 AND status='CLOSED'", book)
            last_reb = await con.fetchval(
                "SELECT max(rebalance_date) FROM paper2_rebalance WHERE book=$1", book)
            start = float(row["start_capital"]) if row else 400000.0
            latest = eq[-1] if eq else None
            books.append({
                "book": book, **meta,
                "startCapital": start,
                "startedAt": str(row["created_at"].date()) if row else None,
                "equity": latest["equity"] if latest else start,
                "returnPct": round((latest["equity"] / start - 1) * 100, 2) if latest else 0.0,
                "ddPct": latest["dd"] if latest else 0.0,
                "nOpen": latest["nOpen"] if latest else 0,
                "nClosed": closed["n"] if closed else 0,
                "winRate": round(100 * closed["wins"] / closed["n"], 1) if closed and closed["n"] else None,
                "realizedPnl": float(closed["pnl"]) if closed else 0.0,
                "cashCredit": float(row["cash_credit"]) if row else 0.0,
                "lastRebalance": str(last_reb) if last_reb else None,
                "equityCurve": eq,
                "openPositions": pos,
            })
        result = {"books": books}
        _CACHE.update(at=now, data=result)
        return result
    finally:
        await con.close()
