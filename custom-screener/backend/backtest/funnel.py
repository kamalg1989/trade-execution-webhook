"""Day-by-day funnel replay.

The liquidity/technical/base-quality/IFP gates are plain SQL against
stock_indicators (already vectorized in exact parity with screen_gpt.py —
see custom-screener/backend/compute/indicators.py's docstring). Only the two
things NOT precomputed there — base-stage classification and entry-technique
detection, both of which need the raw OHLCV bars, not just that day's
snapshot — fall through to the real screen_gpt.py functions for perfect
parity with production (imported directly, not reimplemented).
"""
from __future__ import annotations

import sys
from datetime import date

import pandas as pd

sys.path.insert(0, "/root/trade-execution-webhook")
import screen_gpt  # noqa: E402  — real production funnel/entry/sizing logic

GATE_SQL = """
    SELECT symbol, close, base_range_20d_pct, ifp_score
    FROM stock_indicators
    WHERE indicator_date = $1
      AND turnover_1m_avg_cr >= $2
      AND close > sma_200 AND close > ema_50
      AND base_range_20d_pct < $3
      AND vol_ratio_1d > $4
      AND prior_upmove_pct >= $5
      AND giveback_pct <= $6
      AND vol_dryup_ratio <= $7
      AND dist_20d_high_pct >= $8
      AND ifp_score >= $9
"""


async def funnel_survivors(pool, d: date) -> list[dict]:
    """Day-D funnel survivors straight from stock_indicators — liquidity,
    technical, base-quality and IFP gates, thresholds pulled live from
    screen_gpt's own constants so this can never drift out of parity."""
    rows = await pool.fetch(
        GATE_SQL, d,
        screen_gpt.MIN_DAILY_TURNOVER / 1e7,
        screen_gpt.TECH_MAX_BASE_RANGE * 100,
        screen_gpt.TECH_VOL_MULT,
        screen_gpt.BASE_MIN_PRIOR_UPMOVE_PCT * 100,
        screen_gpt.BASE_MAX_GIVEBACK_PCT * 100,
        screen_gpt.BASE_VOL_DRYUP_MAX_RATIO,
        -screen_gpt.NEAR_BREAKOUT_MAX_DISTANCE * 100,
        screen_gpt.IFP_MIN_SCORE,
    )
    return [dict(r) for r in rows]


async def load_ohlcv_frame(pool, symbol: str, upto: date, bars: int = 400) -> pd.DataFrame | None:
    """Trailing OHLCV window ending on `upto`, shaped exactly like
    screen_gpt.fetch_from_db()'s output (Open/High/Low/Close/Volume,
    datetime index) so screen_gpt's functions work on it unmodified."""
    rows = await pool.fetch(
        """
        SELECT time, open, high, low, close, volume FROM (
          SELECT time, open, high, low, close, volume,
                 row_number() OVER (ORDER BY time DESC) AS rn
          FROM ohlcv_data WHERE symbol = $1 AND time::date <= $2
        ) t WHERE rn <= $3 ORDER BY time ASC
        """,
        symbol, upto, bars,
    )
    if len(rows) < 50:
        return None
    df = pd.DataFrame([dict(r) for r in rows])
    df["Date"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert("Asia/Kolkata")
    df.set_index("Date", inplace=True)
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                             "close": "Close", "volume": "Volume"})
    df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


async def build_candidates(pool, d: date, capital: float) -> list[dict]:
    """Full per-day candidate build: funnel survivors -> base-stage classify
    -> entry-technique/trigger resolve -> position size. Returns only the
    stocks that pass every remaining gate, ranked (best first) same as
    screen_gpt.rank_candidates(): -ifp_score, then base_range_pct (the
    base_quality_score tie-break is skipped — every SQL survivor already has
    a perfect 1.0 base_quality_score by construction, same as production)."""
    screen_gpt.CAPITAL = capital
    survivors = await funnel_survivors(pool, d)
    candidates = []
    for row in survivors:
        sym_ns = row["symbol"] + ".NS"
        df = await load_ohlcv_frame(pool, row["symbol"], d)
        if df is None or len(df) < 200:
            continue
        stage, _ = screen_gpt.classify_base_stage(df, symbol=sym_ns)
        if stage > screen_gpt.BASE_STAGE_MAX_ALLOWED:
            continue
        trade = screen_gpt.create_trade(df, sym_ns, stage)
        if trade is None:
            continue
        target = screen_gpt.compute_target(trade["entry"], trade["sl"], symbol=sym_ns)
        candidates.append({
            "symbol": row["symbol"],
            "entry": trade["entry"], "sl": trade["sl"], "qty": trade["qty"],
            "entry_type": trade["entry_type"], "base_stage": stage,
            "risk_per_share": trade["risk_per_share"], "target": target,
            "ifp_score": float(row["ifp_score"] or 0),
            "base_range_pct": float(row["base_range_20d_pct"] or 0),
        })
    candidates.sort(key=lambda c: (-c["ifp_score"], c["base_range_pct"]))
    return candidates
