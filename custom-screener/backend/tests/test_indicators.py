"""Unit tests for indicator math — pure pandas, no DB."""
import numpy as np
import pandas as pd
import pytest

from compute.indicators import compute_indicators


def make_series(closes, highs=None, lows=None, vols=None):
    n = len(closes)
    highs = highs or [c * 1.01 for c in closes]
    lows = lows or [c * 0.99 for c in closes]
    vols = vols or [100000] * n
    dates = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "time": dates, "symbol": "TEST",
        "open": closes, "high": highs, "low": lows, "close": closes, "volume": vols,
    })


def test_sma_200_null_before_200_bars():
    df = make_series(list(range(1, 150)))
    out = compute_indicators(df)
    assert out["sma_200"].isna().all()          # <200 bars -> NULL
    assert out["bars_available"].iloc[-1] == 149


def test_sma_50_matches_manual():
    closes = list(np.linspace(100, 200, 60))
    out = compute_indicators(make_series(closes))
    manual = float(np.mean(closes[-50:]))
    assert out["sma_50"].iloc[-1] == pytest.approx(round(manual, 2), abs=0.01)


def test_pct_change_offsets():
    closes = [100.0] * 300
    closes[-1] = 110.0            # last bar +10% vs prior
    out = compute_indicators(make_series(closes))
    assert out["pct_chg_1d"].iloc[-1] == pytest.approx(10.0, abs=0.01)
    # 21 bars ago was 100 -> +10%
    assert out["pct_chg_1m"].iloc[-1] == pytest.approx(10.0, abs=0.01)


def test_52w_high_low_and_distance():
    closes = [50.0] * 100 + [80.0] + [60.0] * 20   # peak at 80
    out = compute_indicators(make_series(closes))
    last = out.iloc[-1]
    # high column is close*1.01
    assert last["price_52w_high"] == pytest.approx(80.0 * 1.01, abs=0.01)
    assert last["dist_52w_high_pct"] < 0           # below the high
    assert last["dist_52w_low_pct"] > 0            # above the low


def test_above_below_direction_via_distance():
    closes = list(np.linspace(100, 300, 260))      # strong uptrend
    out = compute_indicators(make_series(closes))
    last = out.iloc[-1]
    assert last["dist_sma_200_pct"] > 0            # price above 200 SMA
    assert last["sma_200"] is not None


def test_turnover_cr():
    closes = [100.0] * 30
    vols = [1_000_000] * 30
    out = compute_indicators(make_series(closes, vols=vols))
    # 100 * 1_000_000 = 1e8 rupees = 10 Cr
    assert out["turnover_1m_avg_cr"].iloc[-1] == pytest.approx(10.0, abs=0.01)


def test_empty_input():
    assert compute_indicators(pd.DataFrame()).empty
