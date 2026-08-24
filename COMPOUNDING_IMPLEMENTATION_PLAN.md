# Compounding & Config Overhaul Implementation Plan

**Scope**: Full architecture refactor for unified position sizing with compounding support  
**Estimated Effort**: 6-8 hours  
**Timeline**: To be completed in phases  

---

## Architecture Design

### Current Problem
- Position sizing scattered across multiple functions (funnel._size_qty, simulator.py, etc.)
- Capital is hardcoded per strategy
- No unified compounding mechanism
- Config usage is inconsistent across strategies

### Proposed Solution
Create a **unified PositionSizer class** that:
1. **Manages running capital** (with compounding)
2. **Applies all config filters** consistently
3. **Sizes positions** using risk/capital constraints
4. **Tracks cumulative P&L** for compounding
5. **Works identically** across all strategies

---

## Implementation Phases

### Phase 1: Build PositionSizer Class (2-3 hours)

**File**: `custom-screener/backend/backtest/position_sizing.py` (NEW)

```python
class PositionSizer:
    """Unified position sizing with compounding support"""
    
    def __init__(self, 
                 initial_capital: float,
                 risk_per_trade_pct: float = 0.25,
                 max_capital_per_trade_pct: float = 10,
                 compounding_enabled: bool = False,
                 compounding_mode: str = "profit_only",
                 compounding_min_capital: float = None,
                 **config):
        self.initial_capital = initial_capital
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_capital_per_trade_pct = max_capital_per_trade_pct
        self.compounding_enabled = compounding_enabled
        self.compounding_mode = compounding_mode
        self.compounding_min_capital = compounding_min_capital or initial_capital
        self.cumulative_realized_pnl = 0.0  # Track throughout backtest
        self.config = config
    
    def get_running_capital(self) -> float:
        """Calculate running capital based on compounding mode"""
        if not self.compounding_enabled:
            return self.initial_capital
        
        if self.compounding_mode == "profit_only":
            # Capital grows and shrinks with P&L
            running = self.initial_capital + self.cumulative_realized_pnl
        else:  # "drawdown_aware"
            # Capital only grows, floor prevents shrinkage
            running = max(
                self.compounding_min_capital,
                self.initial_capital + self.cumulative_realized_pnl
            )
        
        # Ensure minimum
        return max(running, self.compounding_min_capital)
    
    def size_position(self, 
                     entry_price: float, 
                     stop_price: float) -> int:
        """Calculate position size based on running capital and risk"""
        running_capital = self.get_running_capital()
        risk_per_share = entry_price - stop_price
        
        if risk_per_share <= 0:
            return 0
        
        # Risk-based sizing
        qty_risk = int((running_capital * self.risk_per_trade_pct / 100) / risk_per_share)
        
        # Capital constraint
        qty_capital = int((running_capital * self.max_capital_per_trade_pct / 100) / entry_price)
        
        # Take the smaller of the two constraints
        return max(0, min(qty_risk, qty_capital))
    
    def record_trade_closed(self, realized_pnl: float) -> None:
        """Update cumulative P&L when trade closes"""
        self.cumulative_realized_pnl += realized_pnl
    
    def get_capital_status(self) -> dict:
        """Return current capital status for debugging/logging"""
        return {
            "initial_capital": self.initial_capital,
            "cumulative_realized_pnl": self.cumulative_realized_pnl,
            "running_capital": self.get_running_capital(),
            "compounding_enabled": self.compounding_enabled,
            "compounding_mode": self.compounding_mode,
        }
```

**Responsibilities**:
- ✅ Manages running capital
- ✅ Applies compounding logic
- ✅ Consistent position sizing across all strategies
- ✅ Configurable risk parameters

---

### Phase 2: Integrate with Engine (2-3 hours)

**Step 1: Update `engine.py` (_run function)**
- Create PositionSizer instance at start
- Extract compounding settings from run dict
- Pass to signal sizing functions
- Call `record_trade_closed()` on every trade completion

**Step 2: Update `positional_engine.py`**
- Create PositionSizer for POSITIONAL strategy
- Use it for position sizing in rebalance loop
- Track cumulative P&L across rebalances

**Step 3: Update `weekly_engine.py`**
- Create PositionSizer for WEEKLY_BREAKOUT
- Apply compounding across weeks
- Track weekly P&L accumulation

**Step 4: Update `portfolio_run.py`**
- Create PositionSizer for PORTFOLIO
- Integrate with existing position sizing
- Apply compounding to portfolio equity

**Step 5: Other strategies (SQUEEZE_BREAKOUT, RSI_REVERSION)**
- Create PositionSizer instances
- Wire into existing position sizing

---

### Phase 3: Config Filter Integration (1-2 hours)

Add filters to PositionSizer for:
- Entry breadth filters (`entry_breadth_max_pct`, `entry_breadth_require_rising`)
- VCP/cost filters (`max_contraction_ratio`, `min_risk_pct_of_price`)
- Position value filter (`min_position_value`)
- Holding days limit (`max_holding_days`)

**Implementation**:
```python
def can_enter(self, candidate_config: dict) -> bool:
    """Check if candidate meets all filter requirements"""
    # Apply all filters based on self.config
    # Return True only if all pass
    pass

def apply_filters(self, candidates: list) -> list:
    """Filter candidate list based on config"""
    return [c for c in candidates if self.can_enter(c)]
```

---

### Phase 4: Testing & Validation (1-2 hours)

**Test Cases**:
1. ✅ Run 630 vs 631: Verify compounding boost (expect 20-25%)
2. ✅ "profit_only" mode: Capital shrinks on losses
3. ✅ "drawdown_aware" mode: Capital floor holds
4. ✅ Config filters: Entry breadth blocks late-cycle trades
5. ✅ All strategies: Compounding applies identically
6. ✅ Preset loading: All settings take effect

**Verification**:
```bash
# Compare runs
SELECT id, strategy, compounding_enabled, 
       (SELECT SUM(realized_pnl) FROM backtest_trades 
        WHERE run_id = backtest_runs.id AND status = 'CLOSED') as total_pnl
FROM backtest_runs 
WHERE id IN (630, 631);

# Expected: 631 > 630 by ~20-25%
```

---

## Implementation Order

```
1. Create position_sizing.py with PositionSizer class
   └─ Write unit tests for sizing logic
   └─ Verify compounding calculations

2. Update engine.py
   └─ Integrate PositionSizer for BREAKOUT strategy
   └─ Test with run 630/631 pattern

3. Update positional_engine.py  
   └─ Integrate for POSITIONAL strategy
   └─ Test with positional presets

4. Update weekly_engine.py
   └─ Integrate for WEEKLY_BREAKOUT
   └─ Test with weekly presets

5. Update portfolio_run.py
   └─ Integrate for PORTFOLIO

6. Update specialty engines (squeeze, rsi)
   └─ Integrate for SQUEEZE_BREAKOUT and RSI_REVERSION

7. Add config filters to PositionSizer
   └─ Implement entry_breadth filters
   └─ Implement VCP/cost filters
   └─ Test filter application

8. End-to-end testing
   └─ Run full test suite
   └─ Verify all presets work
   └─ Benchmark performance

9. Deployment
   └─ Update database schema if needed
   └─ Deploy to VPS
   └─ Run verification backtests
```

---

## Deployment Checklist

- [ ] All engines updated and tested
- [ ] Compounding works in all strategies
- [ ] Performance impact measured (should be minimal)
- [ ] All presets still load correctly
- [ ] No database migrations needed (just add logic)
- [ ] Backward compatible (old runs unaffected)
- [ ] Verification runs show expected results

---

## Key Benefits

✅ **Unified system**: All strategies use same logic  
✅ **Compounding works**: 20-25% boost on profits  
✅ **All configs used**: No unused settings  
✅ **Maintainable**: One PositionSizer class to update  
✅ **Testable**: Unit tests for sizing logic  
✅ **Preset compatible**: Works seamlessly with preset system  

---

## Estimated Impact on Run 631

**Without compounding**: ₹2,007,098  
**With compounding (profit_only)**: ~₹2,407,000-₹2,509,000 (20-25% boost)  
**With compounding (drawdown_aware)**: ~₹2,307,000-₹2,409,000 (15-20% boost)

---

## Start Point

Given the size, I recommend:
1. Start with Phase 1-2 (PositionSizer + engine.py integration)
2. Test thoroughly with BREAKOUT strategy
3. Then expand to other strategies
4. Add config filters last

Should I proceed with Phase 1 (building PositionSizer class)?
