"""Trade lifecycle simulator — entry-trigger fill checking and the
configurable exit-rule ladder. Pure functions over plain dicts, no DB access,
so this is unit-testable in isolation from the funnel/AI/engine plumbing.

`bar` dict per day: open/high/low/close always present; ema10/ema21/ema50/
atr14/swing_low are optional (None when unavailable — e.g. insufficient
history) and simply skip whichever mechanism needs them that day.

`cfg` dict (numeric run settings, merged into exit_config by engine.py so
callers only pass one dict):
  safety_sl_pct       - always-on intraday floor, % below entry (default 8)
  slippage_pct        - applied to every fill, worse for the trader
                         (buys fill higher, sells fill lower)
  brokerage_per_order - flat fee deducted at every fill event (entry +
                         each partial/final exit)
  chandelier_atr_mult - ATR multiple for the Chandelier trail

Exit-rule floor (always on, not a toggle): intraday -X% from entry (checked
against the day's low; gap-realistic — fills at the worse of the theoretical
stop price or the day's open, since a real stop can't fill better than the
market gapped) and close-based structural/trailing SL.

On top of that floor, independent toggles from exit_config:
  breakeven           - once the day's High implies unrealized gain >= +1R,
                         SL moves up to entry price (never below).
  half_booking        - once unrealized gain (via High) >= +2R, sell half at
                         the 2R price; if trailing is also on, SL on the
                         remainder moves to +1R (mirrors production
                         sl_engine.py Rule 2).
  trailing             - with half_booking on: after the +2R half-book, SL
                         keeps trailing to +(N-1)R as higher integer R-levels
                         are crossed (Rule 3). With half_booking OFF: the
                         *full* position trails the same way once +2R is
                         first crossed (production's "trail full" alternative).
  fixed_target        - if the position is still fully intact (no
                         half-booking has happened) and the day's High
                         reaches the 2R target, exit there.
  ema10_trail /
  ema21_trail /
  ema50_trail          - SL ratchets up to that EMA once the EMA is above the
                         current stop (never tightens below it — same max()
                         pattern as every other trail here). Multiple can be
                         on at once; the highest wins, which is correct.
  chandelier_trail     - SL ratchets up to (highest high since entry) minus
                         chandelier_atr_mult x ATR(14) — volatility-adaptive
                         trail instead of a fixed R-multiple.
  swing_trail          - SL ratchets up to the most recently confirmed swing
                         low (5-bar pivot, same definition as
                         ai_analysis/features/swings.py).
  failed_breakout_exit - close back below the entry trigger before the trade
                         has ever reached breakeven/half-booking invalidates
                         the setup outright — exit rather than wait for the
                         structural SL to eventually catch it.
  swing_break_exit     - close below the most recent confirmed swing low
                         exits immediately, independent of whether
                         swing_trail has already moved the SL there.
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
    entry_fill_price: float | None = None   # net (post-slippage) — the real cost basis
    qty_remaining: int = 0
    current_sl: float = 0.0
    moved_to_breakeven: bool = False
    half_booked: bool = False
    highest_r_acted: int = 0
    peak_high: float = 0.0           # highest bar high since fill — feeds the Chandelier trail
    realized_pnl: float = 0.0        # NET — accumulates half-book proceeds, then final exit, minus costs
    gross_pnl: float = 0.0           # frictionless (no slippage/brokerage) — for cost-drag comparison
    exit_date: object = None
    exit_price: float | None = None
    exit_reason: str | None = None
    partial_exits: list = field(default_factory=list)  # [{date, qty, price, reason}]

    # Identity fields owned by the engine's persistence/expiry logic. These
    # used to live in dicts keyed by Python's id(trade) (object memory
    # address) in engine.py -- a real bug: once a trade is dropped from the
    # `active` list and garbage-collected, CPython can reuse its address for
    # a *later, unrelated* trade, which would then silently match the old
    # trade's dict entry and overwrite its DB row / signal-day index instead
    # of getting its own. Keeping these as fields on the object itself (a
    # strong reference for its whole lifetime) makes that collision
    # impossible. See backtest post-mortem in git history for a real
    # instance of this (two unrelated symbols' fill data merged into one row).
    db_id: int | None = None
    signal_day_idx: int | None = None


def _buy_fill(gross_price: float, cfg: dict) -> float:
    """Slippage always works against the trader: buys fill higher."""
    return round(gross_price * (1 + cfg.get("slippage_pct", 0) / 100), 2)


def _sell_fill(gross_price: float, cfg: dict) -> float:
    """Slippage always works against the trader: sells fill lower."""
    return round(gross_price * (1 - cfg.get("slippage_pct", 0) / 100), 2)


def _leg_costs(value: float, cfg: dict, is_sell: bool) -> float:
    """Real Dhan equity-delivery cost of one fill leg (buy or sell), applied
    on top of slippage. Dhan charges zero brokerage on delivery — the old
    flat brokerage_per_order default (20) was actually Dhan's *intraday*
    rate, not delivery. Real delivery costs: STT on both legs, stamp duty on
    the buy leg only, small exchange+SEBI charges on both legs, and a flat
    per-scrip DP charge on the sell leg only (₹12.50 + 18% GST ≈ ₹14.75).
    `brokerage_per_order` is kept as a knob (defaults to 0) in case this is
    ever pointed at a different broker. `value` = qty x fill price for this
    leg. See sql/005_backtest_dhan_costs.sql for the sourced defaults."""
    stt = value * cfg.get("stt_pct", 0.1) / 100
    exch = value * cfg.get("exchange_charges_pct", 0.003) / 100
    stamp = 0.0 if is_sell else value * cfg.get("stamp_duty_pct", 0.015) / 100
    dp = cfg.get("dp_charge", 14.75) if is_sell else 0.0
    return stt + exch + stamp + dp + cfg.get("brokerage_per_order", 0)


def try_fill(trade: SimTrade, day: object, bar: dict, resting_window_days: int | None,
             trading_days_since_signal: int, cfg: dict | None = None) -> None:
    """Check one day's bar for a still-PENDING trade. Mutates trade in place."""
    cfg = cfg or {}
    if trade.status != "PENDING":
        return
    if bar["high"] >= trade.entry_trigger_price:
        trade.status = "OPEN"
        trade.entry_fill_date = day
        trade.entry_fill_price = _buy_fill(trade.entry_trigger_price, cfg)
        trade.qty_remaining = trade.quantity
        trade.current_sl = trade.structural_sl
        trade.peak_high = bar["high"]
        # Entry-leg costs (STT + stamp duty + exchange charges; no DP charge
        # on a buy), sunk immediately.
        trade.realized_pnl -= _leg_costs(trade.entry_fill_price * trade.quantity, cfg, is_sell=False)
        return
    if resting_window_days is not None and trading_days_since_signal > resting_window_days:
        trade.status = "UNFILLED_EXPIRED"


def step_exit(trade: SimTrade, day: object, bar: dict, exit_config: dict) -> None:
    """One day's exit-rule evaluation for an OPEN trade. `exit_config` carries
    both the boolean toggles and the numeric cfg fields (engine.py merges
    them before calling). Mutates trade in place; sets status=CLOSED (fully)
    once the position is flat."""
    if trade.status != "OPEN":
        return

    entry = trade.entry_fill_price
    risk = trade.risk_per_share
    trade.peak_high = max(trade.peak_high, bar["high"])

    # Floor, always on: intraday -X% from entry, checked against the low.
    # Gap-realistic fill: if the day's open already gapped through the stop,
    # a real order fills near the open, not at the untouched theoretical
    # stop price.
    safety_pct = exit_config.get("safety_sl_pct", 8.0)
    stop_floor = round(entry * (1 - safety_pct / 100), 2)
    if bar["low"] <= stop_floor:
        gross_exit = bar["open"] if bar["open"] < stop_floor else stop_floor
        _close(trade, day, gross_exit, "SAFETY_FLOOR", exit_config)
        return

    # Floor, always on: close-based stop at whatever the current SL is
    # (structural, or breakeven/trail-moved if those toggles are active).
    if bar["close"] < trade.current_sl:
        reason = "TRAIL_SL" if (trade.moved_to_breakeven or trade.half_booked) else "STRUCTURAL_SL"
        _close(trade, day, round(bar["close"], 2), reason, exit_config)
        return

    # Early invalidation: closed back below the breakout trigger before the
    # setup ever proved itself (no breakeven/half-book yet) — the failed
    # breakout is a tighter, earlier exit than waiting for structural SL.
    if (exit_config.get("failed_breakout_exit") and not trade.moved_to_breakeven
            and not trade.half_booked and bar["close"] < trade.entry_trigger_price):
        _close(trade, day, round(bar["close"], 2), "FAILED_BREAKOUT", exit_config)
        return

    # Time stop (sql/015). Only fires on a trade that has NOT yet proved
    # itself — once breakeven or half-booking has triggered, the position is
    # working and the clock must not touch it (winners here average 24 days
    # held, so a naive time stop would amputate exactly the trades that carry
    # the system). days_held is counted from the fill, not the signal.
    max_hold = exit_config.get("max_holding_days")
    if (max_hold and not trade.moved_to_breakeven and not trade.half_booked
            and trade.entry_fill_date is not None
            and (day - trade.entry_fill_date).days >= max_hold):
        _close(trade, day, round(bar["close"], 2), "TIME_STOP", exit_config)
        return

    swing_low = bar.get("swing_low")
    if (exit_config.get("swing_break_exit") and swing_low is not None
            and bar["close"] < swing_low):
        _close(trade, day, round(bar["close"], 2), "SWING_LOW_BREAK", exit_config)
        return

    up_r = int((bar["high"] - entry) // risk) if risk else 0

    if exit_config.get("breakeven") and not trade.moved_to_breakeven and up_r >= 1:
        trade.current_sl = max(trade.current_sl, entry)
        trade.moved_to_breakeven = True

    if up_r >= 2:
        if exit_config.get("half_booking") and not trade.half_booked:
            half_qty = trade.qty_remaining // 2
            if half_qty > 0:
                book_gross = round(entry + 2 * risk, 2)
                book_net = _sell_fill(book_gross, exit_config)
                trade.partial_exits.append({"date": day, "qty": half_qty,
                                             "price": book_net, "reason": "HALF_BOOK_2R"})
                trade.gross_pnl += (book_gross - trade.entry_trigger_price) * half_qty
                trade.realized_pnl += ((book_net - entry) * half_qty
                                        - _leg_costs(book_net * half_qty, exit_config, is_sell=True))
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

    # Trend-following trails — each independently ratchets the SL up (never
    # down) if its level is currently above the stop. Several can be on at
    # once; whichever is tightest (highest) wins, same as every other trail.
    for key in ("ema10_trail", "ema21_trail", "ema50_trail"):
        if exit_config.get(key):
            ema_val = bar.get(key.replace("_trail", ""))  # "ema10"/"ema21"/"ema50"
            if ema_val is not None:
                trade.current_sl = max(trade.current_sl, ema_val)

    if exit_config.get("chandelier_trail"):
        atr14 = bar.get("atr14")
        if atr14 is not None:
            mult = exit_config.get("chandelier_atr_mult", 3.0)
            trade.current_sl = max(trade.current_sl, round(trade.peak_high - mult * atr14, 2))

    if exit_config.get("swing_trail") and swing_low is not None:
        trade.current_sl = max(trade.current_sl, swing_low)

    if (exit_config.get("fixed_target") and not trade.half_booked
            and bar["high"] >= trade.target_price):
        _close(trade, day, trade.target_price, "TARGET_2R", exit_config)
        return


def _close(trade: SimTrade, day: object, gross_price: float, reason: str, cfg: dict) -> None:
    net_price = _sell_fill(gross_price, cfg)
    trade.gross_pnl += (gross_price - trade.entry_trigger_price) * trade.qty_remaining
    trade.realized_pnl += ((net_price - trade.entry_fill_price) * trade.qty_remaining
                            - _leg_costs(net_price * trade.qty_remaining, cfg, is_sell=True))
    trade.qty_remaining = 0
    trade.status = "CLOSED"
    trade.exit_date = day
    trade.exit_price = net_price
    trade.exit_reason = reason


def mark_to_market_open(trade: SimTrade, last_close: float) -> float:
    """Unrealized P&L on the still-open remainder as of the window's last
    close — does not close the trade, just returns the figure for display."""
    if trade.status != "OPEN" or not trade.entry_fill_price:
        return 0.0
    return round((last_close - trade.entry_fill_price) * trade.qty_remaining, 2)
