"""Pivot / support / logical stop from the current base structure."""
from __future__ import annotations

import pandas as pd

from . import swings


def base_window(df: pd.DataFrame) -> pd.DataFrame:
    """Bars from the last significant swing high onward (the current base).

    Falls back to the trailing 20 bars when no swing high is found.
    """
    sh = swings.last_swing_high(df)
    if sh is None or sh[0] >= len(df) - 2:
        return df.iloc[-20:]
    return df.iloc[sh[0]:]


def compute_levels(df: pd.DataFrame) -> dict:
    """pivot = base high, support = base low, stop = min(base low, last swing low)."""
    base = base_window(df)
    pivot = float(base["high"].max())
    support = float(base["low"].min())
    lsl = swings.last_swing_low(df)
    stop = min(support, lsl) if lsl is not None else support
    return {
        "pivot": round(pivot, 2),
        "support": round(support, 2),
        "logical_stop": round(float(stop), 2),
        "base_len_bars": int(len(base)),
        "base_depth_pct": round((pivot - support) / pivot * 100, 2) if pivot else None,
    }
