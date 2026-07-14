"""Unit tests for the pure outcome computation."""
from ai_analysis.outcomes import compute_outcome


def bars_from_closes(closes, spread=1.0):
    return [{"close": c, "high": c + spread, "low": c - spread} for c in closes]


def test_returns_progressive():
    bars = bars_from_closes([102, 104, 106, 108, 110] + [110] * 15)  # 20 bars
    out = compute_outcome(100.0, bars, None, None)
    assert out["ret_5d"] == 10.0
    assert out["ret_20d"] == 10.0
    assert out["ret_60d"] is None  # not enough bars yet


def test_hit_breakout_true():
    bars = bars_from_closes([101, 105, 103] + [103] * 17)
    out = compute_outcome(100.0, bars, breakout=105.5, stop=90.0)
    assert out["hit_breakout"] is True   # high 106 >= 105.5
    assert out["hit_stop"] is False      # full window, never touched 90


def test_hit_unknown_when_window_incomplete():
    bars = bars_from_closes([101, 102, 103])  # only 3 bars
    out = compute_outcome(100.0, bars, breakout=200.0, stop=50.0)
    assert out["hit_breakout"] is None
    assert out["hit_stop"] is None


def test_hit_stop_true_early():
    bars = bars_from_closes([99, 95, 98])
    out = compute_outcome(100.0, bars, breakout=None, stop=94.5)
    assert out["hit_stop"] is True       # low 94 <= 94.5, even in short window
    assert out["hit_breakout"] is None   # no level given


def test_no_levels_no_bars():
    out = compute_outcome(100.0, [], None, None)
    assert all(v is None for v in out.values())
