"""Configurable pre-AI gate. hard = weak IFP never reaches the API."""
from __future__ import annotations

from .. import config


def apply_gate(
    symbol_features: dict[str, dict],
    mode: str | None = None,
    threshold: float | None = None,
) -> tuple[list[str], list[dict]]:
    """Split symbols into (passed, gated_out).

    symbol_features: {symbol: daily_features}
    Returns gated_out entries as {symbol, ifp_score, reason} for the response.
    """
    mode = (mode or config.AI_GATE_MODE).lower()
    threshold = threshold if threshold is not None else config.IFP_GATE_THRESHOLD

    passed, gated = [], []
    for sym, feats in symbol_features.items():
        if feats.get("error"):
            gated.append({"symbol": sym, "ifp_score": None, "reason": feats["error"]})
            continue
        score = feats.get("ifp_score")
        if mode == "soft":
            passed.append(sym)
            continue
        if score is None or score < threshold:
            gated.append({
                "symbol": sym, "ifp_score": score,
                "reason": f"ifp_score below threshold {threshold}",
            })
        else:
            passed.append(sym)
    return passed, gated
