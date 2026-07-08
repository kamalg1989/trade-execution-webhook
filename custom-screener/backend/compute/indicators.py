"""
Indicator calculations for the Custom Screener.

Calc parity note: EMA/SMA/turnover formulas are COPIED from screen_gpt.py
(``ewm(span=N).mean()`` for EMAs, ``rolling(N).mean()`` for SMAs, and
``(close*volume).mean()`` for turnover) so that "above 200 SMA" means the
same thing in both screeners. This module is intentionally self-contained
(no imports from the existing app) to keep the standalone app decoupled.

Everything here is pure pandas/numpy and DB-agnostic, so it is unit-testable
without a database.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Trading-day offsets for percentage-change lookbacks.
PCT_OFFSETS = {
    "pct_chg_1d": 1,
    "pct_chg_5d": 5,
    "pct_chg_1m": 21,
    "pct_chg_3m": 63,
    "pct_chg_6m": 126,
    "pct_chg_1y": 252,
}

WINDOW_52W = 252          # trading days ~ 1 year
MIN_BARS_200SMA = 200     # below this, sma_200 is NULL (insufficient history)
TURNOVER_WINDOW = 20      # ~1 month, matches screen_gpt liquidity window
ATR_PERIOD = 14


def _pct(a: pd.Series, b: pd.Series) -> pd.Series:
    """(a - b) / b * 100, safe against divide-by-zero."""
    return np.where((b == 0) | b.isna(), np.nan, (a - b) / b * 100.0)


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Vectorized: compute every indicator for the WHOLE series in one pass.

    Input df must be sorted ascending by date with columns:
        time (datetime), open, high, low, close, volume
    Returns a new DataFrame indexed positionally with an added ``indicator_date``
    (date) column plus all indicator columns. One row per input bar.
    """
    if df.empty:
        return df.copy()

    df = df.sort_values("time").reset_index(drop=True).copy()
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)

    out = pd.DataFrame()
    out["symbol"] = df["symbol"] if "symbol" in df else np.nan
    # ohlcv_data.time is IST-midnight stored as timestamptz; asyncpg hands it back
    # in UTC. Convert to Asia/Kolkata before taking the date, else every bar shifts
    # to the previous calendar day (IST midnight = prior-day 18:30 UTC).
    _t = pd.to_datetime(df["time"], utc=True).dt.tz_convert("Asia/Kolkata")
    out["indicator_date"] = _t.dt.date
    out["close"] = close.round(2)

    # --- Moving averages (parity with screen_gpt) ---
    out["ema_10"] = close.ewm(span=10).mean().round(2)
    out["ema_21"] = close.ewm(span=21).mean().round(2)
    out["sma_50"] = close.rolling(50).mean().round(2)
    out["sma_200"] = close.rolling(MIN_BARS_200SMA).mean().round(2)  # NaN < 200 bars

    out["dist_ema_10_pct"] = np.round(_pct(close, out["ema_10"]), 2)
    out["dist_ema_21_pct"] = np.round(_pct(close, out["ema_21"]), 2)
    out["dist_sma_50_pct"] = np.round(_pct(close, out["sma_50"]), 2)
    out["dist_sma_200_pct"] = np.round(_pct(close, out["sma_200"]), 2)

    # --- 52-week high/low (inclusive; over available history) ---
    out["price_52w_high"] = high.rolling(WINDOW_52W, min_periods=1).max().round(2)
    out["price_52w_low"] = low.rolling(WINDOW_52W, min_periods=1).min().round(2)
    out["dist_52w_high_pct"] = np.round(_pct(close, out["price_52w_high"]), 2)  # <= 0
    out["dist_52w_low_pct"] = np.round(_pct(close, out["price_52w_low"]), 2)    # >= 0

    # --- Percentage changes (trading-day offsets) ---
    for col, n in PCT_OFFSETS.items():
        out[col] = np.round((close / close.shift(n) - 1.0) * 100.0, 2)

    # --- ATR(14) via Wilder smoothing ---
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    out["atr_14"] = tr.ewm(alpha=1.0 / ATR_PERIOD, adjust=False).mean().round(2)

    # --- Liquidity ---
    out["turnover_1m_avg_cr"] = (
        (close * volume).rolling(TURNOVER_WINDOW).mean() / 1e7
    ).round(2)
    out["volume_1m_avg"] = volume.rolling(TURNOVER_WINDOW).mean().round(0)

    # --- Data quality ---
    out["bars_available"] = np.arange(1, len(out) + 1, dtype=int)

    # New 52w high/low flags — a per-day fact (does THIS bar's high/low set a fresh
    # 252-day extreme as of this date). Persisted; historical rows are never rewritten.
    out["is_new_52w_high"] = high >= high.rolling(WINDOW_52W, min_periods=1).max()
    out["is_new_52w_low"] = low <= low.rolling(WINDOW_52W, min_periods=1).min()

    # Replace numpy NaN with None-friendly NaN (kept as NaN; DB layer casts to None)
    return out


# Columns persisted to stock_indicators (order matters for bulk upsert)
PERSIST_COLUMNS = [
    "symbol", "indicator_date", "close",
    "turnover_1m_avg_cr", "volume_1m_avg",
    "ema_10", "ema_21", "sma_50", "sma_200",
    "dist_ema_10_pct", "dist_ema_21_pct", "dist_sma_50_pct", "dist_sma_200_pct",
    "price_52w_high", "price_52w_low", "dist_52w_high_pct", "dist_52w_low_pct",
    "pct_chg_1d", "pct_chg_5d", "pct_chg_1m", "pct_chg_3m", "pct_chg_6m", "pct_chg_1y",
    "atr_14", "bars_available", "is_new_52w_high", "is_new_52w_low",
]
