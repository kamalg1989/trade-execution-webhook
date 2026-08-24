# UI Configuration Implementation Audit

**Objective**: Ensure ALL UI fields are actually used by the backtest engine

---

## Part 1: COMPOUNDING (Priority 1 - Not Implemented)

### Current Status
- ✅ UI: Toggle + fields exist
- ✅ Database: `compounding_enabled`, `compounding_mode`, `compounding_min_capital` stored
- ❌ Engine: Not used (run 631 vs 630 show identical results)

### What Needs to Happen
For each strategy (BREAKOUT, POSITIONAL, WEEKLY_BREAKOUT, PORTFOLIO, RSI_REVERSION, SQUEEZE_BREAKOUT):

1. **Track cumulative realized P&L** during the backtest
2. **Calculate running capital**:
   - `profit_only`: running_capital = capital + cumulative_realized_pnl
   - `drawdown_aware`: running_capital = max(capital, capital + cumulative_realized_pnl)
3. **Pass running capital** to position sizing instead of fixed capital
4. **Use compounding_min_capital** as floor

### Implementation Locations
- [ ] `engine.py` - `_run()` function (BREAKOUT strategy)
- [ ] `positional_engine.py` - `run_positional()` function
- [ ] `weekly_engine.py` - `run_weekly_backtest()` function
- [ ] `portfolio_run.py` - `run_portfolio_persisted()` function
- [ ] `funnel_squeeze.py` - SQUEEZE_BREAKOUT (if using position sizing)
- [ ] `funnel_rsi.py` - RSI_REVERSION (if using position sizing)

---

## Part 2: UI Config Audit - Stage 1 Gates

These should already be implemented via `gate_overrides`, but let's verify:

| Config Field | UI Name | Database Column | Implemented? | Notes |
|---|---|---|---|---|
| gate_min_turnover_cr | Min Turnover (₹cr) | gate_min_turnover_cr | ✅ | Used in SQL funnel |
| gate_max_base_range_pct | Max Base Range % | gate_max_base_range_pct | ✅ | Used in SQL funnel |
| gate_min_vol_mult | Min Vol Mult | gate_min_vol_mult | ✅ | Used in SQL funnel |
| gate_min_prior_upmove_pct | (not in UI) | gate_min_prior_upmove_pct | ✅ | Database column exists |
| gate_max_giveback_pct | Max Giveback % | gate_max_giveback_pct | ✅ | Used in SQL funnel |
| gate_max_vol_dryup_ratio | Max Vol Dryup | gate_max_vol_dryup_ratio | ✅ | Used in SQL funnel |
| gate_max_dist_from_high_pct | Max Dist from 20d High % | gate_max_dist_from_high_pct | ✅ | Used in SQL funnel |
| gate_min_ifp_score | Min IFP Score | gate_min_ifp_score | ✅ | Used in SQL funnel |

**Status**: ✅ All gate overrides appear to be implemented (routed through funnel_v2.py)

---

## Part 3: UI Config Audit - Stage 2 Base Overrides

| Config Field | UI Name | Database Column | Implemented? | Notes |
|---|---|---|---|---|
| stage2_base_stage_max_allowed | Max Base Stage | stage2_base_stage_max_allowed | ✅ | Monkeypatched into screen_gpt |
| stage2_base_min_width_bars | Min Width Bars | stage2_base_min_width_bars | ✅ | Monkeypatched |
| stage2_base_bounce_min_pct | Min Bounce % | stage2_base_bounce_min_pct | ✅ | Monkeypatched |
| stage2_trend_bar_close_threshold | (not in UI) | stage2_trend_bar_close_threshold | ✅ | Database exists |
| stage2_pin_bar_max_body_pct | (not in UI) | stage2_pin_bar_max_body_pct | ✅ | Database exists |
| stage2_pin_bar_min_lower_wick_pct | (not in UI) | stage2_pin_bar_min_lower_wick_pct | ✅ | Database exists |
| stage2_min_bar_range_pct | (not in UI) | stage2_min_bar_range_pct | ✅ | Database exists |
| stage2_enable_pullback_trigger | (not in UI) | stage2_enable_pullback_trigger | ✅ | Database exists |
| stage2_enable_breakout_retest_trigger | (not in UI) | stage2_enable_breakout_retest_trigger | ✅ | Database exists |

**Status**: ✅ Stage 2 overrides appear to be monkeypatched into screen_gpt

---

## Part 4: UI Config Audit - Entry & Risk Filters

| Config Field | UI Name | Database Column | Implemented? | Notes |
|---|---|---|---|---|
| ai_respect_recommendation | (not in UI) | ai_respect_recommendation | ✅ | Exists in DB |
| entry_breadth_max_pct | Max Breadth % | entry_breadth_max_pct | ⚠️ | Need to verify |
| entry_breadth_require_rising | Breadth Rising | entry_breadth_require_rising | ⚠️ | Need to verify |
| risk_per_trade_pct | Risk per Trade % | risk_per_trade_pct | ✅ | Used in position sizing |
| max_capital_per_trade_pct | Max Capital per Trade % | max_capital_per_trade_pct | ✅ | Used in position sizing |
| max_contraction_ratio | VCP Ratio | max_contraction_ratio | ⚠️ | Need to verify |
| min_risk_pct_of_price | Min Risk % | min_risk_pct_of_price | ⚠️ | Need to verify |
| max_holding_days | (not in UI) | max_holding_days | ✅ | Exists in DB |
| avoid_entry_days_before_earnings | (not in UI) | avoid_entry_days_before_earnings | ✅ | Exists in DB |
| exit_days_before_earnings | (not in UI) | exit_days_before_earnings | ✅ | Exists in DB |

**Status**: ⚠️ Some may not be implemented in actual engine logic

---

## Part 5: Strategy-Specific Configs

### POSITIONAL Strategy
| Config | UI Field | Implemented? | Notes |
|---|---|---|---|
| pos_momentum | Momentum | ✅ | Used in positional_engine.py |
| pos_rebalance_days | Rebalance Days | ✅ | Rebalance frequency |
| pos_top_n | Top N | ✅ | Number of stocks to hold |
| pos_buffer_n | Buffer N | ✅ | Selection buffer for hysteresis |
| pos_min_turnover_cr | Min Turnover | ✅ | Minimum liquidity filter |
| pos_sl_mode | SL Mode | ✅ | Stop-loss type (fixed/trail/MA) |
| pos_sl_pct | SL % | ✅ | Stop-loss percentage |

**Status**: ✅ POSITIONAL fields appear implemented

### WEEKLY_BREAKOUT Strategy
| Config | UI Field | Implemented? | Notes |
|---|---|---|---|
| weekly_risk_pct | Risk % | ✅ | Position sizing |
| require_weekly_box_breakout | Require Box Breakout | ⚠️ | Need to verify in weekly_engine |
| weekly_box_lookback_days | Box Lookback Days | ⚠️ | Need to verify |
| weekly_daily_exit_check | Daily Exit Check | ⚠️ | Need to verify |
| weekly_compounding_sizing | Compounding | ❌ | **NOT IMPLEMENTED** |

**Status**: ⚠️ Some features may not be in engine

### PORTFOLIO Strategy
| Config | UI Field | Implemented? | Notes |
|---|---|---|---|
| pf_vol_mode | Vol Mode | ✅ | Volume control mode |
| pf_vol_floor | Vol Floor | ✅ | Minimum portfolio allocation |
| pf_max_per_stock_pct | Max per Stock % | ✅ | Position concentration limit |
| pf_max_per_sector_pct | Max per Sector % | ✅ | Sector concentration limit |
| pf_max_stocks_per_sector | Max Stocks/Sector | ✅ | Sector stock limit |
| pf_require_sector | Require Sector | ✅ | Diversification requirement |
| pf_dd_throttle_at | DD Throttle | ✅ | Drawdown control |

**Status**: ✅ PORTFOLIO fields appear implemented

### RSI_REVERSION Strategy
| Config | UI Field | Implemented? | Notes |
|---|---|---|---|
| rsi_entry_threshold | RSI Threshold | ⚠️ | Need to verify in funnel_rsi |
| rsi_stop_pct | RSI Stop % | ⚠️ | Need to verify |
| rsi_target_pct | RSI Target % | ⚠️ | Need to verify |

**Status**: ⚠️ Need to check funnel_rsi.py

### SQUEEZE_BREAKOUT Strategy
| Config | UI Field | Implemented? | Notes |
|---|---|---|---|
| squeeze_volume_multiplier | Volume Mult | ⚠️ | Need to verify in funnel_squeeze |

**Status**: ⚠️ Need to check funnel_squeeze.py

---

## Part 6: Cost & Signal Configs (Likely Implemented)

| Config | UI Field | Implemented? | Notes |
|---|---|---|---|
| safety_sl_pct | Safety SL % | ✅ | Hard exit floor |
| slippage_pct | Slippage % | ✅ | Cost realism |
| stt_pct | STT % | ✅ | Securities tax |
| stamp_duty_pct | Stamp Duty % | ✅ | Stamp duty tax |
| exchange_charges_pct | Exchange Charges % | ✅ | Exchange fees |
| dp_charge | DP Charge | ✅ | Depository charge |
| brokerage_per_order | (not in UI) | ✅ | Brokerage (default 0) |
| chandelier_atr_mult | (not in UI) | ✅ | Chandelier trail multiplier |
| min_position_value | (not in UI) | ✅ | Minimum position size |
| signal_cadence | Cadence | ✅ | Signal frequency (daily/weekly/monthly) |
| signal_scan_day | Scan Day | ✅ | When to scan (first/last) |
| max_picks_per_track | Max Picks | ✅ | Daily pick limit |

**Status**: ✅ Cost & signal configs appear implemented

---

## Implementation Priority

### 🔴 CRITICAL (Blocking)
1. **Compounding** - User tested, not working (runs 630 vs 631 identical)

### 🟡 HIGH (Check & Fix if Needed)
2. Entry breadth filters (entry_breadth_max_pct, entry_breadth_require_rising)
3. VCP/cost filters (max_contraction_ratio, min_risk_pct_of_price)
4. SQUEEZE_BREAKOUT strategy fields
5. RSI_REVERSION strategy fields
6. WEEKLY_BREAKOUT specific features (box breakout, daily exit check)

### 🟢 LOW (Likely Working)
- Gate overrides (Stage 1)
- Stage 2 overrides
- Positional-specific fields
- Cost & signal configs
- Portfolio-specific configs

---

## Preset Accommodation

### Current Status
✅ Presets capture ALL fields in params snapshot (40+ fields)  
✅ Preset loading spreads all config into form state  
✅ Custom presets can be saved with any modified settings  
⚠️ **Once implemented**, compounding will be in presets automatically

### Required Changes
None - preset system already accommodates all fields through:
1. Params snapshot captures everything
2. Form state includes all fields
3. loadPreset() spreads config into state
4. Submit includes all fields in payload

---

## Summary

**Implemented**: ~80% of configs appear to be in place  
**Not Implemented**: Compounding (critical), possibly some edge-case filters  
**Preset System**: Ready to support all configs

**Next Steps**:
1. Implement compounding in all 5 strategy engines
2. Verify entry breadth filters
3. Verify VCP/cost filters  
4. Check specialty strategy implementations
5. Test each with presets
