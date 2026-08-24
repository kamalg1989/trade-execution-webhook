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

    # Columns are capitalised — _rows_to_frame() renames to Open/High/Low/Close
    # because that is what screen_gpt expects. Reading lowercase raised KeyError
    # on the very first bar, which is the cheap way to find this; a detector
    # that silently returned [] would not have been.
    highs = [float(x) for x in df["High"].tolist()]
    lows = [float(x) for x in df["Low"].tolist()]
    closes = [float(x) for x in df["Close"].tolist()]

    bar_high, bar_low, bar_close = highs[-1], lows[-1], closes[-1]
    found: list[str] = []

    # ---- HIGH BREAKOUT: actually CLEARING the base, not merely near it -----
    #
    # Base high EXCLUDES today's bar: including it would make the test
    # self-referential — today's high is trivially "near" itself.
    #
    # STRENGTHENED after the first spot check (see ENTRY_V2_SPEC §2.1a). The
    # original `>= base_high OR within 2%` fired on 1,369 of 1,754 survivors —
    # 78% — because Stage 1 ALREADY gates on NEAR_BREAKOUT_MAX_DISTANCE = 5%.
    # Every survivor is near its base high by construction, so asking "is it
    # near the base high" re-tested an upstream filter and gated nothing.
    #
    # Now it must genuinely break out AND hold it into the close. Note this
    # REMOVES a tolerance rather than inventing a threshold — there is no new
    # number to fit. A bar that pokes above the base high and closes back below
    # is a failed breakout, which is the opposite of a buy signal.
    base_slice = highs[-(BASE_LOOKBACK_BARS + 1):-1]
    base_high = max(base_slice) if base_slice else 0.0
    if base_high and bar_high > base_high and bar_close > base_high:
        found.append("HIGH_BREAKOUT")

    # ---- PULLBACK: retrace into support with the trend intact --------------
    # EMA21 because that is the trail production already uses; introducing a
    # different MA here would add a parameter with no justification.
    #
    # The MAs are computed HERE from the close series rather than read off the
    # frame. load_ohlcv_frames_batch() returns OHLCV only, so an earlier version
    # that did `df["ema21"] if "ema21" in df` would have found no column and
    # silently never emitted a PULLBACK — a detector that is simply switched off
    # while appearing to work. Computing them locally makes this independent of
    # the frame's schema.
    close_s = df["Close"]
    ema21 = float(close_s.ewm(span=21, adjust=False).mean().iloc[-1])
    ema50 = (float(close_s.ewm(span=50, adjust=False).mean().iloc[-1])
             if len(close_s) >= 50 else None)
    sma200 = (float(close_s.rolling(200).mean().iloc[-1])
              if len(close_s) >= 200 else None)
    if not _isnan(ema21):
        trend_ok = ((sma200 is None or _isnan(sma200) or bar_close > sma200)
                    and (ema50 is None or _isnan(ema50) or bar_close > ema50))
        # Touched or breached the EMA intraday, but closed back above it —
        # a pullback that HOLDS. A close below is not a pullback, it is a break.
        if trend_ok and bar_low <= ema21 <= bar_close:
            found.append("PULLBACK")

    # ---- BREAKOUT RETEST: cleared the base, came back DOWN to test it ------
    #
    # STRENGTHENED after visual inspection (ENTRY_V2_SPEC §2.1b). The original
    # band was `low <= base_high * 1.02`, which permits the low to sit up to 2%
    # ABOVE the level. In a strong advance that passes trivially: JINDALSTEL on
    # 2023-01-02 broke out at 573.50 on 27 Dec then ran 578 -> 584 -> 598 -> 602
    # without ever returning, yet its low of 584.50 fell inside the band and it
    # was labelled a retest. Price was 5% above the level and still climbing.
    #
    # A retest means price came BACK DOWN to the breakout level. So the low must
    # actually reach it. Again this REMOVES a tolerance rather than adding a
    # parameter — there is no new number to fit.
    prior = highs[-(BASE_LOOKBACK_BARS + RETEST_MAX_BARS + 1):-(RETEST_MAX_BARS + 1)]
    if prior:
        prior_base_high = max(prior)
        recent = highs[-(RETEST_MAX_BARS + 1):-1]
        broke_out = any(h > prior_base_high for h in recent)
        # Low touches or breaches the old high (a genuine return to the level),
        # close still above it (the level held). A close below is a failed
        # retest, which is the opposite of a buy signal.
        if (broke_out and prior_base_high
                and bar_low <= prior_base_high
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
                # The bar that CROSSES the neckline, not any bar above it
                # (ENTRY_V2_SPEC §2.1c). The neckline is fixed by pivots inside
                # the window, so once price clears it `bar_high >= neck` stays
                # true for as long as the advance lasts. Measured: 100 fires
                # from 20 real setups — 5 per setup, half re-signalling for 4+
                # consecutive days, one for 12 — while the other three
                # detectors sat at ~1.4. A buy point is an EVENT; requiring
                # yesterday to be below the level makes it one, and adds no
                # parameter.
                prev_high = highs[-2] if len(highs) >= 2 else 0.0
                if neck > 0 and bar_high > neck and prev_high <= neck:
                    found.append("REVERSE_HS")

    return found


def _isnan(v) -> bool:
    try:
        return v != v
    except Exception:
        return True
