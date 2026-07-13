"""Unit tests: feature engine, gate, verification — synthetic data, no DB/API."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ai_analysis.features import apply_gate, compute_features
from ai_analysis.features import swings, levels as levels_lib
from ai_analysis.verification import verify_levels


def make_df(n: int = 300, seed: int = 7, trend: float = 0.3) -> pd.DataFrame:
    """Synthetic uptrending OHLCV with a flat base at the end."""
    rng = np.random.default_rng(seed)
    closes = 100 + np.cumsum(rng.normal(trend, 1.2, n))
    base_len = min(25, n // 2)
    closes[-base_len:] = closes[-base_len] + rng.normal(0, 0.5, base_len)
    close = pd.Series(closes)
    high = close + rng.uniform(0.2, 1.5, n)
    low = close - rng.uniform(0.2, 1.5, n)
    open_ = close.shift(1).fillna(close.iloc[0])
    vol = pd.Series(rng.uniform(50_000, 150_000, n))
    vol.iloc[-base_len:] *= 0.6  # volume dry-up in the base
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {"open": open_.values, "high": high.values, "low": low.values,
         "close": close.values, "volume": vol.values}, index=idx)


def test_compute_features_shape():
    feats = compute_features(make_df(), "daily")
    assert feats["timeframe"] == "daily"
    assert feats["bars"] == 300
    for key in ("ifp_score", "pivot", "support", "logical_stop", "base_len_bars",
                "swing_structure", "vol_contraction_ratio", "pct_above_sma200"):
        assert key in feats
    assert feats["pivot"] >= feats["support"]
    assert feats["logical_stop"] <= feats["support"] + 1e-9


def test_compute_features_insufficient():
    feats = compute_features(make_df(20), "daily")
    assert feats["error"] == "insufficient_bars"


def test_vol_contraction_reflects_dryup():
    feats = compute_features(make_df(), "daily")
    if feats["vol_contraction_ratio"] is not None:
        assert feats["vol_contraction_ratio"] < 1.0


def test_swing_structure_uptrend():
    df = make_df(trend=0.8, seed=3)
    assert swings.structure(df) in ("hh_hl", "mixed")


def test_swing_structure_downtrend():
    df = make_df(trend=-0.8, seed=3)
    assert swings.structure(df) in ("lh_ll", "mixed")


def test_levels_ordering():
    lv = levels_lib.compute_levels(make_df())
    assert lv["pivot"] >= lv["support"] >= lv["logical_stop"] or (
        lv["logical_stop"] <= lv["support"])
    assert lv["base_len_bars"] > 0


def test_gate_hard():
    feats = {
        "AAA": {"ifp_score": 0.55},
        "BBB": {"ifp_score": 0.10},
        "CCC": {"error": "no_ohlcv"},
    }
    passed, gated = apply_gate(feats, mode="hard", threshold=0.30)
    assert passed == ["AAA"]
    assert {g["symbol"] for g in gated} == {"BBB", "CCC"}


def test_gate_soft_passes_all_valid():
    feats = {"AAA": {"ifp_score": 0.05}, "CCC": {"error": "no_ohlcv"}}
    passed, gated = apply_gate(feats, mode="soft", threshold=0.30)
    assert passed == ["AAA"]
    assert gated[0]["symbol"] == "CCC"


def test_verify_levels_verified():
    analysis = {"buy_point": {"breakout_level": 101.0, "stop_level": 94.5}}
    daily = {"pivot": 100.0, "logical_stop": 95.0}
    v = verify_levels(analysis, daily, tolerance=0.02)
    assert v["breakout"]["status"] == "verified"
    assert v["stop"]["status"] == "verified"
    assert v["overall"] == "verified"


def test_verify_levels_mismatch():
    analysis = {"buy_point": {"breakout_level": 110.0, "stop_level": 95.0}}
    daily = {"pivot": 100.0, "logical_stop": 95.0}
    v = verify_levels(analysis, daily, tolerance=0.02)
    assert v["breakout"]["status"] == "mismatch"
    assert v["overall"] == "mismatch"


def test_verify_levels_ai_missing():
    analysis = {"buy_point": {"breakout_level": None, "stop_level": 95.0}}
    daily = {"pivot": 100.0, "logical_stop": 95.0}
    v = verify_levels(analysis, daily, tolerance=0.02)
    assert v["breakout"]["status"] == "ai_missing"
    assert v["overall"] == "partial"
