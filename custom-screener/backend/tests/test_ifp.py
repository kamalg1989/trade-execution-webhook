"""Tests for the IFP + volume-flow compute module (pure math)."""
import numpy as np
import pandas as pd

from compute.ifp import ifp_series, ifp_components, updown_vol_ratio, obv_slope


def _accumulating(n=160):
    """Uptrend with strong-close high-volume up days, quiet down days."""
    rng = np.random.default_rng(0)
    close = np.cumsum(rng.normal(0.4, 1.0, n)) + 100
    high = close + 0.5
    low = close - 0.5
    vol = np.where(np.diff(np.r_[close[0], close]) > 0, 3_000_000, 500_000)  # big up-vol, small down-vol
    d = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.DataFrame({"time": d, "close": close, "high": high, "low": low, "volume": vol})


def test_ifp_score_range_and_positive_for_accumulation():
    df = _accumulating()
    s = ifp_series(df)
    last = s.iloc[-1]
    assert 0.0 <= last <= 1.0
    assert last > 0.2   # clear accumulation footprint


def test_ifp_components_match_lookback():
    df = _accumulating()
    c = ifp_components(df, lookback=100, vol_mult=1.5, close_pos_min=0.60)
    assert c["bars"] == len(df)
    assert 0 <= c["accumDays"] <= 100
    assert c["ifpScore"] is not None


def test_ifp_tunable_params_change_score():
    df = _accumulating()
    lenient = ifp_components(df, lookback=100, vol_mult=1.2, close_pos_min=0.4)["ifpScore"]
    strict = ifp_components(df, lookback=100, vol_mult=2.5, close_pos_min=0.9)["ifpScore"]
    assert lenient >= strict   # stricter thresholds -> fewer accumulation days


def test_updown_vol_ratio_bullish():
    df = _accumulating()
    assert updown_vol_ratio(df).iloc[-1] > 1.0   # more up-volume than down


def test_obv_slope_positive_for_uptrend():
    df = _accumulating()
    assert obv_slope(df).iloc[-1] > 0


def test_insufficient_bars():
    df = _accumulating(10)
    c = ifp_components(df)
    assert c["ifpScore"] is None
