"""Experimental quant candidate ranking — a standalone variant for
validating whether the production funnel's selection criteria (screen_gpt.py
/ funnel.py, live-trading code) actually predict forward returns, BEFORE
ever proposing a change there. This module is never imported by production
code or the live screener — only by the backtest engine, and only when a
run explicitly opts into `quant_funnel_variant='v2'`.

Why these specific changes — factor analysis behind this (run #55, 172
closed QUANT trades, joined to stock_indicators at signal time; see chat
history for the full quartile breakdown):

  - Turnover/liquidity: MONOTONIC INVERSE relationship with win rate. The
    least-liquid quartile of survivors (still above the production minimum
    gate) won 41.9% (+0.21R); the most-liquid quartile won only 27.9%
    (-0.11R). Production doesn't rank by this at all today.
        -> v2 ranks turnover ASCENDING (lower/less-liquid-but-gated wins),
           inverting the implicit "bigger/more liquid is safer" assumption.

  - Base tightness: production ranks the TIGHTEST base as best
    (base_range_pct ascending). But by quartile, the tightest quartile
    (<12.2%) won only 27.9% (+0.02R) while the 3rd quartile (~15.0-17.5%,
    NOT the tightest) won 48.8% (+0.47R) -- the best bucket in the whole
    analysis. The very tightest consolidations may be over-compressed
    rather than higher quality.
        -> v2 ranks by distance from a 16.25% target (the empirical Q3
           band center) instead of "tighter is always better".

  - Prior upmove: names barely clearing the existing gate underperform
    badly -- bottom quartile (<25.7%) won only 18.6% (-0.14R) vs the 3rd
    quartile winning 44.2% (+0.26R).
        -> v2 raises the effective gate floor to 26% (just above the
           observed p25), on top of production's existing (looser) floor.

  - Volume dry-up: names with the LEAST actual dry-up (loosest pass on the
    existing ratio gate) underperform -- top quartile (ratio > 0.99) won
    only 20.9% (-0.10R) vs the 2nd quartile winning 41.9% (+0.28R).
        -> v2 tightens the effective gate ceiling to 0.99 (just below the
           observed p75), on top of production's existing (looser) ceiling.

Everything else (liquidity/technical/IFP gates, base-stage classification,
entry-technique resolution, position sizing) is untouched -- reused
directly from funnel.py so this can never silently drift from production
parity on the parts we are NOT experimenting on. Only the ranking (which
survivors become rank 1/2/3) and the two extra gate thresholds above are
different, which isolates the "is the selection/ranking logic good?"
question from everything else already validated in this backtest tool.

CAVEAT (read before trusting a "this is better" result): the thresholds
above were derived FROM this same Jan-Aug 2026 window, so a rerun over the
identical window improving is partly expected by construction, not
independent proof of forward edge. Treat a same-window result as an
internal-consistency check, not validation -- a genuine test needs either
a held-out date range or fresh data as this window ages.
"""
from __future__ import annotations

from datetime import date

from . import funnel as v1

# Extra gate thresholds, tighter than production's (see module docstring for
# how these were derived). Applied on top of every existing funnel.py gate
# (liquidity, technical, base-quality, IFP) -- this can only ever shrink the
# survivor pool relative to v1, never grow it.
MIN_PRIOR_UPMOVE_PCT_V2 = 26.0
MAX_VOL_DRYUP_RATIO_V2 = 0.99
BASE_RANGE_TARGET_PCT = 16.25  # empirical Q3 band center -- rank target, not a gate

# Identical to funnel.py's GATE_SQL on every existing gate ($1-$9, same
# order) -- only adds the 2 new WHERE clauses ($10, $11) and the extra
# SELECT columns the re-ranking below needs (turnover, prior_upmove,
# vol_dryup are already computed in stock_indicators, no new indicator work).
GATE_SQL_V2 = """
    SELECT symbol, close, base_range_20d_pct, ifp_score,
           turnover_1m_avg_cr, prior_upmove_pct, vol_dryup_ratio
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
      AND prior_upmove_pct >= $10
      AND vol_dryup_ratio <= $11
    ORDER BY symbol
"""


async def funnel_survivors_v2(pool, d: date) -> list[dict]:
    import screen_gpt  # same production constants as funnel.py, for gate parity
    rows = await pool.fetch(
        GATE_SQL_V2, d,
        screen_gpt.MIN_DAILY_TURNOVER / 1e7,
        screen_gpt.TECH_MAX_BASE_RANGE * 100,
        screen_gpt.TECH_VOL_MULT,
        screen_gpt.BASE_MIN_PRIOR_UPMOVE_PCT * 100,
        screen_gpt.BASE_MAX_GIVEBACK_PCT * 100,
        screen_gpt.BASE_VOL_DRYUP_MAX_RATIO,
        -screen_gpt.NEAR_BREAKOUT_MAX_DISTANCE * 100,
        screen_gpt.IFP_MIN_SCORE,
        MIN_PRIOR_UPMOVE_PCT_V2,
        MAX_VOL_DRYUP_RATIO_V2,
    )
    return [dict(r) for r in rows]


def _rank_key(c: dict) -> tuple:
    """Best (lowest key) first: base-tightness distance from the empirical
    sweet spot, then turnover ascending (less liquid wins), then ifp_score
    descending as a tie-break (not contradicted by the analysis, kept)."""
    base_range_dist = abs(c["base_range_pct"] - BASE_RANGE_TARGET_PCT)
    return (base_range_dist, c["turnover_1m_avg_cr"], -c["ifp_score"])


async def build_candidates(pool, d: date, capital: float) -> list[dict]:
    """Same entry/sizing/caching plumbing as funnel.py's build_candidates()
    (reused directly, not duplicated) -- only the survivor gate and the
    final ranking differ."""
    survivors = await funnel_survivors_v2(pool, d)
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
        to_insert = []
        for sym in todo:
            result = v1._compute_signal(frames.get(sym), sym, indicators_by_symbol[sym])
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
    candidates.sort(key=_rank_key)
    return candidates
