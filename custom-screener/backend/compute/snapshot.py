"""
Market breadth / regime aggregation for a single trading day.

Pure functions over a list of per-symbol indicator dicts (one day's slice),
so it is unit-testable without a database.
"""
from __future__ import annotations

from typing import Iterable

MIN_BARS_ELIGIBLE = 200  # needs a valid sma_200 to count toward breadth


def _num(v):
    return v if isinstance(v, (int, float)) else None


def compute_snapshot(rows: Iterable[dict], processed_count: int,
                     complete_threshold: int = 2600) -> dict:
    """
    rows: iterable of indicator dicts for ONE indicator_date.
    Returns a market_snapshot row dict (regime, breadth, counts).
    """
    rows = list(rows)
    total = len(rows)
    eligible = [r for r in rows if r.get("sma_200") is not None]
    elig_n = len(eligible)

    def count(pred):
        return sum(1 for r in rows if pred(r))

    above_50 = count(lambda r: (r.get("dist_sma_50_pct") or 0) > 0 and r.get("sma_50") is not None)
    above_200 = count(lambda r: (r.get("dist_sma_200_pct") or 0) > 0 and r.get("sma_200") is not None)
    below_50 = count(lambda r: (r.get("dist_sma_50_pct") or 0) < 0 and r.get("sma_50") is not None)
    below_200 = count(lambda r: (r.get("dist_sma_200_pct") or 0) < 0 and r.get("sma_200") is not None)

    def within_high(pct):
        return count(lambda r: r.get("dist_52w_high_pct") is not None and r["dist_52w_high_pct"] > -pct)

    def within_low(pct):
        return count(lambda r: r.get("dist_52w_low_pct") is not None and r["dist_52w_low_pct"] < pct)

    new_high = count(lambda r: r.get("is_new_52w_high"))
    new_low = count(lambda r: r.get("is_new_52w_low"))

    def moved(field, thr):
        return count(lambda r: r.get(field) is not None and abs(r[field]) >= thr)

    denom = elig_n if elig_n else 1
    pct200 = above_200 / denom
    pct50 = above_50 / denom
    nh_nl = (new_high - new_low) / denom

    trend_score = round(2 * (pct200 - 0.5), 2)                       # -1..+1
    breadth_score = round(0.5 * pct200 + 0.3 * pct50
                          + 0.2 * ((nh_nl + 1) / 2), 2)              # 0..1

    if pct200 >= 0.70:
        regime = "Strong Uptrend"
    elif pct200 >= 0.55:
        regime = "Moderate Uptrend"
    elif pct200 >= 0.45:
        regime = "Consolidation"
    elif pct200 >= 0.30:
        regime = "Correction"
    else:
        regime = "Strong Correction"

    return {
        "total_stocks": total,
        "eligible_stocks": elig_n,
        "count_above_50sma": above_50,
        "count_above_200sma": above_200,
        "count_below_50sma": below_50,
        "count_below_200sma": below_200,
        "count_within_15pct_52w_high": within_high(15),
        "count_within_10pct_52w_high": within_high(10),
        "count_within_15pct_52w_low": within_low(15),
        "count_within_10pct_52w_low": within_low(10),
        "count_new_52w_high": new_high,
        "count_new_52w_low": new_low,
        "count_moved_gt_4_5pct_1d": moved("pct_chg_1d", 4.5),
        "count_moved_gt_20pct_1m": moved("pct_chg_1m", 20),
        "count_moved_gt_60pct_3m": moved("pct_chg_3m", 60),
        "count_moved_gt_100pct_6m": moved("pct_chg_6m", 100),
        "regime": regime,
        "trend_score": trend_score,
        "breadth_score": breadth_score,
        "is_complete": processed_count >= complete_threshold,
    }
