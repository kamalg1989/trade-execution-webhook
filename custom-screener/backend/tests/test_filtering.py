"""Unit tests for the pure-Python filter/sort logic."""
import pytest

from app.filtering import FilterError, apply_filters, validate_filters


def rows():
    return [
        {"symbol": "AAA", "turnover_1m_avg_cr": 12.0, "sma_200": 20, "dist_sma_200_pct": 10,
         "sma_50": 25, "dist_sma_50_pct": 5, "ema_10": 26, "dist_52w_high_pct": -6,
         "dist_52w_low_pct": 75, "pct_chg_1m": 12.5, "pct_chg_1d": 1.0, "bars_available": 800},
        {"symbol": "BBB", "turnover_1m_avg_cr": 2.0, "sma_200": 100, "dist_sma_200_pct": -8,
         "sma_50": 110, "dist_sma_50_pct": -3, "ema_10": 105, "dist_52w_high_pct": -20,
         "dist_52w_low_pct": 30, "pct_chg_1m": -4.0, "pct_chg_1d": -2.0, "bars_available": 900},
        {"symbol": "CCC", "turnover_1m_avg_cr": 50.0, "sma_200": None, "dist_sma_200_pct": None,
         "sma_50": 60, "dist_sma_50_pct": 8, "ema_10": 61, "dist_52w_high_pct": -2,
         "dist_52w_low_pct": 120, "pct_chg_1m": 40.0, "pct_chg_1d": 5.0, "bars_available": 90},
    ]


def test_min_turnover():
    out = apply_filters(rows(), {"minTurnoverCr": 10}, include_insufficient=True)
    assert {r["symbol"] for r in out} == {"AAA", "CCC"}


def test_sma200_above_excludes_null_and_below():
    out = apply_filters(rows(), {"sma200": "above"})
    assert [r["symbol"] for r in out] == ["AAA"]        # BBB below, CCC null


def test_within_52w_high():
    out = apply_filters(rows(), {"within52wHighPct": 10}, include_insufficient=True)
    # AAA (-6) and CCC (-2) are within 10%; BBB (-20) is not
    assert {r["symbol"] for r in out} == {"AAA", "CCC"}


def test_include_insufficient_toggle():
    # CCC has 90 bars -> excluded by default
    out = apply_filters(rows(), {"minTurnoverCr": 40})
    assert [r["symbol"] for r in out] == []
    out2 = apply_filters(rows(), {"minTurnoverCr": 40}, include_insufficient=True)
    assert [r["symbol"] for r in out2] == ["CCC"]


def test_pct_range():
    out = apply_filters(rows(), {"pctChg1m": {"min": 5, "max": 20}}, include_insufficient=True)
    assert [r["symbol"] for r in out] == ["AAA"]


def test_sort_desc_nulls_last():
    out = apply_filters(rows(), {}, include_insufficient=True, sort_by="pct_chg_1m", order="DESC")
    assert [r["symbol"] for r in out] == ["CCC", "AAA", "BBB"]


def test_invalid_range_raises():
    with pytest.raises(FilterError):
        validate_filters({"pctChg1m": {"min": 20, "max": 5}})


def test_invalid_direction_raises():
    with pytest.raises(FilterError):
        validate_filters({"sma200": "sideways"})


def _rows_g1():
    return [
        {"symbol": "TIGHT", "bars_available": 800, "ma_aligned": True,
         "dist_ema_50_pct": 4, "ema_50": 90, "sma_200": 80, "dist_sma_200_pct": 10,
         "base_range_20d_pct": 6.0, "dist_20d_high_pct": -1.5, "vol_ratio_1d": 2.1,
         "vol_dryup_ratio": 0.9, "prior_upmove_pct": 30, "giveback_pct": 20, "atr_pct": 2.5,
         "dist_52w_high_pct": -4, "dist_52w_low_pct": 60,
         "ifp_score": 0.35, "updown_vol_ratio": 1.8, "obv_slope": 0.2},
        {"symbol": "WIDE", "bars_available": 800, "ma_aligned": False,
         "dist_ema_50_pct": -3, "ema_50": 100, "sma_200": 110, "dist_sma_200_pct": -8,
         "base_range_20d_pct": 25.0, "dist_20d_high_pct": -18, "vol_ratio_1d": 0.6,
         "vol_dryup_ratio": 1.6, "prior_upmove_pct": 5, "giveback_pct": 70, "atr_pct": 7.0,
         "dist_52w_high_pct": -30, "dist_52w_low_pct": 12,
         "ifp_score": 0.08, "updown_vol_ratio": 0.7, "obv_slope": -0.15},
    ]


def test_ma_aligned():
    out = apply_filters(_rows_g1(), {"maAligned": True})
    assert [r["symbol"] for r in out] == ["TIGHT"]


def test_base_tightness_and_volume():
    out = apply_filters(_rows_g1(), {"baseRange20dMaxPct": 8, "volRatioMin": 1.5})
    assert [r["symbol"] for r in out] == ["TIGHT"]


def test_prior_upmove_giveback_dryup():
    out = apply_filters(_rows_g1(), {"priorUpmoveMinPct": 15, "givebackMaxPct": 30,
                                     "volDryupMaxRatio": 1.3, "within20dHighPct": 5})
    assert [r["symbol"] for r in out] == ["TIGHT"]


def test_52w_below_high_and_above_low():
    # WIDE is >20% below its 52W high and >50% above... no: dist_low=12 -> not >50 above
    out = apply_filters(_rows_g1(), {"below52wHighPct": 20})
    assert [r["symbol"] for r in out] == ["WIDE"]
    out2 = apply_filters(_rows_g1(), {"above52wLowPct": 50})
    assert [r["symbol"] for r in out2] == ["TIGHT"]


def test_atr_range():
    out = apply_filters(_rows_g1(), {"atrPct": {"max": 4}})
    assert [r["symbol"] for r in out] == ["TIGHT"]


def test_ifp_and_flow_filters():
    assert [r["symbol"] for r in apply_filters(_rows_g1(), {"ifpScoreMin": 0.25})] == ["TIGHT"]
    assert [r["symbol"] for r in apply_filters(_rows_g1(), {"updownVolRatioMin": 1.5})] == ["TIGHT"]
    assert [r["symbol"] for r in apply_filters(_rows_g1(), {"obvSlopePositive": True})] == ["TIGHT"]
