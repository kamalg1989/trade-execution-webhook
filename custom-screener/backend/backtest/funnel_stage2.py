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
result, and vice versa).

Speed note (added after the first version of this module always
recomputed everything, uncached): build_candidates() below now uses a
SEPARATE cache table, backtest_stage2_signals_cache (sql/010), keyed by
(symbol, signal_date, config_hash) instead of just (symbol, signal_date).
config_hash is a hash of the 9 resolved Stage 2 constants (see
config_hash() below) — two runs only ever share a cached row when their
effective Stage 2 settings are byte-for-byte identical, which is the same
correctness guarantee the old "never cache" approach gave, just narrower
instead of blanket. The actual computation (classify_base_stage /
resolve_entry / compute_target, via v1._compute_signal) is completely
unchanged — this only changes whether a given (symbol, date, config) is
computed once and reused, or recomputed every time it's seen. A brand-new
config still pays full price on its first run, same as before; a repeated
or overlapping config (e.g. re-running the same combo, or combining a
Stage 2 override with a different Stage 1 gate override) now reuses
whatever was already computed.
"""
from __future__ import annotations

import hashlib
import json
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
    route around the shared (non-config-aware) quant-signal cache."""
    applied = False
    for run_key, attr, cast, scale in STAGE2_OVERRIDE_KEYS:
        val = run.get(run_key)
        if val is None:
            continue
        setattr(screen_gpt, attr, bool(val) if cast is bool else cast(val) * scale)
        applied = True
    return applied


def config_hash() -> str:
    """Hash of the 9 Stage 2 constants' CURRENT resolved values on the
    screen_gpt module — call this AFTER apply_overrides() so it reflects
    what this run will actually compute with (production defaults for any
    key the run didn't override, exactly as apply_overrides left them).
    Deterministic and stable across process restarts (sha256 of a
    fixed-order, fixed-precision JSON dump), so a cache row written by one
    run's subprocess is a valid hit for a later run's subprocess as long as
    the 9 values match exactly."""
    resolved = {attr: round(getattr(screen_gpt, attr), 6) if isinstance(getattr(screen_gpt, attr), float)
                else getattr(screen_gpt, attr)
                for _, attr, _, _ in STAGE2_OVERRIDE_KEYS}
    blob = json.dumps(resolved, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


async def build_candidates(pool, d: date, capital: float, chash: str) -> list[dict]:
    """Same shape/ranking as funnel.build_candidates() (Stage 1 gate and
    Stage 4 ranking untouched) — Stage 2 results are read from/written to
    backtest_stage2_signals_cache keyed by (symbol, signal_date, chash), see
    module docstring. Computation itself (v1._compute_signal) is identical
    to the plain funnel path; only the cache key differs."""
    survivors = await v1.funnel_survivors(pool, d)
    if not survivors:
        return []
    indicators_by_symbol = {row["symbol"]: row for row in survivors}
    symbols = list(indicators_by_symbol)

    cached_rows = await pool.fetch(
        "SELECT * FROM backtest_stage2_signals_cache "
        "WHERE signal_date = $1 AND config_hash = $2 AND symbol = ANY($3)",
        d, chash, symbols,
    )
    signals: dict[str, dict] = {r["symbol"]: dict(r) for r in cached_rows}
    todo = [s for s in symbols if s not in signals]

    if todo:
        frames = await v1.load_ohlcv_frames_batch(pool, todo, d)
        computed = await v1._compute_signals_concurrent(todo, frames, indicators_by_symbol)
        to_insert = []
        for sym in todo:
            result = computed[sym]
            signals[sym] = result
            to_insert.append((
                sym, d, chash, result["passed"], result.get("entry"), result.get("sl"),
                result.get("entry_type"), result.get("base_stage"),
                result.get("risk_per_share"), result.get("target"),
                result.get("ifp_score"), result.get("base_range_pct"),
            ))
        await pool.executemany(
            """
            INSERT INTO backtest_stage2_signals_cache
              (symbol, signal_date, config_hash, passed, entry, sl, entry_type, base_stage,
               risk_per_share, target, ifp_score, base_range_pct)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            ON CONFLICT (symbol, signal_date, config_hash) DO NOTHING
            """,
            to_insert,
        )

    candidates = []
    for sym in symbols:
        sig = signals.get(sym)
        if not sig or not sig["passed"]:
            continue
        entry, sl = float(sig["entry"]), float(sig["sl"])
        risk_per_share = float(sig["risk_per_share"])
        base_stage = sig["base_stage"]
        qty = v1._size_qty(capital, base_stage, entry, risk_per_share)
        if qty <= 0:
            continue
        candidates.append({
            "symbol": sym, "entry": entry, "sl": sl, "qty": qty,
            "entry_type": sig["entry_type"], "base_stage": base_stage,
            "risk_per_share": risk_per_share,
            "target": float(sig["target"]) if sig.get("target") is not None else 0.0,
            "ifp_score": float(sig["ifp_score"] or 0), "base_range_pct": float(sig["base_range_pct"] or 0),
        })
    candidates.sort(key=lambda c: (-c["ifp_score"], c["base_range_pct"]))
    return candidates
