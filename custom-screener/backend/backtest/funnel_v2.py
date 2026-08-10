"""Configurable Stage-1 gate backtest module — lets a backtest run override
any of the SQL survivor-gate thresholds (screen_gpt.py's production
constants) independently, one knob at a time or in combination, to test
whether loosening/tightening a specific gate changes trade quality. Never
touches production screen_gpt.py; only used by the backtest engine, and
only when a run supplies at least one gate override and/or opts into
`quant_funnel_variant='v2'`.

Two independent experiments are supported here, deliberately kept separate
so results aren't conflated:

1. Gate-threshold overrides (`gate_overrides` dict) — any subset of the 8
   Stage-1 thresholds below. Missing/None = use screen_gpt.py's current
   production value, so an empty override dict reproduces funnel.py's gate
   exactly. This is what "modify Stage 1 with different settings" means.

     min_turnover_cr        - screen_gpt.MIN_DAILY_TURNOVER (Rs cr/day, 1mo avg)
     max_base_range_pct     - screen_gpt.TECH_MAX_BASE_RANGE (%, 20d high-low/low)
     min_vol_mult            - screen_gpt.TECH_VOL_MULT (today's vol vs 20d avg)
     min_prior_upmove_pct   - screen_gpt.BASE_MIN_PRIOR_UPMOVE_PCT (%, 60-bar lookback)
     max_giveback_pct       - screen_gpt.BASE_MAX_GIVEBACK_PCT (% of prior upmove given back)
     max_vol_dryup_ratio    - screen_gpt.BASE_VOL_DRYUP_MAX_RATIO (base vol / prior vol)
     max_dist_from_high_pct - screen_gpt.NEAR_BREAKOUT_MAX_DISTANCE (%, distance below 20d high)
     min_ifp_score           - screen_gpt.IFP_MIN_SCORE (0..1 institutional footprint)

2. Alternate ranking (`use_v2_ranking=True`, i.e. `quant_funnel_variant='v2'`)
   — the re-ranking tested in run #60/#61 (rank by distance from a 16.25%
   base-range target, then turnover ascending) which underperformed
   production's ranking (-ifp_score, base_range_pct ascending) head-to-head.
   Defaults OFF. Kept available but should be treated as a separately-known-
   bad experiment, not mixed into gate-threshold tests by default.

Stage 2 (base-stage classify + entry-technique resolve), Stage 3 (position
sizing), and the default Stage 4 ranking are all reused directly from
funnel.py, so this module can never silently drift from production parity
on anything not explicitly being varied.
"""
from __future__ import annotations

from datetime import date

from . import funnel as v1

# Rank target used only when use_v2_ranking=True (see module docstring,
# experiment 2). Not applied to gate-override-only runs.
BASE_RANGE_TARGET_PCT = 16.25

GATE_SQL_V2 = """
    SELECT symbol, close, base_range_20d_pct, ifp_score, turnover_1m_avg_cr
    FROM stock_indicators
    WHERE indicator_date = $1
      AND turnover_1m_avg_cr >= $2
      AND close > sma_200 AND close > ema_50
      AND base_range_20d_pct < $3
      AND vol_ratio_1d > $4
      AND prior_upmove_pct >= $5
      AND giveback_pct <= $6
      AND vol_dryup_ratio <= $7
      AND dist_20d_high_pct >= $8
      AND ifp_score >= $9
    ORDER BY symbol
"""


def _resolve_gate_params(overrides: dict | None) -> tuple:
    """Fill in any missing override with screen_gpt.py's current production
    value, so an empty/None overrides dict reproduces funnel.py's gate
    exactly (same 9 params, same order as funnel.py's GATE_SQL)."""
    import screen_gpt
    o = overrides or {}
    return (
        float(o["min_turnover_cr"]) if o.get("min_turnover_cr") is not None
        else screen_gpt.MIN_DAILY_TURNOVER / 1e7,
        float(o["max_base_range_pct"]) if o.get("max_base_range_pct") is not None
        else screen_gpt.TECH_MAX_BASE_RANGE * 100,
        float(o["min_vol_mult"]) if o.get("min_vol_mult") is not None
        else screen_gpt.TECH_VOL_MULT,
        float(o["min_prior_upmove_pct"]) if o.get("min_prior_upmove_pct") is not None
        else screen_gpt.BASE_MIN_PRIOR_UPMOVE_PCT * 100,
        float(o["max_giveback_pct"]) if o.get("max_giveback_pct") is not None
        else screen_gpt.BASE_MAX_GIVEBACK_PCT * 100,
        float(o["max_vol_dryup_ratio"]) if o.get("max_vol_dryup_ratio") is not None
        else screen_gpt.BASE_VOL_DRYUP_MAX_RATIO,
        float(o["max_dist_from_high_pct"]) if o.get("max_dist_from_high_pct") is not None
        else -screen_gpt.NEAR_BREAKOUT_MAX_DISTANCE * 100,
        float(o["min_ifp_score"]) if o.get("min_ifp_score") is not None
        else screen_gpt.IFP_MIN_SCORE,
    )


async def funnel_survivors_v2(pool, d: date, gate_overrides: dict | None) -> list[dict]:
    params = _resolve_gate_params(gate_overrides)
    rows = await pool.fetch(GATE_SQL_V2, d, *params)
    return [dict(r) for r in rows]


def _rank_key_v1(c: dict) -> tuple:
    """Identical to funnel.py's Stage 4 ranking (-ifp_score, base_range_pct
    ascending) -- the default here, so gate-override-only tests isolate the
    gate change without also changing which survivor ranks first."""
    return (-c["ifp_score"], c["base_range_pct"])


def _rank_key_v2(c: dict) -> tuple:
    """The alternate ranking from the (already-rejected) run #60/#61
    experiment -- only used when use_v2_ranking=True."""
    base_range_dist = abs(c["base_range_pct"] - BASE_RANGE_TARGET_PCT)
    return (base_range_dist, c["turnover_1m_avg_cr"], -c["ifp_score"])


async def build_candidates(
    pool, d: date, capital: float,
    gate_overrides: dict | None = None,
    use_v2_ranking: bool = False,
) -> list[dict]:
    """Same entry/sizing/caching plumbing as funnel.py's build_candidates()
    (reused directly, not duplicated) -- only the survivor gate thresholds
    and (optionally) the final ranking differ."""
    survivors = await funnel_survivors_v2(pool, d, gate_overrides)
    if not survivors:
        return []
    indicators_by_symbol = {row["symbol"]: row for row in survivors}
    symbols = list(indicators_by_symbol)

    cached_rows = await pool.fetch(
        "SELECT * FROM backtest_quant_signals WHERE signal_date = $1 AND symbol = ANY($2)",
        d, symbols,
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
                sym, d, result["passed"], result.get("entry"), result.get("sl"),
                result.get("entry_type"), result.get("base_stage"),
                result.get("risk_per_share"), result.get("target"),
                result.get("ifp_score"), result.get("base_range_pct"),
            ))
        await pool.executemany(
            """
            INSERT INTO backtest_quant_signals
              (symbol, signal_date, passed, entry, sl, entry_type, base_stage,
               risk_per_share, target, ifp_score, base_range_pct)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            ON CONFLICT (symbol, signal_date) DO NOTHING
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
            "ifp_score": float(sig["ifp_score"] or 0),
            "base_range_pct": float(sig["base_range_pct"] or 0),
            "turnover_1m_avg_cr": float(indicators_by_symbol[sym]["turnover_1m_avg_cr"] or 0),
        })
    candidates.sort(key=_rank_key_v2 if use_v2_ranking else _rank_key_v1)
    return candidates
