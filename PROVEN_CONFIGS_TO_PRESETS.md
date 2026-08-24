# Best-Known Configurations → UI Presets Mapping

**Source**: BACKTEST_REPORT.md §5 Consolidated Scoreboard (11-year validation, 2016-2026)  
**Date**: August 16, 2026

---

## Mapping Table: Previous Config → New UI Preset

### 1. POSITIONAL — 6m/63d/top20 + fixed 15% SL
**Performance**: ₹1,034k total (7/11 years +ve, -₹88k worst, 33% maxDD, 516 trades)

| Parameter | Previous Value | New UI Field | Equivalent Setting |
|-----------|----------------|--------------|-------------------|
| Strategy | POSITIONAL | strategy | POSITIONAL |
| Capital | ₹4L (baseline) | capital | 400000 |
| Momentum | 6-month | posMomentum | pct_chg_6m |
| Rebalance | 63 days | posRebalanceDays | 63 |
| Top N stocks | 20 | posTopN | 20 |
| Buffer symbols | 40 (2×topN) | posBufferN | 40 |
| Min turnover | ₹5cr (standard) | posMinTurnoverCr | 5.0 |
| SL mode | Fixed % | posSlMode | fixed |
| SL % | 15% | posSlPct | 15 |
| Safety floor | 8% | safetySlPct | 8.0 |
| Slippage | 0.10% | slippagePct | 0.1 |
| Date range | 2016-2026 | startDate/endDate | 2016-01-01 / today |

**Preset Name**: `✅ POSITIONAL - Proven Optimal (₹1,034k)`

---

### 2. POSITIONAL — EMA21 variant (lowest drawdown)
**Performance**: ₹315k total (6/11 years +ve, -₹29k worst, **17-25% maxDD**, 834 trades)  
**Use case**: Maximum risk control / smoothness priority

| Parameter | Previous Value | New UI Field | Equivalent Setting |
|-----------|----------------|--------------|-------------------|
| Strategy | POSITIONAL | strategy | POSITIONAL |
| Capital | ₹4L (baseline) | capital | 400000 |
| Momentum | 6-month | posMomentum | pct_chg_6m |
| Rebalance | 63 days | posRebalanceDays | 63 |
| Top N stocks | 20 | posTopN | 20 |
| Buffer symbols | 40 (2×topN) | posBufferN | 40 |
| Min turnover | ₹5cr | posMinTurnoverCr | 5.0 |
| SL mode | EMA21 trail | posSlMode | ema21 |
| SL % | N/A (dynamic MA) | posSlPct | 0 |
| Safety floor | 8% | safetySlPct | 8.0 |
| Slippage | 0.10% | slippagePct | 0.1 |
| Date range | 2016-2026 | startDate/endDate | 2016-01-01 / today |

**Preset Name**: `✅ POSITIONAL - Low Drawdown (17-25%, EMA21 SL)`

---

### 3. WEEKLY_BREAKOUT — 6m/63d/top5 (highest return, concentrated)
**Performance**: ₹1,376k total (8/11 years +ve, -₹80k worst, 50% maxDD, high concentration)  
**Use case**: Maximum growth (accepts higher drawdown concentration)

| Parameter | Previous Value | New UI Field | Equivalent Setting |
|-----------|----------------|--------------|-------------------|
| Strategy | WEEKLY_BREAKOUT | strategy | WEEKLY_BREAKOUT |
| Capital | ₹4L (baseline) | capital | 400000 |
| Momentum | 6-month | posMomentum | pct_chg_6m |
| Rebalance | 63 days | posRebalanceDays | 63 |
| Top N stocks | 5 | posTopN | 5 |
| Buffer symbols | 10 (2×topN) | posBufferN | 10 |
| Min turnover | ₹5cr | posMinTurnoverCr | 5.0 |
| SL mode | Fixed % | posSlMode | fixed |
| SL % | 15% | posSlPct | 15 |
| Safety floor | 10% | safetySlPct | 10.0 |
| Slippage | 0.10% | slippagePct | 0.1 |
| Date range | 2016-2026 | startDate/endDate | 2016-01-01 / today |

**Preset Name**: `✅ WEEKLY_BREAKOUT - Aggressive Growth (₹1,376k, 50% DD)`

---

### 4. WEEKLY_BREAKOUT — 6m/63d/top20 (plateau, balanced)
**Performance**: ₹969k total (7/11 years +ve, -₹116k worst, 42% maxDD)  
**Use case**: Balanced growth/drawdown trade-off (recommended default)

| Parameter | Previous Value | New UI Field | Equivalent Setting |
|-----------|----------------|--------------|-------------------|
| Strategy | WEEKLY_BREAKOUT | strategy | WEEKLY_BREAKOUT |
| Capital | ₹4L (baseline) | capital | 400000 |
| Momentum | 6-month | posMomentum | pct_chg_6m |
| Rebalance | 63 days | posRebalanceDays | 63 |
| Top N stocks | 20 | posTopN | 20 |
| Buffer symbols | 40 (2×topN) | posBufferN | 40 |
| Min turnover | ₹5cr | posMinTurnoverCr | 5.0 |
| SL mode | Fixed % | posSlMode | fixed |
| SL % | 15% | posSlPct | 15 |
| Safety floor | 10% | safetySlPct | 10.0 |
| Slippage | 0.10% | slippagePct | 0.1 |
| Date range | 2016-2026 | startDate/endDate | 2016-01-01 / today |

**Preset Name**: `✅ WEEKLY_BREAKOUT - Balanced (₹969k, 42% DD, Recommended)`

---

### 5. BREAKOUT — Production (current live config)
**Performance**: ₹202k total (5/11 years +ve, -₹47k worst, ~17% maxDD, 2,914 trades)  
**Note**: High trade count, cost-intensive; exists for reference/comparison

| Parameter | Previous Value | New UI Field | Equivalent Setting |
|-----------|----------------|--------------|-------------------|
| Strategy | BREAKOUT | strategy | BREAKOUT |
| Capital | ₹4L (baseline) | capital | 400000 |
| Safety floor | 10% | safetySlPct | 10.0 |
| Slippage | 0.10% | slippagePct | 0.1 |
| Signal cadence | Daily | signalCadence | daily |
| Signal scan day | Last (weekend review) | signalScanDay | last |
| Max picks/day | 3 | maxPicksPerTrack | 3 |
| Base stage limit | Stage 2 max | (gate config) | stage2_base_stage_max_allowed: 2 |

**Preset Name**: `✅ BREAKOUT - Production Today (₹202k, Reference)`

---

## Feature: Compounding Position Allocation

### What It Does
Instead of fixed starting capital ₹4L, the system re-calculates position sizing based on current running equity:
```
position_capital = starting_capital + cumulative_realized_pnl
```

This allows profits to automatically compound into larger positions over time.

### Implementation (New UI Fields)

Add to CompactBacktestForm as a toggle + optional override:

```javascript
// New state fields
compoundingEnabled: false,        // Toggle: use compounded capital
compoundingMinCapital: 400000,    // Minimum capital floor (don't go below)
compoundingMode: 'profit_only',   // 'profit_only' or 'drawdown_aware'
```

### UI Changes

1. **After "Capital (₹)" field**, add:
   ```
   ☐ Compound position sizing based on profits
     └─ Minimum capital floor (₹): [400000]
     └─ Compounding mode: [Profit Only ▼] (or Drawdown Aware)
   ```

2. **In "Advanced Settings" section**, explain:
   - Profit Only: Capital grows with wins, shrinks with losses (aggressive)
   - Drawdown Aware: Capital only grows; losses don't shrink it (conservative)

3. **When compounding is enabled**:
   - Show realized P&L in real-time during backtest
   - Display "Running Capital" metric in results (capital at end of backtest)
   - Calculate final position count differently based on equity growth

### Database Field
Already exists in `backtest_runs` table:
```sql
weekly_compounding_sizing BOOLEAN DEFAULT FALSE
```

Add to RunCreate Pydantic model (if not already present):
```python
compounding_enabled: bool = False
compounding_min_capital: float = 400000
compounding_mode: str = Field("profit_only", pattern="^(profit_only|drawdown_aware)$")
```

### Test Scenario
Run POSITIONAL fixed-15% with:
- Without compounding: ₹1,034k (fixed ₹4L capital throughout)
- With compounding: Likely ₹1,150k-₹1,200k (capital grows with profits, larger positions in winning years)

---

## Summary: All 5 Proven Presets to Create

| Preset Name | Strategy | Total Return | Years +ve | Max DD |
|------------|----------|--------------|-----------|--------|
| ✅ POSITIONAL - Proven Optimal | POSITIONAL | ₹1,034k | 7/11 | 33% |
| ✅ POSITIONAL - Low Drawdown | POSITIONAL | ₹315k | 6/11 | 17-25% |
| ✅ WEEKLY_BREAKOUT - Aggressive | WEEKLY_BREAKOUT | ₹1,376k | 8/11 | 50% |
| ✅ WEEKLY_BREAKOUT - Balanced | WEEKLY_BREAKOUT | ₹969k | 7/11 | 42% |
| ✅ BREAKOUT - Production | BREAKOUT | ₹202k | 5/11 | 17% |

All presets include best-known stage 2 gate (base_stage_max_allowed = 2).

---

## Implementation Checklist

- [ ] Add `compounding_enabled`, `compounding_min_capital`, `compounding_mode` to RunCreate model
- [ ] Update CompactBacktestForm to include compounding toggle + fields
- [ ] Pass compounding fields to backend in form submission
- [ ] Update backtest engine to use compounded capital instead of fixed
- [ ] Create all 5 proven presets via `create_proven_presets.py`
- [ ] Test each preset end-to-end on 1-year window
- [ ] Document in UI tooltips

