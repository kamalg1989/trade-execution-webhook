"""Weekly Consolidation Breakout Strategy — backtest-only implementation of
the "Financial Wisdom Weekly Consolidation Breakout Strategy" spec (supplied
by the user 2026-08-14; see chat history for the full spec text). Completely
independent of the daily quant funnel (screen_gpt.py) used everywhere else
in this backtest engine — different timeframe (weekly vs daily), different
indicators (20-week SMA + weekly MACD(12,26,9) vs the daily EMA/ATR/base-
stage system), different exit mechanism (MACD bearish-crossover trailing
stop, see weekly_simulator.py, vs the R-multiple/EMA ladder in simulator.py).
Shares only: the underlying OHLCV data (via the precomputed ohlcv_weekly
table, sql/025) and the backtest_trades table shape (see engine.py's
weekly run path for how fields are repurposed). Dispatched from
engine.run_backtest() via backtest_runs.strategy == 'WEEKLY_BREAKOUT'.

Data limitations vs the literal spec (disclosed to the user 2026-08-14,
before building):
  - No real (₹) market cap in this DB — no shares-outstanding data anywhere.
    symbols_meta.mcap_bucket is a coarse, CURRENT-day label derived from
    today's Nifty index membership (Large=Nifty100/Mid=Midcap150/
    Small=Smallcap250; ~77% of symbols unclassified) — applying it
    retroactively across a 2016-2026 backtest would be look-ahead biased
    (a stock's index membership in 2026 doesn't reflect its 2016 status).
    Substituted with a turnover-based liquidity floor instead (computed
    from the weekly bars themselves, so it's point-in-time correct) — this
    is NOT equivalent to the user's requested ₹10cr market-cap floor, just
    a basic "not a dead/illiquid stock" sanity check given data constraints.
  - "Book value" trend isn't available; the fundamentals filter checks
    revenue + net profit trend only (via earnings_fundamentals), using only
    broadcasts dated before the signal week to avoid look-ahead.

Box-geometry interpretation notes (the spec describes the box in prose, not
exact formulas — these are the concrete rules actually implemented):
  - Box top/bottom are drawn through candle BODIES (max/min of Open,Close
    over the window), not wick extremes, per the spec's explicit instruction.
  - Box duration: tries every length from BOX_MIN_WEEKS to BOX_MAX_WEEKS
    ending the week before the breakout candle, and picks the LONGEST
    window whose depth is within BOX_MAX_DEPTH_PCT — "longer consolidations
    build larger energy bases" per the spec, so longer is preferred among
    otherwise-qualifying candidates.
  - "Rising" 20-week SMA: current 20W SMA > 20W SMA four weeks ago.
  - MACD filter: MACD line above signal line at the breakout week's close
    (the spec's "or a fresh bullish crossover near the zero line" is a
    softer alternative condition not encoded here — kept to the stricter,
    unambiguous reading).

Performance (2026-08-14): the original scan was a pure-Python loop calling
`df.iloc[idx]` and re-slicing/aggregating a 6-26-week box window PER WEEK PER
SYMBOL — O(symbols x weeks x 21 box lengths) of live pandas scalar/slice ops,
which is what made the full 2016-2026 run take ~10+ minutes just for the
signal scan. `_scan_prep()` now does the equivalent work as vectorized numpy
array ops, computed ONCE per symbol:
  1. The five gate conditions (above/rising SMA, MACD, wick, closing-high)
     become one boolean array via vectorized comparisons instead of a
     per-week scalar row extraction + branch.
  2. All 21 box-length top/bottom/volume-mean series become rolling-window
     columns (`.rolling(n).max()/.min()/.mean()`, each O(weeks) in C, done
     once) instead of 21 live slice+aggregate calls PER CANDIDATE WEEK.
The per-week Python loop then only runs over weeks that already pass the
fast gate array (usually a small fraction of total weeks), and does O(1)
array lookups instead of O(21) live aggregations for the box search.
These arrays are intentionally NOT stored back onto the shared indicator
frame (the one held in weekly_engine.frames for the whole run) — that would
add ~1GB of resident memory across ~2300 symbols on a box with under 2GB
total RAM. They're plain numpy arrays local to one _scan_symbol_signals()
call, garbage collected once that symbol's scan finishes.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

BOX_MIN_WEEKS = 6
BOX_MAX_WEEKS = 26                 # spec gives no explicit cap; 6 months is a reasonable ceiling
BOX_MAX_DEPTH_PCT = 0.35           # hard cap from spec ("maximum capped at 35%")
BOX_IDEAL_DEPTH_PCT = 0.20         # spec's "ideally under 20%" — used only for scoring/reporting
BREAKOUT_MIN_PCT = 0.05            # 5% above box top
BREAKOUT_MAX_PCT = 0.20            # 20% above box top
CLOSING_HIGH_LOOKBACK_WEEKS = 10   # "10-week closing high"
VOLUME_MIN_EXPANSION = 1.10        # >=10% above avg box-week volume (spec's stated minimum)
UPPER_WICK_MAX_PCT = 0.50          # reject if upper wick >= 50% of candle range
SMA_TREND_WEEKS = 20
SMA_RISING_LOOKBACK = 4
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9

# Liquidity floor substituting for the requested ₹10cr market-cap filter (see
# module docstring) — deliberately loose, since ₹10cr market cap itself is a
# very low bar; this just excludes genuinely dead/illiquid names. Computed as
# avg(weekly volume x weekly close) over the trailing 4 weeks, i.e. a rough
# weekly-turnover-in-rupees proxy.
MIN_WEEKLY_TURNOVER_RS = 2_500_000  # ₹25 lakh/week ≈ ₹5L/day, a low liquidity floor


@dataclass
class BoxBreakoutSignal:
    symbol: str
    signal_week_end: date          # the breakout candle's week_end (Friday)
    box_start_idx: int
    box_weeks: int
    box_top: float
    box_bottom: float
    box_depth_pct: float
    breakout_close: float
    breakout_high: float
    breakout_low: float
    entry_trigger: float           # breakout week close + small buffer, filled next week
    initial_stop: float
    risk_pct: float


def compute_weekly_indicators(weekly: pd.DataFrame) -> pd.DataFrame:
    """Adds sma20, macd_line, macd_signal, body_hi, body_lo, upper_wick_pct,
    closing_high_10w columns to a weekly OHLCV frame (Open/High/Low/Close/
    Volume, indexed by week_end date, ascending). Pure/vectorized — computed
    once per symbol per run, not per scan-week, since none of these depend
    on a particular "as of" date."""
    df = weekly.copy()
    df["sma20"] = df["Close"].rolling(SMA_TREND_WEEKS).mean()
    ema_fast = df["Close"].ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = df["Close"].ewm(span=MACD_SLOW, adjust=False).mean()
    df["macd_line"] = ema_fast - ema_slow
    df["macd_signal"] = df["macd_line"].ewm(span=MACD_SIGNAL, adjust=False).mean()
    df["body_hi"] = df[["Open", "Close"]].max(axis=1)
    df["body_lo"] = df[["Open", "Close"]].min(axis=1)
    rng = (df["High"] - df["Low"]).replace(0, pd.NA)
    df["upper_wick_pct"] = ((df["High"] - df["body_hi"]) / rng).fillna(0.0)
    df["closing_high_10w"] = df["Close"].rolling(CLOSING_HIGH_LOOKBACK_WEEKS).max()
    return df


def _scan_prep(df: pd.DataFrame) -> tuple:
    """Vectorized gate array + rolling box/volume/turnover lookups for one
    symbol's full history — see the perf note in the module docstring.
    Returns (fast_pass, rollups, turnover_4wk):
      fast_pass    — bool ndarray, True where a week passes ALL of: above a
                     rising 20W SMA, MACD bullish, wick ok, is the 10W
                     closing high. NaN comparisons evaluate False, same as
                     the original scalar pd.isna() checks they replace.
      rollups      — {n: (box_top ndarray, box_bottom ndarray,
                     avg_volume ndarray)} for n in BOX_MIN_WEEKS..BOX_MAX_WEEKS,
                     each value at index i = the aggregate over the n weeks
                     STRICTLY BEFORE i (via .shift(1)), matching the original
                     df.iloc[breakout_idx-n:breakout_idx] window exactly.
      turnover_4wk — ndarray, trailing-4-week avg(volume x close) ending the
                     week before i (box-length independent, since the spec's
                     turnover check is always `.tail(4)` of whatever box
                     window was chosen).
    """
    sma20 = df["sma20"]
    fast_pass = (
        (df["Close"] > sma20)
        & (sma20 > sma20.shift(SMA_RISING_LOOKBACK))
        & (df["macd_line"] > df["macd_signal"])
        & (df["upper_wick_pct"] < UPPER_WICK_MAX_PCT)
        & (df["Close"] >= df["closing_high_10w"])
    ).to_numpy()

    rollups = {}
    for n in range(BOX_MIN_WEEKS, BOX_MAX_WEEKS + 1):
        rollups[n] = (
            df["body_hi"].rolling(n).max().shift(1).to_numpy(),
            df["body_lo"].rolling(n).min().shift(1).to_numpy(),
            df["Volume"].rolling(n).mean().shift(1).to_numpy(),
        )
    turnover_4wk = ((df["Volume"] * df["Close"]).rolling(4).mean().shift(1)).to_numpy()
    return fast_pass, rollups, turnover_4wk


def _find_box(rollups: dict, breakout_idx: int) -> tuple[int, int, float, float, float] | None:
    """Among window lengths BOX_MIN_WEEKS..BOX_MAX_WEEKS ending the week
    immediately before `breakout_idx`, returns the LONGEST window whose
    body-based depth is <= BOX_MAX_DEPTH_PCT, as
    (box_start_idx, box_weeks, box_top, box_bottom, depth_pct), or None if
    no length qualifies. O(21) array lookups against the rollups precomputed
    once per symbol by _scan_prep(), instead of 21 live slice+aggregate
    calls per candidate week."""
    best = None
    for n in range(BOX_MIN_WEEKS, BOX_MAX_WEEKS + 1):
        start = breakout_idx - n
        if start < 0:
            break
        box_top, box_bottom, _ = rollups[n]
        top, bottom = box_top[breakout_idx], box_bottom[breakout_idx]
        if np.isnan(top) or np.isnan(bottom) or bottom <= 0:
            continue
        depth = (top - bottom) / bottom
        if depth <= BOX_MAX_DEPTH_PCT:
            best = (start, n, float(top), float(bottom), float(depth))
    return best


def scan_breakout(df: pd.DataFrame, breakout_idx: int, symbol: str,
                   rollups: dict, turnover_4wk) -> BoxBreakoutSignal | None:
    """Evaluates whether week `breakout_idx` (0-based row in the enriched
    weekly frame) is a valid breakout-candle signal, given the rollups/
    turnover_4wk precomputed by _scan_prep(). Callers should pre-filter to
    weeks where _scan_prep()'s fast_pass[breakout_idx] is True — this
    function does NOT re-check SMA/MACD/wick/closing-high itself (those are
    fully vectorized already), only the box search, breakout-size, volume-
    expansion, turnover and stop/entry math, none of which can be reduced to
    a single array op since the box length is chosen per-candidate."""
    close = float(df["Close"].iat[breakout_idx])
    high = float(df["High"].iat[breakout_idx])
    low = float(df["Low"].iat[breakout_idx])

    box = _find_box(rollups, breakout_idx)
    if box is None:
        return None
    box_start, box_weeks, box_top, box_bottom, depth = box

    pct_above = (close - box_top) / box_top
    if not (BREAKOUT_MIN_PCT <= pct_above <= BREAKOUT_MAX_PCT):
        return None

    volume = float(df["Volume"].iat[breakout_idx])
    avg_box_volume = rollups[box_weeks][2][breakout_idx]
    if np.isnan(avg_box_volume) or avg_box_volume <= 0 or volume < avg_box_volume * VOLUME_MIN_EXPANSION:
        return None

    # Liquidity floor (substitute for market cap — see module docstring).
    avg_turnover = turnover_4wk[breakout_idx]
    if np.isnan(avg_turnover) or avg_turnover < MIN_WEEKLY_TURNOVER_RS:
        return None

    entry_trigger = round(close * 1.005, 2)  # small buffer above breakout week's close
    lower_third = box_bottom + (box_top - box_bottom) / 3.0
    stop_candidate = max(low, lower_third)
    max_risk_stop = entry_trigger * 0.85  # 15% distance cap
    initial_stop = round(max(stop_candidate, max_risk_stop), 2)
    if initial_stop >= entry_trigger:
        return None  # degenerate — stop above/at entry, skip

    week_end = df.index[breakout_idx]
    return BoxBreakoutSignal(
        symbol=symbol, signal_week_end=week_end.date() if hasattr(week_end, "date") else week_end,
        box_start_idx=box_start, box_weeks=box_weeks, box_top=box_top, box_bottom=box_bottom,
        box_depth_pct=depth, breakout_close=close, breakout_high=high, breakout_low=low,
        entry_trigger=entry_trigger, initial_stop=initial_stop,
        risk_pct=round((entry_trigger - initial_stop) / entry_trigger * 100, 2),
    )


def size_position(capital: float, entry: float, stop: float, risk_pct: float = 1.0,
                   max_capital_pct: float = 25.0) -> int:
    """Position Size ($) = Equity x Account Risk% / Trade Risk% — implemented
    directly off entry/stop (risk_per_share), which is mathematically
    equivalent and avoids a second definition of "trade risk %" drifting out
    of sync with the actual stop distance. max_capital_pct is an added
    engineering safeguard (not in the literal spec) to prevent extreme
    concentration on unusually tight stops — disclosed to the user."""
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return 0
    qty_risk = int((capital * risk_pct / 100) / risk_per_share)
    qty_cap = int((capital * max_capital_pct / 100) / entry)
    return max(0, min(qty_risk, qty_cap))


async def fundamentals_pass(pool, symbol: str, as_of: date) -> bool:
    """Revenue AND net profit both higher than the same-period figure one
    reporting period further back, using only earnings broadcast before
    `as_of` (point-in-time safe — no look-ahead). Returns True (pass) if
    fewer than 2 qualifying reports exist yet, i.e. doesn't block a young
    stock with sparse fundamentals history; only rejects on a CONFIRMED
    downtrend."""
    rows = await pool.fetch(
        """
        SELECT revenue, net_profit FROM earnings_fundamentals
        WHERE symbol = $1 AND broadcast_date < $2 AND revenue IS NOT NULL AND net_profit IS NOT NULL
        ORDER BY period_to DESC LIMIT 2
        """,
        symbol, as_of,
    )
    if len(rows) < 2:
        return True
    latest, prior = rows[0], rows[1]
    return float(latest["revenue"]) >= float(prior["revenue"]) and float(latest["net_profit"]) >= float(prior["net_profit"])


async def macd_ratchet_series(pool, symbol: str, upto: date) -> list[tuple[date, float | None]]:
    """Weekly-MACD bearish-crossover ratchet level over a symbol's history
    through `upto` — used by the daily engine's optional `macd_trail` exit
    (see simulator.step_exit), a slower/less noise-sensitive alternative to
    the EMA10/21/50 trails. Motivated by run #589 (see chat 2026-08-14):
    the WEEKLY_BREAKOUT strategy's MACD-crossover trail let winners run to
    +2.09R on average vs +1.15R for the daily strategy's EMA21 trail on the
    same measure, because it only reacts to a full week's bearish close, not
    daily noise.

    Level starts at None (no crossover yet -> exit purely on structural
    SL/other rules, this trail simply doesn't apply) and ratchets up (never
    down) to that week's Low each time a fresh bearish crossover occurs —
    exactly weekly_simulator.step_exit_weekly's ratchet rule, just without a
    structural_sl anchor to start from (the daily engine's own structural SL
    already exists independently; this trail only ever raises it further).

    Returns one (week_end, level) pair per week (forward-filled), so a
    caller can bisect straight to "the level as of any given day" without
    re-walking the whole history — see macd_trail_level_at()."""
    rows = await pool.fetch(
        "SELECT week_end, open, high, low, close, volume FROM ohlcv_weekly "
        "WHERE symbol = $1 AND week_end <= $2 ORDER BY week_end ASC",
        symbol, upto,
    )
    if not rows:
        return []
    df = pd.DataFrame([dict(r) for r in rows])
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                             "close": "Close", "volume": "Volume"})
    df = df[["Open", "High", "Low", "Close", "Volume", "week_end"]].astype(
        {"Open": float, "High": float, "Low": float, "Close": float, "Volume": float}
    )
    df = df.set_index("week_end")
    df = compute_weekly_indicators(df)

    out: list[tuple[date, float | None]] = []
    level: float | None = None
    prev_macd, prev_signal = None, None
    for week_end, row in df.iterrows():
        macd_line, macd_signal = row["macd_line"], row["macd_signal"]
        if (pd.notna(macd_line) and pd.notna(macd_signal)
                and prev_macd is not None and pd.notna(prev_macd) and pd.notna(prev_signal)):
            was_bullish_or_flat = prev_macd >= prev_signal
            now_bearish = macd_line < macd_signal
            if was_bullish_or_flat and now_bearish:
                new_level = round(float(row["Low"]), 2)
                level = new_level if level is None else max(level, new_level)
        d = week_end.date() if hasattr(week_end, "date") else week_end
        out.append((d, level))
        prev_macd, prev_signal = macd_line, macd_signal
    return out


def macd_trail_level_at(series: list[tuple[date, float | None]], day: date) -> float | None:
    """Ratchet level CONFIRMED as of `day` — the level from the most recent
    week whose week_end <= day, since that week's close (and therefore
    whether it was a bearish crossover) is only known once the week is over.
    `series` must be sorted ascending by week_end (macd_ratchet_series()
    already returns it that way). O(log weeks) via bisect."""
    if not series:
        return None
    week_ends = [d for d, _ in series]
    i = bisect_left(week_ends, day)
    # week_ends[i] is the first entry >= day. If it equals day exactly, that
    # week's own close IS known by end of that trading day -- include it.
    if i < len(week_ends) and week_ends[i] == day:
        return series[i][1]
    if i == 0:
        return None
    return series[i - 1][1]
