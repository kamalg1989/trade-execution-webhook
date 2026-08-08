"""Trade lifecycle simulator — entry-trigger fill checking and the
configurable exit-rule ladder. Pure functions over plain dicts, no DB access,
so this is unit-testable in isolation from the funnel/AI/engine plumbing.

Exit-rule floor (always on, not a toggle): intraday -8% from entry (checked
against the day's low) and close-based structural SL. On top of that floor,
four independent toggles from exit_config:
  breakeven    - once the day's High implies unrealized gain >= +1R, SL moves
                 up to entry price (never below).
  half_booking - once unrealized gain (via High) >= +2R, sell half at the 2R
                 price; if trailing is also on, SL on the remainder moves to
                 +1R (mirrors production sl_engine.py Rule 2).
  trailing     - with half_booking on: after the +2R half-book, SL keeps
                 trailing to +(N-1)R as higher integer R-levels are crossed
                 (Rule 3). With half_booking OFF: the *full* position trails
                 the same way once +2R is first crossed, matching production's
                 "trail full instead of booking" alternative.
  fixed_target - if the position is still fully intact (no half-booking has
                 happened) and the day's High reaches the 2R target, exit
                 there. Never fires on a day half-booking already fired.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SimTrade:
    symbol: str
    signal_date: object
    entry_trigger_price: float
    structural_sl: float
    target_price: float
    risk_per_share: float
    quantity: int
    entry_type: str
    base_stage: int
    quant_rank: int | None = None
    ai_rank: int | None = None
    ai_confidence: float | None = None
    ai_recommendation: str | None = None

    status: str = "PENDING"          # PENDING | OPEN | CLOSED | UNFILLED_EXPIRED | SUPERSEDED
    entry_fill_date: object = None
    entry_fill_price: float | None = None
    qty_remaining: int = 0
    current_sl: float = 0.0
    moved_to_breakeven: bool = False
    half_booked: bool = False
    highest_r_acted: int = 0
    realized_pnl: float = 0.0        # accumulates half-book proceeds, then final exit
    exit_date: object = None
    exit_price: float | None = None
    exit_reason: str | None = None
    partial_exits: list = field(default_factory=list)  # [{date, qty, price, reason}]


def try_fill(trade: SimTrade, day: object, bar: dict, resting_window_days: int | None,
             trading_days_since_signal: int) -> None:
    """Check one day's bar for a still-PENDING trade. Mutates trade in place."""
    if trade.status != "PENDING":
        return
    if bar["high"] >= trade.entry_trigger_price:
        trade.status = "OPEN"
        trade.entry_fill_date = day
        trade.entry_fill_price = trade.entry_trigger_price
        trade.qty_remaining = trade.quantity
        trade.current_sl = trade.structural_sl
        return
    if resting_window_days is not None and trading_days_since_signal > resting_window_days:
        trade.status = "UNFILLED_EXPIRED"


def _r_at(trade: SimTrade, price: float) -> float:
    if not trade.risk_per_share:
        return 0.0
    return (price - trade.entry_fill_price) / trade.risk_per_share


def step_exit(trade: SimTrade, day: object, bar: dict, exit_config: dict) -> None:
    """One day's exit-rule evaluation for an OPEN trade. Mutates trade in
    place; sets status=CLOSED (fully) once the position is flat."""
    if trade.status != "OPEN":
        return

    entry = trade.entry_fill_price
    risk = trade.risk_per_share

    # Floor, always on: intraday -8% from entry, checked against the low.
    stop8 = round(entry * 0.92, 2)
    if bar["low"] <= stop8:
        _close(trade, day, stop8, "MINUS_8PCT")
        return

    # Floor, always on: close-based stop at whatever the current SL is
    # (structural, or breakeven/trail-moved if those toggles are active).
    if bar["close"] < trade.current_sl:
        reason = "TRAIL_SL" if (trade.moved_to_breakeven or trade.half_booked) else "STRUCTURAL_SL"
        _close(trade, day, round(bar["close"], 2), reason)
        return

    up_r = int((bar["high"] - entry) // risk) if risk else 0

    if exit_config.get("breakeven") and not trade.moved_to_breakeven and up_r >= 1:
        trade.current_sl = max(trade.current_sl, entry)
        trade.moved_to_breakeven = True

    if up_r >= 2:
        if exit_config.get("half_booking") and not trade.half_booked:
            half_qty = trade.qty_remaining // 2
            if half_qty > 0:
                book_price = round(entry + 2 * risk, 2)
                trade.partial_exits.append({"date": day, "qty": half_qty,
                                             "price": book_price, "reason": "HALF_BOOK_2R"})
                trade.realized_pnl += (book_price - entry) * half_qty
                trade.qty_remaining -= half_qty
            trade.half_booked = True
            trade.highest_r_acted = 2
            if exit_config.get("trailing"):
                trade.current_sl = max(trade.current_sl, round(entry + 1 * risk, 2))
        elif exit_config.get("trailing") and not exit_config.get("half_booking"):
            # "Trail full" alternative — no booking, just ratchet the stop.
            trade.current_sl = max(trade.current_sl, round(entry + (up_r - 1) * risk, 2))
            trade.highest_r_acted = up_r
        elif (exit_config.get("trailing") and trade.half_booked
              and up_r >= 3 and up_r > trade.highest_r_acted):
            trade.current_sl = max(trade.current_sl, round(entry + (up_r - 1) * risk, 2))
            trade.highest_r_acted = up_r

    if (exit_config.get("fixed_target") and not trade.half_booked
            and bar["high"] >= trade.target_price):
        _close(trade, day, trade.target_price, "TARGET_2R")
        return


def _close(trade: SimTrade, day: object, price: float, reason: str) -> None:
    trade.realized_pnl += (price - trade.entry_fill_price) * trade.qty_remaining
    trade.qty_remaining = 0
    trade.status = "CLOSED"
    trade.exit_date = day
    trade.exit_price = price
    trade.exit_reason = reason


def mark_to_market_open(trade: SimTrade, last_close: float) -> float:
    """Unrealized P&L on the still-open remainder as of the window's last
    close — does not close the trade, just returns the figure for display."""
    if trade.status != "OPEN" or not trade.entry_fill_price:
        return 0.0
    return round((last_close - trade.entry_fill_price) * trade.qty_remaining, 2)
