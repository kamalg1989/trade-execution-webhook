"""Cross-check AI-reported levels against computed levels (±tolerance)."""
from __future__ import annotations

from .. import config


def _check(ai_val: float | None, computed: float | None, tolerance: float) -> dict:
    if computed is None:
        return {"status": "no_computed", "ai": ai_val, "computed": None}
    if ai_val is None:
        return {"status": "ai_missing", "ai": None, "computed": computed}
    dev = abs(ai_val - computed) / computed
    return {
        "status": "verified" if dev <= tolerance else "mismatch",
        "ai": ai_val,
        "computed": computed,
        "deviation_pct": round(dev * 100, 2),
    }


def verify_levels(analysis: dict, daily_feats: dict, tolerance: float | None = None) -> dict:
    """Compare buy_point.breakout_level/stop_level to computed pivot/logical_stop."""
    tol = tolerance if tolerance is not None else config.LEVEL_TOLERANCE
    bp = analysis.get("buy_point") or {}
    breakout = _check(bp.get("breakout_level"), daily_feats.get("pivot"), tol)
    stop = _check(bp.get("stop_level"), daily_feats.get("logical_stop"), tol)
    overall = "verified"
    if "mismatch" in (breakout["status"], stop["status"]):
        overall = "mismatch"
    elif "ai_missing" in (breakout["status"], stop["status"]):
        overall = "partial"
    return {"overall": overall, "breakout": breakout, "stop": stop, "tolerance_pct": tol * 100}
