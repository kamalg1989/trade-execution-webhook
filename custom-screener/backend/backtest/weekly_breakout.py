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
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

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


def _find_box(df: pd.DataFrame, breakout_idx: int) -> tuple[int, int, float, float, float] | None:
    """Among window lengths BOX_MIN_WEEKS..BOX_MAX_WEEKS ending the week
    immediately before `breakout_idx`, returns the LONGEST window whose
    body-based depth is <= BOX_MAX_DEPTH_PCT, as
    (box_start_idx, box_weeks, box_top, box_bottom, depth_pct), or None if
    no length qualifies."""
    best = None
    for n in range(BOX_MIN_WEEKS, BOX_MAX_WEEKS + 1):
        start = breakout_idx - n
        if start < 0:
            break
        window = df.iloc[start:breakout_idx]
        box_top = window["body_hi"].max()
        box_bottom = window["body_lo"].min()
        if box_bottom <= 0:
            continue
        depth = (box_top - box_bottom) / box_bottom
        if depth <= BOX_MAX_DEPTH_PCT:
            best = (start, n, float(box_top), float(box_bottom), float(depth))
    return best


def scan_breakout(df: pd.DataFrame, breakout_idx: int, symbol: str) -> BoxBreakoutSignal | None:
    """Evaluates whether week `breakout_idx` (0-based row in the enriched
    weekly frame) is a valid breakout-candle signal. Returns None if any
    gate fails. `df` must already have compute_weekly_indicators() columns."""
    if breakout_idx < BOX_MIN_WEEKS + SMA_TREND_WEEKS:
        return None
    row = df.iloc[breakout_idx]
    close, high, low = float(row["Close"]), float(row["High"]), float(row["Low"])
    sma20 = row["sma20"]
    if pd.isna(sma20) or close <= sma20:
        return None
    sma_prior = df["sma20"].iloc[breakout_idx - SMA_RISING_LOOKBACK]
    if pd.isna(sma_prior) or sma20 <= sma_prior:
        return None  # SMA must be rising

    macd_line, macd_signal = row["macd_line"], row["macd_signal"]
    if pd.isna(macd_line) or pd.isna(macd_signal) or macd_line <= macd_signal:
        return None

    if row["upper_wick_pct"] >= UPPER_WICK_MAX_PCT:
        return None

    closing_high_10w = row["closing_high_10w"]
    if pd.isna(closing_high_10w) or close < closing_high_10w:
        return None  # must itself BE the 10-week closing high

    box = _find_box(df, breakout_idx)
    if box is None:
        return None
    box_start, box_weeks, box_top, box_bottom, depth = box

    pct_above = (close - box_top) / box_top
    if not (BREAKOUT_MIN_PCT <= pct_above <= BREAKOUT_MAX_PCT):
        return None

    box_window = df.iloc[box_start:breakout_idx]
    avg_box_volume = box_window["Volume"].mean()
    if avg_box_volume <= 0 or row["Volume"] < avg_box_volume * VOLUME_MIN_EXPANSION:
        return None

    # Liquidity floor (substitute for market cap — see module docstring).
    avg_turnover = (box_window["Volume"] * box_window["Close"]).tail(4).mean()
    if pd.isna(avg_turnover) or avg_turnover < MIN_WEEKLY_TURNOVER_RS:
        return None

    entry_trigger = round(close * 1.005, 2)  # small buffer above breakout week's close
    lower_third = box_bottom + (box_top - box_bottom) / 3.0
    stop_candidate = max(low, lower_third)
    max_risk_stop = entry_trigger * 0.85  # 15% distance cap
    initial_stop = round(max(stop_candidate, max_risk_stop), 2)
    if initial_stop >= entry_trigger:
        return None  # degenerate — stop above/at entry, skip

    return BoxBreakoutSignal(
        symbol=symbol, signal_week_end=row.name.date() if hasattr(row.name, "date") else row.name,
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
