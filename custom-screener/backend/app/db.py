"""
Read-only data access for the Custom Screener API.

The Repo abstraction keeps SQL in one place and lets tests inject an in-memory
fake (no Postgres needed). All indicator filtering/sorting happens in
``app.filtering`` — the DB only returns a whole day's slice.
"""
from __future__ import annotations

from datetime import date
from typing import Optional, Protocol

from . import config

# Columns returned to the API layer (superset of what the client sees)
_ROW_COLS = """
    symbol, indicator_date, close, turnover_1m_avg_cr, volume_1m_avg,
    ema_10, ema_21, ema_50, sma_50, sma_200,
    dist_ema_10_pct, dist_ema_21_pct, dist_ema_50_pct, dist_sma_50_pct, dist_sma_200_pct,
    ma_aligned,
    price_52w_high, price_52w_low, dist_52w_high_pct, dist_52w_low_pct,
    pct_chg_1d, pct_chg_5d, pct_chg_1m, pct_chg_3m, pct_chg_6m, pct_chg_1y,
    atr_14, atr_pct, base_range_20d_pct, dist_20d_high_pct,
    vol_ratio_1d, vol_dryup_ratio, prior_upmove_pct, giveback_pct,
    ifp_score, updown_vol_ratio, obv_slope,
    bars_available
"""


class Repo(Protocol):
    async def latest_complete_date(self) -> Optional[date]: ...
    async def day_slice(self, d: date) -> list[dict]: ...
    async def snapshot(self, d: date) -> Optional[dict]: ...
    async def historical(self, symbol: str, frm: date, to: date, limit: int) -> list[dict]: ...
    async def ohlcv_tail(self, symbols: list[str], upto: date, bars: int) -> dict: ...


class PgRepo:
    """asyncpg-backed repository."""

    def __init__(self, pool):
        self.pool = pool

    async def latest_complete_date(self) -> Optional[date]:
        async with self.pool.acquire() as con:
            row = await con.fetchrow(
                "SELECT snapshot_date FROM market_snapshot "
                "WHERE is_complete = TRUE ORDER BY snapshot_date DESC LIMIT 1"
            )
            return row["snapshot_date"] if row else None

    async def day_slice(self, d: date) -> list[dict]:
        async with self.pool.acquire() as con:
            rows = await con.fetch(
                f"SELECT {_ROW_COLS} FROM stock_indicators WHERE indicator_date = $1", d
            )
            return [_to_float_dict(r) for r in rows]

    async def snapshot(self, d: date) -> Optional[dict]:
        async with self.pool.acquire() as con:
            row = await con.fetchrow(
                "SELECT * FROM market_snapshot WHERE snapshot_date = $1", d
            )
            return dict(row) if row else None

    async def historical(self, symbol: str, frm: date, to: date, limit: int) -> list[dict]:
        async with self.pool.acquire() as con:
            rows = await con.fetch(
                f"SELECT {_ROW_COLS} FROM stock_indicators "
                "WHERE symbol = $1 AND indicator_date BETWEEN $2 AND $3 "
                "ORDER BY indicator_date ASC LIMIT $4",
                symbol, frm, to, limit,
            )
            return [_to_float_dict(r) for r in rows]

    async def ohlcv_tail(self, symbols: list[str], upto: date, bars: int) -> dict:
        """Last `bars` OHLCV rows per symbol up to `upto`, grouped by symbol.
        Used by the on-demand tunable IFP endpoint (operates on a filtered subset)."""
        async with self.pool.acquire() as con:
            # window per symbol via row_number, then keep the last `bars`
            rows = await con.fetch(
                """
                SELECT symbol, time, open, high, low, close, volume FROM (
                  SELECT symbol, time, open, high, low, close, volume,
                         row_number() OVER (PARTITION BY symbol ORDER BY time DESC) AS rn
                  FROM ohlcv_data
                  WHERE symbol = ANY($1) AND time::date <= $2
                ) t WHERE rn <= $3
                ORDER BY symbol, time ASC
                """,
                symbols, upto, bars,
            )
        out: dict[str, list] = {}
        for r in rows:
            out.setdefault(r["symbol"], []).append({
                "time": r["time"], "open": float(r["open"]), "high": float(r["high"]),
                "low": float(r["low"]), "close": float(r["close"]), "volume": float(r["volume"]),
            })
        return out


def _to_float_dict(r) -> dict:
    d = dict(r)
    for k, v in d.items():
        # asyncpg returns Decimal for NUMERIC; normalize to float/None
        if v is not None and hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
            d[k] = float(v)
    return d


async def create_pool():
    import asyncpg
    return await asyncpg.create_pool(
        host=config.DB_HOST, port=config.DB_PORT, user=config.DB_USER,
        password=config.DB_PASSWORD, database=config.DB_NAME,
        min_size=1, max_size=5,
    )
