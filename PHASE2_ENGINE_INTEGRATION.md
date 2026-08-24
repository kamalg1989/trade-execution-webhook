# Phase 2: Engine Integration - Detailed Implementation Guide

**Status**: Ready to integrate PositionSizer into backtest engines  
**PositionSizer Location**: `custom-screener/backend/backtest/position_sizing.py`  
**Estimated Duration**: 2-3 hours  

---

## Integration Strategy

### Overview
For each strategy engine, we need to:
1. Import PositionSizer
2. Create instance at run start with config from run dict
3. Replace existing position sizing calls with `sizer.size_position()`
4. Hook trade completion to call `sizer.record_trade_closed()`
5. Test with runs to verify compounding works

### Key Points
- ✅ PositionSizer is thread-safe for a single run
- ✅ Cumulative P&L updates automatically with each trade close
- ✅ Running capital is calculated on-demand (no need to cache)
- ✅ Backward compatible (when compounding_enabled=False, works like before)

---

## Integration Steps by Engine

### 1. BREAKOUT Strategy (engine.py → _run function)

**Location**: `custom-screener/backend/backtest/engine.py`, function `async def _run(run: dict, pool)`

**Current Code Pattern**:
```python
capital = float(run["capital"])

# ... later in day loop ...
# Position sizing is scattered across multiple calls
# Some use capital directly, others use funnel._size_qty
```

**Integration Steps**:

**Step 1.1**: Add import at top of engine.py
```python
from .position_sizing import PositionSizer
```

**Step 1.2**: Create sizer instance after line 132 (where capital is read)
```python
capital = float(run["capital"])

# Create unified position sizer with compounding support
sizer = PositionSizer(
    initial_capital=capital,
    risk_per_trade_pct=float(run.get("risk_per_trade_pct") or 0.25),
    max_capital_per_trade_pct=float(run.get("max_capital_per_trade_pct") or 10),
    compounding_enabled=bool(run.get("compounding_enabled") or False),
    compounding_mode=str(run.get("compounding_mode") or "profit_only"),
    compounding_min_capital=float(run.get("compounding_min_capital") or capital),
    min_position_value=min_position_value,
)

logger.info(f"Position sizer initialized: {sizer.get_capital_status()}")
```

**Step 1.3**: Find position sizing calls
Search for: `funnel._size_qty`, `qty =`, `quantity =`  
These need to be replaced with `sizer.size_position(entry, stop)`

**Step 1.4**: Hook trade completion
When a trade closes (in `close_trade()` or `step_exit()`), add:
```python
sizer.record_trade_closed(realized_pnl=trade.realized_pnl)
```

**Step 1.5**: Test
Create/modify a test run with compounding=true and verify:
- Running capital increases over time
- Position sizes grow with capital
- Results differ from same run without compounding by 20-25%

---

### 2. POSITIONAL Strategy (positional_engine.py → run_positional function)

**Location**: `custom-screener/backend/backtest/positional_engine.py`

**Same integration pattern as BREAKOUT**:
1. Import PositionSizer
2. Create instance at start with positional-specific settings
3. Replace position sizing in rebalance loop
4. Track cumulative P&L across rebalances

**Key Difference**: POSITIONAL rebalances periodically, not daily  
- Track P&L across the entire rebalance period
- Call `record_trade_closed()` when positions are closed at rebalance

---

### 3. WEEKLY_BREAKOUT Strategy (weekly_engine.py → run_weekly_backtest)

**Location**: `custom-screener/backend/backtest/weekly_engine.py`

**Same pattern** with one key consideration:
- WEEKLY operates on weekly timeframe
- Still need to track cumulative P&L
- Position sizes grow as capital accumulates over weeks

---

### 4. PORTFOLIO Strategy (portfolio_run.py → run_portfolio_persisted)

**Location**: `custom-screener/backend/backtest/portfolio_run.py`

**Note**: PORTFOLIO already has its own continuous compounding model  
- May need to coordinate with existing portfolio equity tracking
- Check if it already implements profit-based position sizing
- Integrate cautiously (PORTFOLIO is complex)

---

### 5. RSI_REVERSION & SQUEEZE_BREAKOUT (funnel_rsi.py, funnel_squeeze.py)

**Location**: `custom-screener/backend/backtest/funnel_rsi.py`, `funnel_squeeze.py`

These are called from engine.py, so:
1. Accept `sizer` as parameter
2. Use `sizer.size_position()` instead of internal sizing
3. Let engine track P&L

---

## Test Cases for Each Engine

After integrating each engine, test with:

```python
# Test 1: Compounding OFF (baseline)
run_id = X  # BREAKOUT with compounding_enabled=false

# Test 2: Compounding ON (profit_only)
run_id = X+1  # Same settings, compounding_enabled=true, mode=profit_only
# Expected: result > baseline by 15-25%

# Test 3: Compounding ON (drawdown_aware)
run_id = X+2  # Same settings, compounding_enabled=true, mode=drawdown_aware
# Expected: result between baseline and profit_only
```

**Verification Query**:
```sql
SELECT 
  id, strategy, compounding_enabled, compounding_mode,
  (SELECT SUM(realized_pnl) FROM backtest_trades t 
   WHERE t.run_id = r.id AND t.status = 'CLOSED') as total_pnl
FROM backtest_runs r
WHERE id IN (X, X+1, X+2)
ORDER BY id;
```

Expected output:
```
id  | strategy | compounding_enabled | compounding_mode  | total_pnl
----|----------|---------------------|-------------------|----------
X   | BREAKOUT | false               | profit_only       | 2000000
X+1 | BREAKOUT | true                | profit_only       | 2400000  ← +20%
X+2 | BREAKOUT | true                | drawdown_aware    | 2200000  ← +10%
```

---

## Code Integration Checklist

### BREAKOUT Strategy
- [ ] Import PositionSizer in engine.py
- [ ] Create sizer instance after line 132
- [ ] Find all `funnel._size_qty` calls → replace with `sizer.size_position()`
- [ ] Hook `close_trade()` or equivalent to call `sizer.record_trade_closed()`
- [ ] Test with compounding=true/false
- [ ] Verify results match expected boost (20-25%)

### POSITIONAL Strategy
- [ ] Import PositionSizer in positional_engine.py
- [ ] Create sizer instance
- [ ] Replace position sizing in rebalance loop
- [ ] Track P&L on rebalance
- [ ] Test thoroughly (rebalance logic is complex)

### WEEKLY_BREAKOUT Strategy
- [ ] Import PositionSizer in weekly_engine.py
- [ ] Create sizer instance
- [ ] Replace position sizing logic
- [ ] Test with weekly data

### PORTFOLIO Strategy
- [ ] Review existing portfolio compounding model
- [ ] Integrate cautiously (may have unique requirements)

### RSI_REVERSION & SQUEEZE_BREAKOUT
- [ ] Modify to accept sizer parameter
- [ ] Use sizer.size_position()

---

## Rollback Plan

If anything breaks during integration:
1. Revert the engine changes (git checkout)
2. PositionSizer stays in place (it's backward compatible)
3. Compounding stays disabled until fixed

No database migrations needed — just code changes.

---

## Success Criteria

✅ All engines use PositionSizer  
✅ Compounding boosts returns by 20-25% for profit_only mode  
✅ Compounding boosts returns by 10-20% for drawdown_aware mode  
✅ Non-compounding runs produce identical results as before  
✅ All presets load and work correctly  
✅ No performance regression  

---

## Next: Phase 2 Implementation

When ready, I'll:
1. Modify engine.py to use PositionSizer
2. Test with BREAKOUT strategy (runs 630/631 pattern)
3. Verify 20-25% compounding boost
4. Repeat for other engines
5. Final verification across all strategies

Should I proceed? 🚀
