"""Weekly-cadence trade lifecycle for the Weekly Consolidation Breakout
strategy — see weekly_breakout.py for signal generation. Deliberately
separate from simulator.py (the daily engine's exit ladder): this strategy's
exit mechanism (MACD bearish-crossover trailing stop, evaluated once per
week) has nothing in common with the R-multiple/EMA/chandelier system used
everywhere else in this backtest engine.

Fill/exit realism mirrors simulator.py's conventions where they translate
directly to weekly bars: gap-realistic fills (a stop/entry can't fill better
than the week's Open if price gapped through it), slippage always working
against the trader. Costs (brokerage/STT/etc.) reuse simulator.py's
_leg_costs()/_buy_fill()/_sell_fill() helpers directly rather than
duplicating the Dhan cost model.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .simulator import _buy_fill, _leg_costs, _sell_fill


@dataclass
class WeeklyTrade:
    symbol: str
    signal_week_end: object            # date of the breakout candle's week
    entry_trigger_price: float
    structural_sl: float
    risk_per_share: float
    quantity: int
    box_weeks: int
    box_depth_pct: float

    status: str = "PENDING"            # PENDING | OPEN | CLOSED | UNFILLED_EXPIRED
    entry_fill_date: object = None
    entry_fill_price: float | None = None
    current_sl: float = 0.0
    macd_trail_active: bool = False    # True once the first bearish crossover has set a trail stop
    realized_pnl: float = 0.0
    gross_pnl: float = 0.0
    exit_date: object = None
    exit_price: float | None = None
    exit_reason: str | None = None

    # Phase 2 (2026-08-17, CAGR-optimization) — optional breakeven-stop-move
    # and partial profit-booking, ported from simulator.py's Rule 2/3 (see
    # step_exit's up_r block). Both off by default (cfg flags weekly_
    # breakeven_enabled/weekly_half_booking_enabled) so an unconfigured run
    # is byte-identical to before these existed. qty_remaining tracks live
    # position size for a partially-booked trade — quantity itself stays the
    # ORIGINAL fill size (needed for r_multiple = realized_pnl / (risk *
    # quantity), same convention as engine.py's SimTrade).
    qty_remaining: int = 0
    moved_to_breakeven: bool = False
    half_booked: bool = False
    partial_exits: list = field(default_factory=list)

    db_id: int | None = None
    signal_week_idx: int | None = None


def try_fill_weekly(trade: WeeklyTrade, week_end: object, bar: dict,
                     resting_window_weeks: int, weeks_since_signal: int, cfg: dict) -> None:
    """One week's bar for a still-PENDING trade — entry is a buy-stop a
    small buffer above the breakout week's close (set at signal time),
    filled if this (the following) week's High reaches it."""
    if trade.status != "PENDING":
        return
    if bar["high"] >= trade.entry_trigger_price:
        trade.status = "OPEN"
        trade.entry_fill_date = week_end
        fill_gross = max(trade.entry_trigger_price, bar["open"])  # gap-realistic
        trade.entry_fill_price = _buy_fill(fill_gross, cfg)
        trade.current_sl = trade.structural_sl
        trade.qty_remaining = trade.quantity
        trade.realized_pnl -= _leg_costs(trade.entry_fill_price * trade.quantity, cfg, is_sell=False)
        return
    if resting_window_weeks is not None and weeks_since_signal > resting_window_weeks:
        trade.status = "UNFILLED_EXPIRED"


def step_exit_weekly(trade: WeeklyTrade, week_end: object, bar: dict, cfg: dict) -> None:
    """One week's bar for an OPEN trade. `bar` carries this week's OHLC plus
    macd_line/macd_signal/macd_line_prev/macd_signal_prev (previous week's
    values, needed to detect the crossover itself, not just the current
    state) computed by the caller from the precomputed indicator frame.

    Exit ladder (evaluated in order — first match wins):
      1. Hard structural stop (always active): this week's Low breaches
         current_sl -> exit, gap-realistic against the week's Open.
      2. Breakeven (opt-in, weekly_breakeven_enabled): once this week's High
         has moved >= +1R above entry and breakeven hasn't already
         triggered, current_sl ratchets up to entry — never loosens.
      3. Half-booking (opt-in, weekly_half_booking_enabled): once this
         week's High has moved >= +2R above entry and half-booking hasn't
         already triggered, sell half the remaining quantity at entry+2R
         and ratchet current_sl to entry+1R for the remainder. Same ordering
         as simulator.py's Rule 2/3 — stop-breach is checked first against
         THIS week's Low before any High-based profit-taking, so a week that
         both dips through the stop and spikes above +2R is treated as a
         stop-out, not a book (conservative, matches the daily engine).
      4. MACD bearish crossover (macd_line was >= signal last week, now <
         signal this week): ratchets current_sl up to THIS week's Low (the
         "Execution Line" in the spec) — never loosens an existing tighter
         stop. Does not itself close the trade the week it triggers; the
         position exits only once a LATER week's Low actually breaches that
         level (per spec: "If price breaches that level in subsequent
         weeks, close the position").
    """
    if trade.status != "OPEN":
        return

    if bar["low"] <= trade.current_sl:
        gross_exit = bar["open"] if bar["open"] < trade.current_sl else trade.current_sl
        _close_weekly(trade, week_end, gross_exit,
                      "MACD_TRAIL_SL" if trade.macd_trail_active else "STRUCTURAL_SL", cfg)
        return

    entry = trade.entry_fill_price
    risk = trade.risk_per_share
    up_r = (bar["high"] - entry) / risk if risk else 0.0

    if cfg.get("weekly_breakeven_enabled") and not trade.moved_to_breakeven and up_r >= 1:
        trade.current_sl = max(trade.current_sl, entry)
        trade.moved_to_breakeven = True

    if cfg.get("weekly_half_booking_enabled") and not trade.half_booked and up_r >= 2:
        half_qty = trade.qty_remaining // 2
        if half_qty > 0:
            book_gross = round(entry + 2 * risk, 2)
            book_net = _sell_fill(book_gross, cfg)
            trade.partial_exits.append({"date": week_end, "qty": half_qty,
                                         "price": book_net, "reason": "HALF_BOOK_2R"})
            trade.gross_pnl += (book_gross - trade.entry_trigger_price) * half_qty
            trade.realized_pnl += ((book_net - entry) * half_qty
                                    - _leg_costs(book_net * half_qty, cfg, is_sell=True))
            trade.qty_remaining -= half_qty
        trade.half_booked = True
        trade.current_sl = max(trade.current_sl, round(entry + 1 * risk, 2))

    macd_line, macd_signal = bar.get("macd_line"), bar.get("macd_signal")
    macd_line_prev, macd_signal_prev = bar.get("macd_line_prev"), bar.get("macd_signal_prev")
    if None not in (macd_line, macd_signal, macd_line_prev, macd_signal_prev):
        was_bullish_or_flat = macd_line_prev >= macd_signal_prev
        now_bearish = macd_line < macd_signal
        if was_bullish_or_flat and now_bearish:
            trade.current_sl = max(trade.current_sl, round(bar["low"], 2))
            trade.macd_trail_active = True


def check_daily_stop_breach(trade: WeeklyTrade, day: object, daily_bar: dict, cfg: dict) -> bool:
    """Daily-cadence stop-breach check (sql/030 `weekly_daily_exit_check`
    toggle) — checks current_sl (whatever the last-known structural or
    MACD-ratchet level is) against a DAILY bar's Low instead of waiting for
    the week to close. The MACD ratchet level itself still only updates once
    a week completes (see update_macd_ratchet below — MACD is inherently a
    weekly indicator here); this only makes the BREACH of an already-set
    level react daily instead of weekly, so a reversal is caught sooner at
    the cost of more whipsaw exits on daily noise (the same daily-EMA21 vs
    weekly-MACD trail-off measured in the daily engine's macd_trail
    experiment, run #589 analysis, just applied within this strategy
    itself). Returns True if the trade was closed."""
    if trade.status != "OPEN":
        return False
    if daily_bar["low"] <= trade.current_sl:
        gross_exit = daily_bar["open"] if daily_bar["open"] < trade.current_sl else trade.current_sl
        _close_weekly(trade, day, gross_exit,
                      "MACD_TRAIL_SL_DAILY" if trade.macd_trail_active else "STRUCTURAL_SL_DAILY", cfg)
        return True
    return False


def update_macd_ratchet(trade: WeeklyTrade, week_end: object, bar: dict) -> None:
    """Just the crossover-ratchet-update half of step_exit_weekly, with NO
    stop-breach check — used alongside check_daily_stop_breach() above when
    `weekly_daily_exit_check` is on, so the breach check (daily) and the
    ratchet update (still weekly) run on their own natural cadences instead
    of both being folded into a single week-end step."""
    if trade.status != "OPEN":
        return
    macd_line, macd_signal = bar.get("macd_line"), bar.get("macd_signal")
    macd_line_prev, macd_signal_prev = bar.get("macd_line_prev"), bar.get("macd_signal_prev")
    if None not in (macd_line, macd_signal, macd_line_prev, macd_signal_prev):
        was_bullish_or_flat = macd_line_prev >= macd_signal_prev
        now_bearish = macd_line < macd_signal
        if was_bullish_or_flat and now_bearish:
            trade.current_sl = max(trade.current_sl, round(bar["low"], 2))
            trade.macd_trail_active = True


def _close_weekly(trade: WeeklyTrade, week_end: object, gross_price: float, reason: str, cfg: dict) -> None:
    """Closes whatever quantity is still open (qty_remaining — the full
    original quantity unless a prior half-booking already sold part of it).
    gross_pnl/realized_pnl accumulate (+=) rather than overwrite so a
    half-booked trade's final close adds to, rather than erases, the P&L
    already realized at the +2R partial exit."""
    net_price = _sell_fill(gross_price, cfg)
    trade.gross_pnl += (gross_price - trade.entry_trigger_price) * trade.qty_remaining
    trade.realized_pnl += ((net_price - trade.entry_fill_price) * trade.qty_remaining
                            - _leg_costs(net_price * trade.qty_remaining, cfg, is_sell=True))
    trade.qty_remaining = 0
    trade.status = "CLOSED"
    trade.exit_date = week_end
    trade.exit_price = net_price
    trade.exit_reason = reason
