"""Unit tests for market breadth / regime aggregation."""
from compute.snapshot import compute_snapshot


def _mk(dist200, dist50=5, dist_high=-5, dist_low=50, new_high=False):
    return {
        "sma_200": None if dist200 is None else 100,
        "sma_50": 100,
        "dist_sma_200_pct": dist200,
        "dist_sma_50_pct": dist50,
        "dist_52w_high_pct": dist_high,
        "dist_52w_low_pct": dist_low,
        "pct_chg_1d": 5.0, "pct_chg_1m": 25.0, "pct_chg_3m": 0, "pct_chg_6m": 0,
        "is_new_52w_high": new_high, "is_new_52w_low": False,
    }


def test_strong_uptrend_regime():
    rows = [_mk(10) for _ in range(80)] + [_mk(-5) for _ in range(20)]
    snap = compute_snapshot(rows, processed_count=100, complete_threshold=50)
    assert snap["count_above_200sma"] == 80
    assert snap["regime"] == "Strong Uptrend"       # 80% above 200
    assert snap["trend_score"] == 0.6               # 2*(0.8-0.5)
    assert snap["is_complete"] is True


def test_correction_regime():
    rows = [_mk(10) for _ in range(35)] + [_mk(-5) for _ in range(65)]
    snap = compute_snapshot(rows, processed_count=100, complete_threshold=50)
    assert snap["regime"] == "Correction"           # 35% above 200


def test_eligible_excludes_null_sma200():
    rows = [_mk(10) for _ in range(50)] + [_mk(None) for _ in range(50)]
    snap = compute_snapshot(rows, processed_count=100)
    assert snap["total_stocks"] == 100
    assert snap["eligible_stocks"] == 50
    # 50/50 eligible above 200 -> consolidation boundary (pct200 = 1.0 => strong uptrend)
    assert snap["regime"] == "Strong Uptrend"


def test_incomplete_flag():
    rows = [_mk(10) for _ in range(10)]
    snap = compute_snapshot(rows, processed_count=10, complete_threshold=2600)
    assert snap["is_complete"] is False


def test_new_high_count_uses_persisted_flag():
    rows = [_mk(10, new_high=True) for _ in range(7)] + [_mk(10, new_high=False) for _ in range(3)]
    snap = compute_snapshot(rows, processed_count=10, complete_threshold=1)
    assert snap["count_new_52w_high"] == 7
    assert snap["count_new_52w_low"] == 0


def test_big_mover_counts():
    rows = [_mk(10) for _ in range(5)]
    snap = compute_snapshot(rows, processed_count=5, complete_threshold=1)
    assert snap["count_moved_gt_4_5pct_1d"] == 5     # all at pct_chg_1d=5.0
    assert snap["count_moved_gt_20pct_1m"] == 5      # all at 25.0
