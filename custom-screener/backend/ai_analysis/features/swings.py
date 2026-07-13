"""Swing point detection and HH-HL / LH-LL structure classification."""
from __future__ import annotations

import numpy as np
import pandas as pd

SWING_WIN = 5  # bars each side for a pivot


def swing_points(df: pd.DataFrame, win: int = SWING_WIN) -> tuple[list, list]:
    """Return (swing_highs, swing_lows) as lists of (iloc_index, price)."""
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    n = len(df)
    highs, lows = [], []
    for i in range(win, n - win):
        seg_h = high[i - win : i + win + 1]
        seg_l = low[i - win : i + win + 1]
        if high[i] == seg_h.max() and (seg_h == high[i]).sum() == 1:
            highs.append((i, float(high[i])))
        if low[i] == seg_l.min() and (seg_l == low[i]).sum() == 1:
            lows.append((i, float(low[i])))
    return highs, lows


def structure(df: pd.DataFrame, win: int = SWING_WIN, last_n: int = 4) -> str:
    """Classify recent structure: 'hh_hl' | 'lh_ll' | 'mixed' | 'insufficient'."""
    highs, lows = swing_points(df, win)
    hs = [p for _, p in highs[-last_n:]]
    ls = [p for _, p in lows[-last_n:]]
    if len(hs) < 2 or len(ls) < 2:
        return "insufficient"
    hh = all(b > a for a, b in zip(hs, hs[1:]))
    hl = all(b > a for a, b in zip(ls, ls[1:]))
    lh = all(b < a for a, b in zip(hs, hs[1:]))
    ll = all(b < a for a, b in zip(ls, ls[1:]))
    if hh and hl:
        return "hh_hl"
    if lh and ll:
        return "lh_ll"
    return "mixed"


def last_swing_low(df: pd.DataFrame, win: int = SWING_WIN) -> float | None:
    _, lows = swing_points(df, win)
    return lows[-1][1] if lows else None


def last_swing_high(df: pd.DataFrame, win: int = SWING_WIN) -> tuple[int, float] | None:
    highs, _ = swing_points(df, win)
    return highs[-1] if highs else None
