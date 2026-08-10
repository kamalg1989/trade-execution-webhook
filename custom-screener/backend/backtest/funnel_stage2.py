"""Stage 2 (base-stage classification + entry-technique detection)
overrides — backtest-only, applied by monkeypatching screen_gpt.py's
module-level constants for the lifetime of the run's subprocess.

Safe because every backtest run is its own fresh `python -m
backtest.runner --run-id N` subprocess (see app/routers/backtest.py's
create_run()) — patching screen_gpt's globals here can never leak into
another run, into the live trading services, or into production
screen_gpt.py on disk. It only ever affects the in-memory copy of the
module inside this one subprocess.

Correctness note: `backtest_quant_signals` (funnel.py's cache table) is
keyed only by (symbol, signal_date) and assumes Stage 2 is always computed
at screen_gpt.py's fixed production constants — every cached row is valid
for every run because Stage 2 never changed between runs before this
module existed. Once Stage 2 itself becomes overridable, sharing that
cache across differently-configured runs would silently contaminate both
directions (a default run could read a stage2-overridden run's cached
result, and vice versa). So build_candidates_uncached() below deliberately
never reads or writes that cache — it always recomputes classify_base_stage
/resolve_entry fresh, trading some speed for correctness.
"""
from __future__ import annotations

from datetime import date

import screen_gpt

from . import funnel as v1

# (run-config key, screen_gpt attribute, cast, scale-to-native-units)
# Percent-style run fields are entered as whole numbers (e.g. 15 for 15%)
# and scaled to the 0-1 fractions screen_gpt's constants actually use.
STAGE2_OVERRIDE_KEYS = (
    ("stage2_base_stage_max_allowed", "BASE_STAGE_MAX_ALLOWED", int, 1),
    ("stage2_base_min_width_bars", "BASE_MIN_WIDTH_BARS", int, 1),
    ("stage2_base_bounce_min_pct", "BASE_BOUNCE_MIN_PCT", float, 1 / 100),
    ("stage2_trend_bar_close_threshold", "TREND_BAR_CLOSE_THRESHOLD", float, 1),
    ("stage2_pin_bar_max_body_pct", "PIN_BAR_MAX_BODY_PCT", float, 1),
    ("stage2_pin_bar_min_lower_wick_pct", "PIN_BAR_MIN_LOWER_WICK_PCT", float, 1),
    ("stage2_min_bar_range_pct", "MIN_BAR_RANGE_PCT", float, 1 / 100),
    ("stage2_enable_pullback_trigger", "ENABLE_PULLBACK_TRIGGER", bool, None),
    ("stage2_enable_breakout_retest_trigger", "ENABLE_BREAKOUT_RETEST_TRIGGER", bool, None),
)


def apply_overrides(run: dict) -> bool:
    """Monkeypatch screen_gpt's constants per the run config. Returns True
    if any override was actually applied, so the caller knows whether to
    route around the shared quant-signal cache."""
    applied = False
    for run_key, attr, cast, scale in STAGE2_OVERRIDE_KEYS:
        val = run.get(run_key)
        if val is None:
            continue
        setattr(screen_gpt, attr, bool(val) if cast is bool else cast(val) * scale)
        applied = True
    return applied


async def build_candidates_uncached(pool, d: date, capital: float) -> list[dict]:
    """Same shape/ranking as funnel.build_candidates() (Stage 1 gate and
    Stage 4 ranking untouched) but recomputes Stage 2 fresh every call,
    never touching backtest_quant_signals — see module docstring."""
    survivors = await v1.funnel_survivors(pool, d)
    if not survivors:
        return []
    indicators_by_symbol = {row["symbol"]: row for row in survivors}
    symbols = list(indicators_by_symbol)

    frames = await v1.load_ohlcv_frames_batch(pool, symbols, d)
    # Uncached, so this recomputes every symbol every call — parallelized
    # (see v1._compute_signals_concurrent) since there's no cache to soften
    # the cost the way funnel.py/funnel_v2.py's "todo"-only loop can.
    computed = await v1._compute_signals_concurrent(symbols, frames, indicators_by_symbol)
    candidates = []
    for sym in symbols:
        sig = computed[sym]
        if not sig.get("passed"):
            continue
        entry, sl = sig["entry"], sig["sl"]
        risk_per_share = sig["risk_per_share"]
        base_stage = sig["base_stage"]
        qty = v1._size_qty(capital, base_stage, entry, risk_per_share)
        if qty <= 0:
            continue
        candidates.append({
            "symbol": sym, "entry": entry, "sl": sl, "qty": qty,
            "entry_type": sig["entry_type"], "base_stage": base_stage,
            "risk_per_share": risk_per_share,
            "target": sig.get("target") or 0.0,
            "ifp_score": sig["ifp_score"], "base_range_pct": sig["base_range_pct"],
        })
    candidates.sort(key=lambda c: (-c["ifp_score"], c["base_range_pct"]))
    return candidates
