"""
Pure-Python filtering + sorting over one trading day's indicator slice.

Per the design, a single indicator_date holds <=2,710 rows, so the DB query is a
trivial ``WHERE indicator_date = $1`` and ALL filter/sort logic runs here in
memory. Keeping it out of SQL makes it fully unit-testable without a database
and keeps the query plan a single index lookup.
"""
from __future__ import annotations

from typing import Optional


class FilterError(ValueError):
    """Raised for invalid filter input -> mapped to HTTP 400."""


def _range_ok(name: str, rng: Optional[dict]):
    if not rng:
        return
    lo, hi = rng.get("min"), rng.get("max")
    if lo is not None and hi is not None and lo > hi:
        raise FilterError(f"{name}.min ({lo}) must be <= {name}.max ({hi})")


def _passes_range(val, rng: Optional[dict]) -> bool:
    if not rng:
        return True
    lo, hi = rng.get("min"), rng.get("max")
    if lo is None and hi is None:
        return True
    if val is None:
        return False
    if lo is not None and val < lo:
        return False
    if hi is not None and val > hi:
        return False
    return True


SORTABLE = {
    "symbol", "close", "turnover_1m_avg_cr",
    "ema_10", "ema_21", "ema_50", "sma_50", "sma_200",
    "dist_sma_200_pct", "dist_52w_high_pct", "dist_52w_low_pct",
    "pct_chg_1d", "pct_chg_5d", "pct_chg_1m", "pct_chg_3m", "pct_chg_6m", "pct_chg_1y",
    "atr_pct", "base_range_20d_pct", "dist_20d_high_pct", "vol_ratio_1d",
    "vol_dryup_ratio", "prior_upmove_pct", "giveback_pct",
    "ifp_score", "updown_vol_ratio", "obv_slope",
}


def validate_filters(filters: dict):
    """Raise FilterError on bad input before doing any work."""
    for key in ("pctChg1d", "pctChg5d", "pctChg1m", "pctChg3m", "pctChg6m", "pctChg1y", "atrPct"):
        _range_ok(key, filters.get(key))
    for key in ("within52wHighPct", "below52wHighPct", "within52wLowPct", "above52wLowPct",
                "baseRange20dMaxPct", "within20dHighPct", "volRatioMin",
                "volDryupMaxRatio", "priorUpmoveMinPct", "givebackMaxPct",
                "updownVolRatioMin"):
        v = filters.get(key)
        if v is not None and v <= 0:
            raise FilterError(f"{key} must be > 0")
    ifp = filters.get("ifpScoreMin")
    if ifp is not None and not (0 <= ifp <= 1):
        raise FilterError("ifpScoreMin must be between 0 and 1")
    for key in ("sma200", "sma50", "ema50"):
        if filters.get(key) not in (None, "any", "above", "below"):
            raise FilterError(f"{key} must be one of: any, above, below")


_PCT_MAP = {
    "pctChg1d": "pct_chg_1d", "pctChg5d": "pct_chg_5d", "pctChg1m": "pct_chg_1m",
    "pctChg3m": "pct_chg_3m", "pctChg6m": "pct_chg_6m", "pctChg1y": "pct_chg_1y",
}


def _row_matches(r: dict, f: dict, include_insufficient: bool) -> bool:
    if not include_insufficient and (r.get("bars_available") or 0) < 200:
        return False

    mt = f.get("minTurnoverCr")
    if mt is not None and (r.get("turnover_1m_avg_cr") is None or r["turnover_1m_avg_cr"] < mt):
        return False

    for direction_key, dist_col, ma_col in (
        ("sma200", "dist_sma_200_pct", "sma_200"),
        ("sma50", "dist_sma_50_pct", "sma_50"),
        ("ema50", "dist_ema_50_pct", "ema_50"),
    ):
        d = f.get(direction_key)
        if d in (None, "any"):
            continue
        if r.get(ma_col) is None:
            return False
        dist = r.get(dist_col)
        if d == "above" and not (dist is not None and dist > 0):
            return False
        if d == "below" and not (dist is not None and dist < 0):
            return False

    if f.get("maAligned") and not r.get("ma_aligned"):
        return False

    lo = f.get("ema10Above")
    hi = f.get("ema10Below")
    if lo is not None and (r.get("ema_10") is None or r["ema_10"] <= lo):
        return False
    if hi is not None and (r.get("ema_10") is None or r["ema_10"] >= hi):
        return False

    # 52-week high: within X% (near) or more than X% below
    wh = f.get("within52wHighPct")
    if wh is not None and (r.get("dist_52w_high_pct") is None or r["dist_52w_high_pct"] <= -wh):
        return False
    bh = f.get("below52wHighPct")
    if bh is not None and (r.get("dist_52w_high_pct") is None or r["dist_52w_high_pct"] >= -bh):
        return False
    # 52-week low: within X% (near) or more than X% above
    wl = f.get("within52wLowPct")
    if wl is not None and (r.get("dist_52w_low_pct") is None or r["dist_52w_low_pct"] >= wl):
        return False
    al = f.get("above52wLowPct")
    if al is not None and (r.get("dist_52w_low_pct") is None or r["dist_52w_low_pct"] <= al):
        return False

    # Group-1 technical / base-quality
    br = f.get("baseRange20dMaxPct")
    if br is not None and (r.get("base_range_20d_pct") is None or r["base_range_20d_pct"] > br):
        return False
    w20 = f.get("within20dHighPct")
    if w20 is not None and (r.get("dist_20d_high_pct") is None or r["dist_20d_high_pct"] <= -w20):
        return False
    vr = f.get("volRatioMin")
    if vr is not None and (r.get("vol_ratio_1d") is None or r["vol_ratio_1d"] < vr):
        return False
    vd = f.get("volDryupMaxRatio")
    if vd is not None and (r.get("vol_dryup_ratio") is None or r["vol_dryup_ratio"] > vd):
        return False
    pu = f.get("priorUpmoveMinPct")
    if pu is not None and (r.get("prior_upmove_pct") is None or r["prior_upmove_pct"] < pu):
        return False
    gb = f.get("givebackMaxPct")
    if gb is not None and (r.get("giveback_pct") is None or r["giveback_pct"] > gb):
        return False
    if not _passes_range(r.get("atr_pct"), f.get("atrPct")):
        return False

    ifp = f.get("ifpScoreMin")
    if ifp is not None and (r.get("ifp_score") is None or r["ifp_score"] < ifp):
        return False
    uv = f.get("updownVolRatioMin")
    if uv is not None and (r.get("updown_vol_ratio") is None or r["updown_vol_ratio"] < uv):
        return False
    if f.get("obvSlopePositive") and not (r.get("obv_slope") is not None and r["obv_slope"] > 0):
        return False

    for key, col in _PCT_MAP.items():
        if not _passes_range(r.get(col), f.get(key)):
            return False
    return True


def apply_filters(rows: list[dict], filters: dict,
                  include_insufficient: bool = False,
                  sort_by: str = "pct_chg_1d", order: str = "DESC") -> list[dict]:
    """Filter a day's slice, then sort. Returns all matches (no pagination)."""
    validate_filters(filters)
    matched = [r for r in rows if _row_matches(r, filters, include_insufficient)]

    if sort_by not in SORTABLE:
        sort_by = "pct_chg_1d"
    reverse = str(order).upper() != "ASC"

    def key(r):
        v = r.get(sort_by)
        # push NULLs to the bottom regardless of direction
        if v is None:
            return (1, 0)
        if isinstance(v, str):
            return (0, v)
        return (0, -v if reverse else v)

    if sort_by == "symbol":
        matched.sort(key=lambda r: r.get("symbol") or "", reverse=reverse)
    else:
        matched.sort(key=lambda r: (r.get(sort_by) is None,
                                    r.get(sort_by) if r.get(sort_by) is not None else 0),
                     reverse=reverse)
    return matched
