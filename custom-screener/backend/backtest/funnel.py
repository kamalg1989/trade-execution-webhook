"""Day-by-day funnel replay.

The liquidity/technical/base-quality/IFP gates are plain SQL against
stock_indicators (already vectorized in exact parity with screen_gpt.py —
see custom-screener/backend/compute/indicators.py's docstring). Only the two
things NOT precomputed there — base-stage classification and entry-technique
detection, both of which need the raw OHLCV bars, not just that day's
snapshot — fall through to the real screen_gpt.py functions for perfect
parity with production (imported directly, not reimplemented).

Performance: the per-symbol OHLCV load + base-stage/entry-technique/target
computation is cached in backtest_quant_signals, keyed by (symbol,
signal_date) — same idea as backtest_ai_signals for the AI track. A rerun of
an already-computed date, or a new run whose window overlaps an old one,
skips straight to a cache lookup for every symbol instead of re-querying
OHLCV and re-running classify_base_stage()/resolve_entry() over it. The one
thing deliberately NOT cached is quantity — it depends on the run's capital,
so it's recomputed cheaply from cached entry/sl/base_stage at
candidate-assembly time (_size_qty below) instead of being baked into the
cached row.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date

import pandas as pd

sys.path.insert(0, "/root/trade-execution-webhook")
import screen_gpt  # noqa: E402  — real production funnel/entry/sizing logic

GATE_SQL = """
    SELECT symbol, close, base_range_20d_pct, ifp_score
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
# ORDER BY symbol matters here, not just for tidiness: without it Postgres is
# free to return survivor rows in a different order on every execution, and
# since candidates.sort() below is a *stable* sort, a tie on (ifp_score,
# base_range_pct) at the top-3 cutoff could silently flip which symbol wins
# between two otherwise-identical runs. This makes the funnel's output fully
# deterministic for a given day, independent of query-plan/row-order noise.


async def funnel_survivors(pool, d: date) -> list[dict]:
    """Day-D funnel survivors straight from stock_indicators — liquidity,
    technical, base-quality and IFP gates, thresholds pulled live from
    screen_gpt's own constants so this can never drift out of parity."""
    rows = await pool.fetch(
        GATE_SQL, d,
        screen_gpt.MIN_DAILY_TURNOVER / 1e7,
        screen_gpt.TECH_MAX_BASE_RANGE * 100,
        screen_gpt.TECH_VOL_MULT,
        screen_gpt.BASE_MIN_PRIOR_UPMOVE_PCT * 100,
        screen_gpt.BASE_MAX_GIVEBACK_PCT * 100,
        screen_gpt.BASE_VOL_DRYUP_MAX_RATIO,
        -screen_gpt.NEAR_BREAKOUT_MAX_DISTANCE * 100,
        screen_gpt.IFP_MIN_SCORE,
    )
    return [dict(r) for r in rows]


async def load_ohlcv_frame(pool, symbol: str, upto: date, bars: int = 400) -> pd.DataFrame | None:
    """Trailing OHLCV window ending on `upto`, shaped exactly like
    screen_gpt.fetch_from_db()'s output (Open/High/Low/Close/Volume,
    datetime index) so screen_gpt's functions work on it unmodified."""
    rows = await pool.fetch(
        """
        SELECT time, open, high, low, close, volume FROM (
          SELECT time, open, high, low, close, volume,
                 row_number() OVER (ORDER BY time DESC) AS rn
          FROM ohlcv_data WHERE symbol = $1 AND time::date <= $2
        ) t WHERE rn <= $3 ORDER BY time ASC
        """,
        symbol, upto, bars,
    )
    return _rows_to_frame(rows)


async def load_ohlcv_frames_batch(pool, symbols: list[str], upto: date, bars: int = 400) -> dict[str, pd.DataFrame]:
    """Same as load_ohlcv_frame but for many symbols in one round trip
    (partitioned window function) instead of N sequential per-symbol
    queries — this was the dominant cost of a quant-track backtest day
    (dozens of sequential awaited DB calls before this)."""
    if not symbols:
        return {}
    rows = await pool.fetch(
        """
        SELECT symbol, time, open, high, low, close, volume FROM (
          SELECT symbol, time, open, high, low, close, volume,
                 row_number() OVER (PARTITION BY symbol ORDER BY time DESC) AS rn
          FROM ohlcv_data WHERE symbol = ANY($1) AND time::date <= $2
        ) t WHERE rn <= $3 ORDER BY symbol, time ASC
        """,
        symbols, upto, bars,
    )
    by_symbol: dict[str, list] = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"], []).append(r)
    return {sym: f for sym, rs in by_symbol.items() if (f := _rows_to_frame(rs)) is not None}


def _rows_to_frame(rows) -> pd.DataFrame | None:
    if len(rows) < 50:
        return None
    df = pd.DataFrame([dict(r) for r in rows])
    df["Date"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert("Asia/Kolkata")
    df.set_index("Date", inplace=True)
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                             "close": "Close", "volume": "Volume"})
    df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


def _compute_signal(df: pd.DataFrame | None, symbol: str, indicators_row: dict) -> dict:
    """Everything about a (symbol, day) funnel result that's independent of
    capital: base-stage classification + entry-technique resolution (via
    screen_gpt.resolve_entry(), NOT screen_gpt.create_trade() — the latter
    also gates on qty>0, which depends on CAPITAL and would make a cached
    'rejected' wrongly sticky across runs with a different capital)."""
    if df is None or len(df) < 200:
        return {"passed": False}
    sym_ns = symbol + ".NS"
    stage, _ = screen_gpt.classify_base_stage(df, symbol=sym_ns)
    if stage > screen_gpt.BASE_STAGE_MAX_ALLOWED:
        return {"passed": False}
    trigger = screen_gpt.resolve_entry(df, sym_ns)
    if trigger is None:
        return {"passed": False}
    entry, sl = trigger["entry"], trigger["sl"]
    risk_per_share = entry - sl
    if risk_per_share <= 0:
        return {"passed": False}
    target = screen_gpt.compute_target(entry, sl, symbol=sym_ns)
    return {
        "passed": True, "entry": entry, "sl": sl, "entry_type": trigger["type"],
        "base_stage": stage, "risk_per_share": round(risk_per_share, 2), "target": target,
        "ifp_score": float(indicators_row["ifp_score"] or 0),
        "base_range_pct": float(indicators_row["base_range_20d_pct"] or 0),
    }


async def _compute_signals_concurrent(
    symbols: list[str], frames: dict, indicators_by_symbol: dict, concurrency: int = 4,
) -> dict[str, dict]:
    """Run _compute_signal (CPU-bound pandas: base-stage classify + entry-
    technique resolve) across symbols in a small thread pool instead of a
    sequential Python for-loop — this was the dominant per-day cost on
    dates with many not-yet-cached symbols. Concurrency kept low: the VPS
    is a 2-vCPU, ~2GB box shared with several other always-on services."""
    sem = asyncio.Semaphore(concurrency)

    async def _one(sym: str):
        async with sem:
            return sym, await asyncio.to_thread(_compute_signal, frames.get(sym), sym, indicators_by_symbol[sym])

    pairs = await asyncio.gather(*(_one(s) for s in symbols))
    return dict(pairs)


# Production's two hardcoded sizing literals from screen_gpt.create_trade().
# Used whenever a run doesn't override them (sql/013), so default behavior is
# byte-identical to before those knobs existed.
PROD_RISK_PER_TRADE_PCT = 0.25
PROD_MAX_CAPITAL_PER_TRADE_PCT = 10.0


CONTRACTION_RECENT_BARS = 10   # the "current, tightest" leg of the base
CONTRACTION_PRIOR_BARS = 15    # the wider leg immediately before it


async def contraction_ratios(pool, symbols: list[str], d: date) -> dict[str, float]:
    """VCP-style progressive-contraction measure per symbol as of day `d`
    (see sql/014 for the motivation and the measured edge).

        ratio = high-low range of the last 10 sessions
              / high-low range of the 15 sessions before those

    < 1 means the base is tightening into the pivot (what a VCP/pennant
    actually looks like); >= 1 means it's flat or widening. Computed in one
    round trip for every symbol, entirely from ohlcv_data — screen_gpt.py
    itself is untouched, so this can never affect live screening.

    Symbols with no usable prior range are simply omitted, and the caller
    treats "missing" as "don't filter it out" so a data gap can never
    silently suppress an entry."""
    if not symbols:
        return {}
    rows = await pool.fetch(
        """
        SELECT symbol,
               (MAX(high) FILTER (WHERE rn <= $3) - MIN(low) FILTER (WHERE rn <= $3))
             / NULLIF(MAX(high) FILTER (WHERE rn > $3) - MIN(low) FILTER (WHERE rn > $3), 0)
               AS ratio
        FROM (
          SELECT symbol, high, low,
                 row_number() OVER (PARTITION BY symbol ORDER BY time DESC) AS rn
          FROM ohlcv_data
          WHERE symbol = ANY($1) AND time::date < $2
        ) t
        WHERE rn <= $4
        GROUP BY symbol
        """,
        symbols, d, CONTRACTION_RECENT_BARS,
        CONTRACTION_RECENT_BARS + CONTRACTION_PRIOR_BARS,
    )
    return {r["symbol"]: float(r["ratio"]) for r in rows if r["ratio"] is not None}


def _size_qty(capital: float, base_stage: int, entry: float, risk_per_share: float,
              sizing: dict | None = None) -> int:
    """Mirrors screen_gpt.create_trade()'s sizing formula — kept here instead
    of calling create_trade() so quantity can be computed per-run from a
    cached, capital-independent signal. Must stay in sync if those literals
    ever change in screen_gpt.py.

    `sizing` (optional, backtest-only — see sql/013) may override either
    literal: {"risk_per_trade_pct": 0.5, "max_capital_per_trade_pct": 15}.
    Omitted/None keys fall back to production's values."""
    s = sizing or {}
    risk_pct = s.get("risk_per_trade_pct") or PROD_RISK_PER_TRADE_PCT
    cap_pct = s.get("max_capital_per_trade_pct") or PROD_MAX_CAPITAL_PER_TRADE_PCT
    stage_mult = screen_gpt.BASE_STAGE_SIZE_MULTIPLIER.get(base_stage, screen_gpt.BASE_STAGE_DEFAULT_MULTIPLIER)
    qty_risk = int(capital * (risk_pct / 100) * stage_mult / risk_per_share)
    qty_cap = int(capital * (cap_pct / 100) * stage_mult / entry)
    return min(qty_risk, qty_cap)


async def build_candidates(pool, d: date, capital: float, sizing: dict | None = None) -> list[dict]:
    """Full per-day candidate build: funnel survivors -> base-stage classify
    -> entry-technique/trigger resolve (cached) -> position size (not
    cached, capital-dependent). Returns only the stocks that pass every
    remaining gate, ranked (best first) same as screen_gpt.rank_candidates():
    -ifp_score, then base_range_pct (the base_quality_score tie-break is
    skipped — every SQL survivor already has a perfect 1.0 base_quality_score
    by construction, same as production)."""
    survivors = await funnel_survivors(pool, d)
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
        frames = await load_ohlcv_frames_batch(pool, todo, d)
        computed = await _compute_signals_concurrent(todo, frames, indicators_by_symbol)
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
        qty = _size_qty(capital, base_stage, entry, risk_per_share, sizing)
        if qty <= 0:
            continue
        candidates.append({
            "symbol": sym, "entry": entry, "sl": sl, "qty": qty,
            "entry_type": sig["entry_type"], "base_stage": base_stage,
            "risk_per_share": risk_per_share,
            "target": float(sig["target"]) if sig.get("target") is not None else 0.0,
            "ifp_score": float(sig["ifp_score"] or 0),
            "base_range_pct": float(sig["base_range_pct"] or 0),
        })
    candidates.sort(key=lambda c: (-c["ifp_score"], c["base_range_pct"]))
    return candidates
