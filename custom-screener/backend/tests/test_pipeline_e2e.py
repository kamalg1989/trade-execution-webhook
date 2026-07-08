"""End-to-end (no DB): compute_indicators -> day slice -> snapshot -> filter."""
import numpy as np
import pandas as pd

from app.filtering import apply_filters
from compute.indicators import compute_indicators
from compute.snapshot import compute_snapshot


def _series(symbol, closes):
    n = len(closes)
    dates = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "time": dates, "symbol": symbol,
        "open": closes, "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes], "close": closes,
        "volume": [2_000_000] * n,
    })


def test_pipeline_end_to_end():
    # 3 symbols, 300 bars each: one strong uptrend, one downtrend, one flat
    up = list(np.linspace(50, 200, 300))
    down = list(np.linspace(200, 60, 300))
    flat = [100.0] * 300

    frames = {
        "UP": compute_indicators(_series("UP", up)),
        "DOWN": compute_indicators(_series("DOWN", down)),
        "FLAT": compute_indicators(_series("FLAT", flat)),
    }

    # last-date slice: one row per symbol
    slice_rows = []
    for df in frames.values():
        r = df.iloc[-1].to_dict()
        slice_rows.append(r)

    # snapshot
    snap = compute_snapshot(slice_rows, processed_count=3, complete_threshold=1)
    assert snap["total_stocks"] == 3
    assert snap["eligible_stocks"] == 3          # all have >=200 bars
    assert snap["count_above_200sma"] >= 1       # UP is above

    # filter: above 200 SMA, decent turnover
    matched = apply_filters(slice_rows, {"sma200": "above", "minTurnoverCr": 1})
    syms = {r["symbol"] for r in matched}
    assert "UP" in syms
    assert "DOWN" not in syms
