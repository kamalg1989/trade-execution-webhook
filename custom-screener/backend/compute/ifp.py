"""
Institutional Footprint (IFP) + volume-flow metrics — pure, vectorized, no AI.

Parity with screen_gpt.compute_ifp_score:
  Over `lookback` days, count
    - accumulation days: up-day AND volume > vol_mult * avg(vol,avg_win) AND
      close in the top (1-close_pos_min) of the day's range
    - quiet down-days: down-day AND volume < avg(vol,avg_win)
  ifp_score = (accum + quiet_down) / lookback   (0..1)

Plus two standard flow gauges:
  - updown_vol_ratio (N-day): sum(up-day vol) / sum(down-day vol)   (>1 = buying)
  - obv_slope (N-day): net signed volume over N days as a fraction of expected
    N-day volume (roughly -1..1; >0 = accumulation)

Same function powers the nightly precompute (default params) and the on-demand
tunable endpoint (custom params on a filtered subset), so results are consistent.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# BAU defaults
IFP_LOOKBACK = 100
IFP_VOL_MULT = 1.5
IFP_CLOSE_POS_MIN = 0.60
IFP_AVG_WIN = 20
FLOW_WIN = 50


def _cols(df: pd.DataFrame):
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    vol = df["volume"].astype(float)
    return close, high, low, vol


def ifp_series(df: pd.DataFrame, lookback: int = IFP_LOOKBACK,
               vol_mult: float = IFP_VOL_MULT, close_pos_min: float = IFP_CLOSE_POS_MIN,
               avg_win: int = IFP_AVG_WIN) -> pd.Series:
    """Per-bar IFP score = trailing-`lookback` accumulation fraction (0..1)."""
    close, high, low, vol = _cols(df)
    rng = (high - low).where((high - low) > 0, np.nan)
    close_pos = (close - low) / rng                       # 0..1 (NaN on doji)
    is_up = close > close.shift(1)
    avg_vol = vol.rolling(avg_win).mean()

    accum = (is_up & (vol > vol_mult * avg_vol) & (close_pos >= close_pos_min)).astype(float)
    quiet_down = ((~is_up) & (vol < avg_vol)).astype(float)
    score = (accum + quiet_down).rolling(lookback).sum() / lookback
    return score.round(3)


def ifp_components(df: pd.DataFrame, lookback: int = IFP_LOOKBACK,
                   vol_mult: float = IFP_VOL_MULT, close_pos_min: float = IFP_CLOSE_POS_MIN,
                   avg_win: int = IFP_AVG_WIN) -> dict:
    """IFP for the LAST bar with a breakdown — used by the on-demand endpoint."""
    if df is None or len(df) < avg_win + 2:
        return {"ifpScore": None, "accumDays": None, "quietDownDays": None, "bars": len(df) if df is not None else 0}
    close, high, low, vol = _cols(df)
    rng = (high - low).where((high - low) > 0, np.nan)
    close_pos = (close - low) / rng
    is_up = close > close.shift(1)
    avg_vol = vol.rolling(avg_win).mean()
    accum = (is_up & (vol > vol_mult * avg_vol) & (close_pos >= close_pos_min))
    quiet_down = ((~is_up) & (vol < avg_vol))
    tail = slice(-lookback, None)
    a = int(accum.iloc[tail].sum())
    q = int(quiet_down.iloc[tail].sum())
    denom = min(lookback, len(df))
    return {
        "ifpScore": round((a + q) / denom, 3) if denom else None,
        "accumDays": a, "quietDownDays": q, "bars": int(len(df)),
    }


def updown_vol_ratio(df: pd.DataFrame, window: int = FLOW_WIN) -> pd.Series:
    close, _, _, vol = _cols(df)
    is_up = close > close.shift(1)
    up_vol = vol.where(is_up, 0.0).rolling(window).sum()
    down_vol = vol.where(~is_up, 0.0).rolling(window).sum()
    return (up_vol / down_vol.where(down_vol > 0, np.nan)).round(2)


def obv_slope(df: pd.DataFrame, window: int = FLOW_WIN) -> pd.Series:
    close, _, _, vol = _cols(df)
    sign = np.sign(close.diff()).fillna(0.0)
    obv = (sign * vol).cumsum()
    exp_vol = vol.rolling(window).mean() * window          # expected N-day volume
    net = obv - obv.shift(window)
    return (net / exp_vol.where(exp_vol > 0, np.nan)).round(3)   # ~ -1..1, >0 accumulation
