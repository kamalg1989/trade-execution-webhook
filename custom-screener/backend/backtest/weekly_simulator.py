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
      2. MACD bearish crossover (macd_line was >= signal last week, now <
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

    macd_line, macd_signal = bar.get("macd_line"), bar.get("macd_signal")
    macd_line_prev, macd_signal_prev = bar.get("macd_line_prev"), bar.get("macd_signal_prev")
    if None not in (macd_line, macd_signal, macd_line_prev, macd_signal_prev):
        was_bullish_or_flat = macd_line_prev >= macd_signal_prev
        now_bearish = macd_line < macd_signal
        if was_bullish_or_flat and now_bearish:
            trade.current_sl = max(trade.current_sl, round(bar["low"], 2))
            trade.macd_trail_active = True


def _close_weekly(trade: WeeklyTrade, week_end: object, gross_price: float, reason: str, cfg: dict) -> None:
    net_price = _sell_fill(gross_price, cfg)
    trade.gross_pnl = (gross_price - trade.entry_trigger_price) * trade.quantity
    trade.realized_pnl += ((net_price - trade.entry_fill_price) * trade.quantity
                            - _leg_costs(net_price * trade.quantity, cfg, is_sell=True))
    trade.status = "CLOSED"
    trade.exit_date = week_end
    trade.exit_price = net_price
    trade.exit_reason = reason
