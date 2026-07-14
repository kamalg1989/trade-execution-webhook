"""System prompt + feature-block formatting for the vision call."""
from __future__ import annotations

import json

SYSTEM_PROMPT_CORE = """You are an expert technical analyst specialising in the Base-and-Bounce \
methodology: market cycle phases (accumulation, advance, distribution, decline), base counting \
(base 0 = accumulation through base 4+), constructive-base assessment, and Institutional \
Footprint (IFP) — the principle that institutions can manipulate price but cannot hide volume.

You receive, for one stock:
1. A DAILY candlestick chart (volume panel with 20-day volume average line, EMAs 10/21/50/200 — \
see the on-chart legend for which color is which EMA).
2. A WEEKLY candlestick chart (EMAs 10/40 week — see the on-chart legend), if provided.
3. COMPUTED FEATURES: exact numbers derived from OHLCV — IFP score, accumulation days, \
absorption days, up/down volume ratio, volume contraction, base depth, retracement of prior \
advance, swing structure, and computed levels (pivot/support/logical stop).

Rules:
- Trust the COMPUTED FEATURES for anything volume- or level-related; charts cannot convey \
exact volume magnitudes. Use the charts for structural shape: base form, pattern geometry, \
trend context, and cross-timeframe base counting.
- A constructive base: gives back < 30% of the prior advance, contracting volume, no wild \
bars. Flag violations.
- Base 4+ or distribution signatures (high-volume churn, LH-LL, double/triple tops, H&S) \
mean caution or AVOID.
- Buy point types: pullback, reverse head-and-shoulders breakout, high breakout, breakout \
retest. Structures: hammer, HH-HL.
- breakout_level and stop_level must be concrete prices consistent with the computed pivot \
and logical stop unless the chart clearly shows a better structural level — if you deviate, \
say why in the thesis.
- Be conservative: if evidence is mixed, prefer EARLY_STAGE or NOT_READY over SETUP_READY.
- Be brief: evidence and thesis max 2 sentences each; base_quality_reasons max 4 short phrases; \
report at most 4 patterns (highest confidence first)."""

# Anthropic path reports via forced tool-use; Gemini path uses response_schema
# JSON mode instead, so it imports SYSTEM_PROMPT_CORE directly (no tool line).
SYSTEM_PROMPT = SYSTEM_PROMPT_CORE + \
    "\n\nReport your analysis ONLY via the report_chart_analysis tool."


def _compact(feats: dict) -> dict:
    """Trim token-heavy detail before sending to the model."""
    f = dict(feats)
    days = f.get("absorption_days")
    if isinstance(days, list) and days:
        f["absorption_days_count"] = len(days)
        f["absorption_days_recent"] = days[-2:]
        del f["absorption_days"]
    for k, v in f.items():
        if isinstance(v, float):
            f[k] = round(v, 3)
    return f


def feature_block(symbol: str, daily_feats: dict, weekly_feats: dict | None) -> str:
    dumps = lambda d: json.dumps(_compact(d), separators=(",", ":"), default=str)  # noqa: E731
    if weekly_feats is None:
        return (
            f"Stock: {symbol}\n"
            f"FEATURES daily: {dumps(daily_feats)}\n"
            "Only the daily chart is provided (no weekly). Base your base_count on daily "
            "structure and set weekly_context to 'not analysed'. "
            "Analyse base structure, IFP, patterns and buy point per your instructions."
        )
    return (
        f"Stock: {symbol}\n"
        f"FEATURES daily: {dumps(daily_feats)}\n"
        f"FEATURES weekly: {dumps(weekly_feats)}\n"
        "Image 1 = daily chart, image 2 = weekly chart. "
        "Analyse base structure, IFP, patterns and buy point per your instructions."
    )
