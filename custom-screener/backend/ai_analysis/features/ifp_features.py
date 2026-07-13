"""Deterministic feature engine — everything the AI receives as numbers.

Reuses compute/ifp.py for IFP score and flow gauges; adds absorption days,
volume contraction, retracement depth, extension vs SMAs, swing structure,
and base levels. Pure pandas, no AI, no DB.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from compute import ifp as ifp_lib

from . import levels as levels_lib
from . import swings

ABSORB_VOL_MULT = 1.5
ABSORB_RANGE_MAX = 0.7      # × ATR14
ABSORB_CLOSE_POS = 0.60     # close in top 40% of range
ABSORB_NEAR_SUPPORT = 0.03  # low within 3% of base support


def _atr(df: pd.DataFrame, win: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return tr.rolling(win).mean()


def absorption_days(df: pd.DataFrame, support: float, last_n: int = 20) -> list[dict]:
    """High volume + narrow range + strong close near support = absorb & bounce."""
    if len(df) < 30 or not support:
        return []
    d = df.copy()
    d["avg_vol"] = d["volume"].rolling(20).mean()
    d["atr"] = _atr(d)
    rng = (d["high"] - d["low"]).replace(0, np.nan)
    d["close_pos"] = (d["close"] - d["low"]) / rng
    tail = d.iloc[-last_n:]
    mask = (
        (tail["volume"] > ABSORB_VOL_MULT * tail["avg_vol"])
        & ((tail["high"] - tail["low"]) < ABSORB_RANGE_MAX * tail["atr"])
        & (tail["close_pos"] >= ABSORB_CLOSE_POS)
        & (tail["low"] <= support * (1 + ABSORB_NEAR_SUPPORT))
    )
    out = []
    for ts, row in tail[mask.fillna(False)].iterrows():
        out.append({
            "date": str(getattr(ts, "date", lambda: ts)()),
            "close": round(float(row["close"]), 2),
            "vol_x_avg": round(float(row["volume"] / row["avg_vol"]), 2)
            if row["avg_vol"] else None,
        })
    return out


def vol_contraction(df: pd.DataFrame, base_len: int) -> float | None:
    """Base avg volume ÷ prior-advance avg volume. < 1 = constructive dry-up."""
    if base_len <= 1 or len(df) < base_len + 10:
        return None
    base_vol = df["volume"].iloc[-base_len:].mean()
    adv_len = min(base_len * 2, len(df) - base_len)
    adv_vol = df["volume"].iloc[-(base_len + adv_len):-base_len].mean()
    if not adv_vol:
        return None
    return round(float(base_vol / adv_vol), 2)


def retrace_of_advance(df: pd.DataFrame, pivot: float, support: float) -> float | None:
    """Base depth as % of prior advance ('give away < 30% of up move')."""
    highs, lows = swings.swing_points(df)
    if not lows or not pivot:
        return None
    lows_before_pivot = [p for i, p in lows if p < pivot]
    if not lows_before_pivot:
        return None
    advance_start = min(lows_before_pivot[-3:])
    advance = pivot - advance_start
    if advance <= 0:
        return None
    return round((pivot - support) / advance * 100, 1)


def _sma(s: pd.Series, w: int) -> float | None:
    if len(s) < w:
        return None
    return float(s.rolling(w).mean().iloc[-1])


def compute_features(df: pd.DataFrame, timeframe: str = "daily") -> dict:
    """Full feature dict for one symbol + timeframe. df: time-indexed OHLCV asc."""
    if df is None or len(df) < 40:
        return {"error": "insufficient_bars", "bars": 0 if df is None else len(df)}

    close = float(df["close"].iloc[-1])
    lv = levels_lib.compute_levels(df)
    ifp = ifp_lib.ifp_components(df)
    udr = ifp_lib.updown_vol_ratio(df).iloc[-1]
    obv = ifp_lib.obv_slope(df).iloc[-1]
    sma50 = _sma(df["close"], 50)
    sma200 = _sma(df["close"], 200)

    feats = {
        "timeframe": timeframe,
        "bars": int(len(df)),
        "close": round(close, 2),
        # IFP core (reused engine)
        "ifp_score": ifp.get("ifpScore"),
        "accum_days": ifp.get("accumDays"),
        "quiet_down_days": ifp.get("quietDownDays"),
        "updown_vol_ratio": None if pd.isna(udr) else float(udr),
        "obv_slope": None if pd.isna(obv) else float(obv),
        # Base structure
        **lv,
        "retrace_of_advance_pct": retrace_of_advance(df, lv["pivot"], lv["support"]),
        "vol_contraction_ratio": vol_contraction(df, lv["base_len_bars"]),
        "swing_structure": swings.structure(df),
        "absorption_days": absorption_days(df, lv["support"]),
        # Extension
        "pct_above_sma50": round((close / sma50 - 1) * 100, 2) if sma50 else None,
        "pct_above_sma200": round((close / sma200 - 1) * 100, 2) if sma200 else None,
        "sma50_above_sma200": bool(sma50 > sma200) if sma50 and sma200 else None,
        "dist_to_pivot_pct": round((lv["pivot"] / close - 1) * 100, 2) if close else None,
    }
    return feats
