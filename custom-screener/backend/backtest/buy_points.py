"""Buy-point detection — WHERE in the base we are, as distinct from WHETHER
today's bar is actionable.

The deck ("When to Buy", slide 15) separates two things production has flattened
into one list:

    buy point  -> pullback | reverse H&S | high breakout | breakout retest
    trigger    -> hammer(pin) | HH-HL | inside bar | trend bar

screen_gpt.detect_entry_technique() only ever answers the second. PULLBACK and
BREAKOUT_RETEST exist but sit at the BOTTOM of resolve_entry() as fallbacks,
reached only when no candle matched — and both are switched off. So production
today fires on a trend bar anywhere in the base, with no notion of location.

This module answers the first question. Entry v2 requires BOTH: a recognised buy
point AND a valid trigger. That is strictly fewer signals than today.

EVERY THRESHOLD HERE IS PRE-REGISTERED in /ENTRY_V2_SPEC.md and must not be
tuned against results. Four detectors with three knobs each is twelve dimensions
of search surface, and twelve dimensions will always yield a winner — which is
how seven ideas in this project already died. Values are tied to numbers
production uses elsewhere rather than chosen to make an output look good.

Pure functions over a pandas OHLCV frame. No DB, no I/O, unit-testable.
"""
from __future__ import annotations

# --- pre-registered constants (see ENTRY_V2_SPEC.md §2.1) -------------------

BASE_LOOKBACK_BARS = 20      # matches screen_gpt.BASE_LOOKBACK_BARS
NEAR_PCT = 0.02              # "at" a level = within 2%. Production's Stage-1
                             # NEAR_BREAKOUT_MAX_DISTANCE is 5%; at the trigger
                             # we want tighter, and 2% is one round step below.
RETEST_MAX_BARS = 10         # half the base lookback. A "retest" arriving later
                             # than half a base-length is a new base.
HS_WINDOW = 60               # ~1 quarter: the shortest window in which a
                             # three-trough structure is visible on daily bars.
HS_SYMMETRY = 0.15           # |left - right| <= 15% of head depth. Deliberately
                             # loose: a tight tolerance finds almost nothing and
                             # would be a fitted parameter.
PIVOT_HALF = 2               # 5-bar fractal (centre lowest of 5), same
                             # definition as ai_analysis/features/swings.py.


def _near(value: float, level: float, pct: float = NEAR_PCT) -> bool:
    """Within pct of a level, from either side."""
    return level > 0 and abs(value - level) / level <= pct


def _swing_lows(lows: list[float]) -> list[int]:
    """Indices of 5-bar fractal lows. Shared definition with swings.py rather
    than a new one — an independently invented pivot rule would be another free
    parameter with nothing behind it."""
    out = []
    for i in range(PIVOT_HALF, len(lows) - PIVOT_HALF):
        window = lows[i - PIVOT_HALF:i + PIVOT_HALF + 1]
        if lows[i] == min(window) and window.count(lows[i]) == 1:
            out.append(i)
    return out


def detect_buy_points(df, symbol: str = "?") -> list[str]:
    """Every buy point today's bar satisfies, or [] if none.

    Returns a LIST, not a first-match: a bar can legitimately be both a high
    breakout and a breakout retest, and collapsing that to one label would hide
    it. The caller decides what to do with multiples; recording all of them
    means the choice can be measured later instead of being baked in now.
    """
    if df is None or len(df) < PIVOT_HALF * 2 + 6:
        return []

    highs = [float(x) for x in df["high"].tolist()]
    lows = [float(x) for x in df["low"].tolist()]
    closes = [float(x) for x in df["close"].tolist()]

    bar_high, bar_low, bar_close = highs[-1], lows[-1], closes[-1]
    found: list[str] = []

    # ---- HIGH BREAKOUT: at the top of the base, about to clear it ----------
    # Base high EXCLUDES today's bar: including it would make the test
    # self-referential — today's high is trivially "near" itself.
    base_slice = highs[-(BASE_LOOKBACK_BARS + 1):-1]
    base_high = max(base_slice) if base_slice else 0.0
    if base_high and (bar_high >= base_high or _near(bar_high, base_high)):
        found.append("HIGH_BREAKOUT")

    # ---- PULLBACK: retrace into support with the trend intact --------------
    # EMA21 because that is the trail production already uses; introducing a
    # different MA here would add a parameter with no justification.
    ema21 = df["ema21"].iloc[-1] if "ema21" in df else None
    ema50 = df["ema50"].iloc[-1] if "ema50" in df else None
    sma200 = df["sma200"].iloc[-1] if "sma200" in df else None
    if ema21 is not None and not _isnan(ema21):
        trend_ok = ((sma200 is None or _isnan(sma200) or bar_close > float(sma200))
                    and (ema50 is None or _isnan(ema50) or bar_close > float(ema50)))
        # Touched or breached the EMA intraday, but closed back above it —
        # a pullback that HOLDS. A close below is not a pullback, it is a break.
        if trend_ok and bar_low <= float(ema21) <= bar_close:
            found.append("PULLBACK")

    # ---- BREAKOUT RETEST: cleared the base, came back to test it -----------
    prior = highs[-(BASE_LOOKBACK_BARS + RETEST_MAX_BARS + 1):-(RETEST_MAX_BARS + 1)]
    if prior:
        prior_base_high = max(prior)
        recent = highs[-(RETEST_MAX_BARS + 1):-1]
        broke_out = any(h > prior_base_high for h in recent)
        # Came back DOWN to the level and held it: low at/below the old high,
        # close still above. That is the retest; a close below is a failure.
        if (broke_out and prior_base_high
                and bar_low <= prior_base_high * (1 + NEAR_PCT)
                and bar_close > prior_base_high):
            found.append("BREAKOUT_RETEST")

    # ---- REVERSE HEAD & SHOULDERS -----------------------------------------
    win_lows = lows[-HS_WINDOW:]
    piv = _swing_lows(win_lows)
    if len(piv) >= 3:
        # Most recent three troughs. Scanning ALL combinations would be a
        # search over the pattern space rather than a detection of it.
        li, hi_, ri = piv[-3], piv[-2], piv[-1]
        left, head, right = win_lows[li], win_lows[hi_], win_lows[ri]
        if head < left and head < right:
            depth = min(left, right) - head
            if depth > 0 and abs(left - right) <= HS_SYMMETRY * depth:
                win_highs = highs[-HS_WINDOW:]
                neck = max(max(win_highs[li:hi_ + 1]), max(win_highs[hi_:ri + 1]))
                if neck > 0 and (bar_high >= neck or _near(bar_high, neck)):
                    found.append("REVERSE_HS")

    return found


def _isnan(v) -> bool:
    try:
        return v != v
    except Exception:
        return True
