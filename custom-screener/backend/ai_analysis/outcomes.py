"""Nightly outcome tracker: forward returns for every stored AI analysis.

For each result row whose ret_60d is still NULL, compute (from the bars AFTER
analysis_date):
  ret_5d / ret_20d / ret_60d : close-to-close % return from the analysis-date close
  hit_breakout               : any high >= AI breakout level within 20 bars
  hit_stop                   : any low  <= AI stop level within 20 bars

Run nightly (after the indicator compute):
  45 18 * * * cd /root/trade-execution-webhook/custom-screener/backend && \
    /root/trade-execution-webhook/venv/bin/python -m ai_analysis.outcomes \
    >> /root/trade-execution-webhook/market_data_setup/scripts/outcomes.log 2>&1

This is what turns the screener into a research loop: win rates per model /
recommendation / IFP band become queryable facts instead of opinions.
"""
from __future__ import annotations

import asyncio
import json
import logging

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

HIT_WINDOW = 20  # bars for breakout/stop hit checks


def compute_outcome(entry_close: float, bars: list[dict],
                    breakout: float | None, stop: float | None) -> dict:
    """Pure helper: bars = forward OHLC rows (ascending, after analysis date)."""
    def ret(n: int) -> float | None:
        if entry_close and len(bars) >= n:
            return round((bars[n - 1]["close"] / entry_close - 1) * 100, 2)
        return None

    window = bars[:HIT_WINDOW]

    def hit(level: float | None, key: str, cmp) -> bool | None:
        """True once touched; False only after a full window with no touch;
        None (= still unknown) while the window is incomplete."""
        if not level or not window:
            return None
        touched = any(cmp(b[key], level) for b in window)
        if touched:
            return True
        return False if len(window) >= HIT_WINDOW else None

    return {
        "ret_5d": ret(5),
        "ret_20d": ret(20),
        "ret_60d": ret(60),
        "hit_breakout": hit(breakout, "high", lambda a, b: a >= b),
        "hit_stop": hit(stop, "low", lambda a, b: a <= b),
    }


async def run(pool) -> int:
    rows = await pool.fetch(
        """
        SELECT id, symbol, analysis_date, features, analysis
        FROM ai_analysis_results
        WHERE ret_60d IS NULL
        ORDER BY analysis_date
        """)
    updated = 0
    for row in rows:
        feats = row["features"]
        analysis = row["analysis"]
        if isinstance(feats, str):
            feats = json.loads(feats)
        if isinstance(analysis, str):
            analysis = json.loads(analysis)
        entry = ((feats or {}).get("daily") or {}).get("close")
        if not entry:
            continue
        bp = (analysis or {}).get("buy_point") or {}

        bars = await pool.fetch(
            """
            SELECT high::float, low::float, close::float
            FROM ohlcv_data
            WHERE symbol = $1 AND time::date > $2
            ORDER BY time ASC
            LIMIT 61
            """, row["symbol"], row["analysis_date"])
        if not bars:
            continue
        out = compute_outcome(float(entry), [dict(b) for b in bars],
                              bp.get("breakout_level"), bp.get("stop_level"))
        if all(v is None for v in out.values()):
            continue
        await pool.execute(
            """
            UPDATE ai_analysis_results
            SET ret_5d = COALESCE($2, ret_5d),
                ret_20d = COALESCE($3, ret_20d),
                ret_60d = COALESCE($4, ret_60d),
                hit_breakout = COALESCE($5, hit_breakout),
                hit_stop = COALESCE($6, hit_stop),
                outcomes_updated_at = NOW()
            WHERE id = $1
            """,
            row["id"], out["ret_5d"], out["ret_20d"], out["ret_60d"],
            out["hit_breakout"], out["hit_stop"])
        updated += 1
    logger.info("✅ outcomes updated for %d of %d pending rows", updated, len(rows))
    return updated


async def main():
    from app.db import create_pool
    pool = await create_pool()
    try:
        await run(pool)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
