# Backtest Performance Optimization Plan

## Current State Analysis

### Existing Caching (Already Working)
✅ **backtest_quant_signals** - Caches (symbol, signal_date) → (entry, SL, base_stage, target, etc.)
✅ **backtest_ai_signals** - Caches AI analysis results  
✅ **backtest_stage2_signals_cache** - Caches stage 2 signal computations
✅ **macd_series_cache** - Per-symbol MACD data cached in-memory during run
✅ **Database indexes** - Good indexes on (symbol, time) for fast OHLCV lookups
✅ **Connection pooling** - Active connection pool to database

### Current Bottlenecks (Per-Run Performance)

#### 1. **Daily OHLCV Queries** (Biggest Impact)
- **What**: For each active position on each trading day, queries: `SELECT ... FROM ohlcv_data WHERE symbol = ANY(?) AND time::date = ?`
- **Frequency**: Once per day per active symbol (hundreds of queries for a multi-year backtest)
- **Issue**: Database round-trip cost dominates when there are many active positions
- **Current approach**: Batched by day (good) but not cached across days within a run

#### 2. **Swing Data Queries** (Medium Impact)
- **What**: For each symbol on each day, loads trailing swing data (80-bar window)
- **Frequency**: Once per day per symbol that has swing_trail configured
- **Current approach**: Fetched every day, not cached

#### 3. **Signal Computation** (Already Optimized)
- **What**: Base-stage classification + entry-technique resolution
- **Status**: ✅ Cached in backtest_quant_signals
- **Performance**: Minimal impact after first computation

---

## Optimization Strategy (Safe & Non-Intrusive)

### Option 1: In-Process OHLCV Caching (Recommended - Fastest)
**Approach**: Cache OHLCV data in memory during a run, keyed by (symbol, date)

**Implementation**:
```python
# At start of run, pre-warm cache with all symbols for entire date range
ohlcv_cache = {}  # {(symbol, date): {open, high, low, close, ...}}

# Load all OHLCV for active symbols upfront instead of fetching daily
async def preload_ohlcv(pool, symbols: list[str], start_date: date, end_date: date):
    rows = await pool.fetch(
        """SELECT symbol, time::date as d, open, high, low, close 
           FROM ohlcv_data 
           WHERE symbol = ANY($1) AND time::date BETWEEN $2 AND $3""",
        symbols, start_date, end_date
    )
    for row in rows:
        ohlcv_cache[(row['symbol'], row['d'])] = {
            'open': float(row['open']),
            ...
        }
```

**Benefits**:
- ✅ Eliminates repeated database queries for same (symbol, date)
- ✅ Can reduce per-day query time by 50-70% for large backtests
- ✅ Safe: Data is read-only, no logic changes
- ✅ Minimal memory overhead: ~100KB per 1000 (symbol, date) pairs

**Risk**: None - this is purely an optimization, no logic changes

---

### Option 2: Swing Data Caching
**Approach**: Cache swing_low calculations in a table like backtest_quant_signals

**Implementation**:
- Add `backtest_swing_cache` table: (symbol, date, swing_low)
- Check cache before computing
- Insert computed swings into cache for reuse

**Benefits**: 
- ✅ Avoids recomputing swing_low on subsequent runs over same dates
- ✅ Particularly useful for multiple runs over overlapping date ranges

**Implementation effort**: ~30 lines of code in engine.py

---

### Option 3: Database Query Optimization
**Approach**: Verify query plans are using indexes correctly

**Current indexes**: Already have (symbol, time) indexes ✅
**Check**: Run EXPLAIN ANALYZE on the daily OHLCV queries to verify index use

---

## Recommended Implementation Order

### Phase 1: OHLCV In-Process Cache (Immediate Impact)
- Add optional cache in engine.py's run() function
- Warm cache once at start of run for all symbols used in screening
- Fallback to database queries if not in cache
- **Expected speedup**: 30-50% faster for 5+ year backtests

### Phase 2: Swing Cache (Secondary)
- Add table if Phase 1 proves beneficial
- **Expected speedup**: 10-20% for runs with swing_trail exit

### Phase 3: Parallel Signal Preprocessing
- Pre-compute all required signals for the entire date range at startup
- **Expected speedup**: 5-10% additional

---

## Risk Assessment

| Change | Risk | Mitigation |
|--------|------|-----------|
| In-process OHLCV cache | None - read-only data | Cache stays local to run process |
| Swing cache table | Low - new data only | Use ON CONFLICT DO NOTHING |
| Query optimization | None - index verification only | No code changes |

---

## Measurement Plan

**Before & After Metrics**:
```
Run #625 (10 years, ~2500 trading days):
- Current time: ~2:40 (est.)
- Target with Phase 1: ~1:30-2:00
- Target with Phase 1+2: ~1:15-1:45
```

**Tracking**:
- Log time spent in:
  - Signal computation
  - OHLCV fetching
  - Swing computation
  - Trade persistence
  - Other

---

## Implementation Complexity

- **Phase 1 (OHLCV cache)**: 20 lines, ~1 hour
- **Phase 2 (Swing cache)**: 30 lines, ~2 hours  
- **Phase 3 (Parallel prep)**: 50 lines, ~3 hours

**Total safe time**: ~6 hours for all phases
**Logic risk**: Minimal - all caching is read-only

---

## Caching Logic Integrity Verification

✅ **OHLCV cache**: Market data is immutable, safe to cache
✅ **Swing cache**: Calculation is deterministic (same input → same output)
✅ **Signal cache**: Already proven safe (backtest_quant_signals in production)
✅ **No entry/exit logic affected**: All caches are pre-computed data, not trading logic
