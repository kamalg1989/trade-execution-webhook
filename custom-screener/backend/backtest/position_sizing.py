"""
Unified position sizing system with compounding support.

Replaces scattered sizing logic across multiple engines with a single,
testable, configurable PositionSizer class that:
- Manages running capital (with profit/drawdown-aware compounding)
- Applies all config filters consistently
- Sizes positions using risk/capital constraints
- Tracks cumulative P&L for compounding
- Works identically across all strategies

Usage:
    sizer = PositionSizer(
        initial_capital=400000,
        risk_per_trade_pct=0.25,
        compounding_enabled=True,
        compounding_mode="drawdown_aware",
    )

    qty = sizer.size_position(entry_price=100, stop_price=85)

    # When trade closes:
    sizer.record_trade_closed(realized_pnl=500)
"""

from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


class PositionSizer:
    """Unified position sizing with compounding support."""

    def __init__(
        self,
        initial_capital: float,
        risk_per_trade_pct: float = 0.25,
        max_capital_per_trade_pct: float = 10.0,
        compounding_enabled: bool = False,
        compounding_mode: str = "profit_only",
        compounding_min_capital: Optional[float] = None,
        min_position_value: float = 0.0,
        compounding_max_capital: Optional[float] = None,
        adv_position_cap_pct: Optional[float] = None,
        **config
    ):
        """
        Initialize position sizer with compounding support.

        Args:
            initial_capital: Starting capital in ₹
            risk_per_trade_pct: Account risk % per trade (default 0.25%)
            max_capital_per_trade_pct: Max position size as % of capital (default 10%)
            compounding_enabled: Enable position compounding on profits
            compounding_mode: "profit_only" (grows/shrinks) or "drawdown_aware" (only grows)
            compounding_min_capital: Floor for running capital (defaults to initial_capital)
            min_position_value: Minimum position size in ₹ (skip smaller positions)
            **config: Additional config for filters (entry_breadth, etc.)
        """
        self.initial_capital = initial_capital
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_capital_per_trade_pct = max_capital_per_trade_pct
        self.compounding_enabled = compounding_enabled
        self.compounding_mode = compounding_mode
        self.compounding_min_capital = compounding_min_capital or initial_capital
        self.min_position_value = min_position_value
        # 2026-08-17 risk audit (V5) — CODE-ENFORCED guards, not guidelines:
        #   compounding_max_capital: hard ceiling on the equity used for
        #     sizing. Beyond it the book stops scaling — the capacity report
        #     showed the composite ranking's low-turnover edge stops absorbing
        #     size around Rs.20L equity (20 trades >10% of ADV on the
        #     uncapped compounding path). Excess capital above the ceiling is
        #     the caller's to deploy elsewhere (INDEX_TF has no capacity
        #     limit at this account size).
        #   adv_position_cap_pct: no single position may exceed this % of the
        #     stock's 1-month average daily traded value. Preserves access to
        #     illiquid names at bounded size instead of excluding them, which
        #     is the correct resolution of "the edge IS illiquidity" vs
        #     "illiquidity caps size". Enforced only when the caller supplies
        #     adv_value (None = data unavailable -> cap not applied, same
        #     missing-data convention as everywhere else in this engine).
        self.compounding_max_capital = compounding_max_capital
        self.adv_position_cap_pct = adv_position_cap_pct
        self.config = config

        # Cumulative P&L tracking for compounding
        self.cumulative_realized_pnl = 0.0
        self.trade_count = 0
        self.total_realized_pnl = 0.0

        logger.info(
            f"PositionSizer initialized: capital={initial_capital}, "
            f"risk={risk_per_trade_pct}%, compounding={compounding_enabled} "
            f"({compounding_mode})"
        )

    def get_running_capital(self) -> float:
        """
        Calculate running capital based on compounding mode.

        Returns:
            Running capital for position sizing.

        Modes:
            - profit_only: running = initial + cumulative_pnl (grows AND shrinks)
            - drawdown_aware: running = max(floor, initial + cumulative_pnl) (only grows)
        """
        if not self.compounding_enabled:
            return self.initial_capital

        if self.compounding_mode == "profit_only":
            # Capital grows with wins, shrinks with losses
            running = self.initial_capital + self.cumulative_realized_pnl
        else:  # "drawdown_aware"
            # Capital only grows, floor prevents shrinkage
            running = max(
                self.compounding_min_capital,
                self.initial_capital + self.cumulative_realized_pnl,
            )

        # Ensure minimum floor
        running = max(running, self.compounding_min_capital)
        # Hard ceiling (see __init__ audit comment) — sizing stops scaling
        # here even though the account keeps growing.
        if self.compounding_max_capital is not None:
            running = min(running, self.compounding_max_capital)
        return running

    def size_position(self, entry_price: float, stop_price: float,
                       committed_capital: float = 0.0,
                       size_scale: float = 1.0,
                       adv_value: Optional[float] = None) -> int:
        """
        Calculate position size based on running capital and risk parameters.

        Args:
            entry_price: Entry price in ₹
            stop_price: Stop-loss price in ₹
            committed_capital: ₹ value already tied up in OTHER currently-open
                positions (entry_price * qty_remaining, summed). Caller is
                responsible for computing this from its own active-trades
                list — PositionSizer has no visibility into open positions,
                only into closed-trade P&L (for compounding). Defaults to 0
                (no portfolio-level cap applied), so callers that don't pass
                it get the pre-existing, uncapped behavior.

        Returns:
            Quantity to buy (0 if position doesn't meet constraints)

        Logic:
            1. Calculate risk per share (entry - stop)
            2. Size based on risk: qty = (capital * risk_pct) / risk_per_share
               — risk % is of TOTAL equity, standard convention, unaffected
               by what else is currently open.
            3. Cap based on max_capital: qty = (capital * max_capital_pct) / entry
            4. Cap based on AVAILABLE capital: qty = (capital - committed) / entry
               — this is the portfolio-level exposure cap: total concurrent
               open-position value can never exceed running_capital, however
               many positions try to open at once (added 2026-08-17, see
               finding that WEEKLY_BREAKOUT could carry 20-30+ concurrent
               positions worth several multiples of stated capital, since
               max_picks only throttled NEW entries per period, not the
               running total of still-open ones).
            5. Take smallest of the three.
            6. Filter by min_position_value if set.
        """
        running_capital = self.get_running_capital()
        risk_per_share = entry_price - stop_price

        if risk_per_share <= 0:
            logger.warning(
                f"Invalid risk: entry={entry_price}, stop={stop_price}. "
                f"Risk per share must be positive."
            )
            return 0

        # size_scale (default 1.0 = no change) scales the two RISK BUDGETS but
        # deliberately NOT qty_available below. A circuit breaker is a choice to
        # take less risk than allowed; available cash is a hard constraint. If
        # size_scale also cut qty_available it would model the account as having
        # less money than it does, which is a different (and wrong) thing.
        scale = max(0.0, size_scale)

        # Risk-based sizing: position risk = (qty * risk_per_share)
        qty_risk = int(
            (running_capital * self.risk_per_trade_pct / 100 * scale) / risk_per_share
        )

        # Capital constraint: position value = (qty * entry_price)
        qty_capital = int(
            (running_capital * self.max_capital_per_trade_pct / 100 * scale) / entry_price
        )

        # Portfolio-level cap: don't deploy more than what isn't already
        # tied up in other open positions. committed_capital=0 (default)
        # reproduces the old uncapped behavior exactly.
        available_capital = max(0.0, running_capital - committed_capital)
        qty_available = int(available_capital / entry_price) if entry_price > 0 else 0

        # Take smallest of the three constraints
        qty = max(0, min(qty_risk, qty_capital, qty_available))

        # ADV-relative liquidity cap (audit V5) — position value may not
        # exceed adv_position_cap_pct of the stock's average daily traded
        # value. Applied LAST so it caps whatever the other constraints
        # allowed; only active when both the config and the data exist.
        if (self.adv_position_cap_pct is not None and adv_value is not None
                and adv_value > 0 and entry_price > 0):
            qty_adv = int(adv_value * self.adv_position_cap_pct / 100 / entry_price)
            qty = min(qty, qty_adv)

        # Check minimum position value
        if qty > 0 and self.min_position_value > 0:
            position_value = qty * entry_price
            if position_value < self.min_position_value:
                logger.debug(
                    f"Position value {position_value} < min {self.min_position_value}. "
                    f"Skipping."
                )
                return 0

        return qty

    def record_trade_closed(self, realized_pnl: float) -> None:
        """
        Record a closed trade's P&L for compounding.

        Call this when a position closes to update cumulative P&L.
        The running capital will automatically incorporate this for
        the next position sizing.

        Args:
            realized_pnl: Realized P&L in ₹ (positive = profit, negative = loss)
        """
        self.cumulative_realized_pnl += realized_pnl
        self.total_realized_pnl += realized_pnl
        self.trade_count += 1

        if realized_pnl != 0:
            capital_status = self.get_capital_status()
            logger.debug(
                f"Trade #{self.trade_count} closed: "
                f"pnl={realized_pnl:+.0f}, "
                f"cumulative={capital_status['cumulative_realized_pnl']:+.0f}, "
                f"running_capital={capital_status['running_capital']:,.0f}"
            )

    def get_capital_status(self) -> Dict[str, Any]:
        """
        Get current capital status for debugging/logging.

        Returns:
            Dict with initial capital, cumulative P&L, running capital, etc.
        """
        return {
            "initial_capital": self.initial_capital,
            "cumulative_realized_pnl": self.cumulative_realized_pnl,
            "running_capital": self.get_running_capital(),
            "compounding_enabled": self.compounding_enabled,
            "compounding_mode": self.compounding_mode,
            "compounding_min_capital": self.compounding_min_capital,
            "trade_count": self.trade_count,
            "total_realized_pnl": self.total_realized_pnl,
        }

    def get_compounding_boost(self) -> float:
        """
        Calculate the boost from compounding (for metrics/logging).

        Returns:
            Percentage boost: (running_capital - initial_capital) / initial_capital * 100
        """
        if not self.compounding_enabled or self.cumulative_realized_pnl == 0:
            return 0.0

        return (
            (self.get_running_capital() - self.initial_capital) / self.initial_capital
        ) * 100


# Unit tests
if __name__ == "__main__":
    import sys

    def test_basic_sizing():
        """Test basic position sizing without compounding."""
        sizer = PositionSizer(
            initial_capital=400000,
            risk_per_trade_pct=0.25,
            max_capital_per_trade_pct=10,
        )

        # Entry=100, Stop=85 (risk_per_share = 15)
        # Qty by risk: (400k * 0.25% / 100) / 15 = 1000 / 15 = 66.67 → 66
        # Qty by cap: (400k * 10% / 100) / 100 = 40000 / 100 = 400
        # Smaller: 66
        qty = sizer.size_position(entry_price=100, stop_price=85)
        assert qty == 66, f"Expected 66, got {qty}"
        print("✓ Basic sizing")

    def test_compounding_profit_only():
        """Test compounding in profit_only mode (grows and shrinks)."""
        sizer = PositionSizer(
            initial_capital=400000,
            risk_per_trade_pct=0.25,
            compounding_enabled=True,
            compounding_mode="profit_only",
        )

        assert sizer.get_running_capital() == 400000
        sizer.record_trade_closed(realized_pnl=10000)
        assert sizer.get_running_capital() == 410000

        # Qty should increase with capital
        qty1 = sizer.size_position(entry_price=100, stop_price=85)
        sizer.cumulative_realized_pnl = 0
        qty2 = sizer.size_position(entry_price=100, stop_price=85)
        assert qty1 > qty2, "Qty should be higher with more capital"
        print("✓ Compounding (profit_only)")

    def test_compounding_drawdown_aware():
        """Test compounding in drawdown_aware mode (only grows, floor prevents shrink)."""
        sizer = PositionSizer(
            initial_capital=400000,
            risk_per_trade_pct=0.25,
            compounding_enabled=True,
            compounding_mode="drawdown_aware",
            compounding_min_capital=400000,
        )

        sizer.record_trade_closed(realized_pnl=-50000)  # Loss
        assert sizer.get_running_capital() == 400000, "Capital should not shrink below floor"

        sizer.record_trade_closed(realized_pnl=100000)  # Profit
        assert sizer.get_running_capital() == 450000, "Capital should grow with profit"
        print("✓ Compounding (drawdown_aware)")

    def test_capital_status():
        """Test capital status reporting."""
        sizer = PositionSizer(
            initial_capital=400000,
            compounding_enabled=True,
            compounding_mode="profit_only",
        )

        sizer.record_trade_closed(50000)
        status = sizer.get_capital_status()

        assert status["initial_capital"] == 400000
        assert status["cumulative_realized_pnl"] == 50000
        assert status["running_capital"] == 450000
        assert status["trade_count"] == 1
        print("✓ Capital status")

    def test_min_position_value():
        """Test minimum position value filter."""
        sizer = PositionSizer(
            initial_capital=400000,
            risk_per_trade_pct=0.25,
            min_position_value=10000,  # ₹10k minimum
        )

        # This would size to ~6 shares @ ₹100 = ₹600 < ₹10k minimum
        qty = sizer.size_position(entry_price=100, stop_price=85)
        assert qty == 0, "Should skip position < min_position_value"

        # But @ ₹200/share, 6 shares = ₹1200 < ₹10k, skip
        qty = sizer.size_position(entry_price=200, stop_price=170)
        assert qty == 0, "Should skip position < min_position_value"

        print("✓ Minimum position value")

    # Run tests
    try:
        test_basic_sizing()
        test_compounding_profit_only()
        test_compounding_drawdown_aware()
        test_capital_status()
        test_min_position_value()
        print("\n✅ All tests passed!")
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
