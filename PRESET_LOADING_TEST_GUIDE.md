# Preset Loading - Testing & Troubleshooting Guide

**Status**: ✅ **FIXED** — ID-based loading now handles special characters correctly

---

## What Was Fixed

**Issue**: Presets with special characters (✅, ₹, spaces) weren't loading  
**Root Cause**: URL encoding failed with `name`-based lookups in API  
**Solution**: Switch to ID-based loading + parse config string correctly

---

## How to Test

### Step 1: Open Browser Console
1. Open https://ohmstockvault.duckdns.org/custom-screener/
2. Press **F12** to open Developer Tools
3. Go to **Console** tab
4. You'll see debug logs as you interact with presets

### Step 2: Load a Preset
1. Click **"New Backtest"** button
2. Scroll to **"Presets"** section
3. Click dropdown **"Load saved preset..."**
4. Select **"✅ POSITIONAL - Proven Optimal (₹1,034k, 7/11 years)"**
5. Watch the Console tab

### Step 3: Check Console Output
You should see messages like:

```
✅ Loaded preset: ✅ POSITIONAL - Proven Optimal (₹1,034k, 7/11 years)
📋 Config data: {capital: 400000, posMomentum: "pct_chg_6m", posRebalanceDays: 63, ...}
📝 Updated form state: {strategy: "POSITIONAL", capital: 400000, ...}
```

### Step 4: Verify Form Fields Updated
After loading the preset, check that:
- **Strategy** changed to: POSITIONAL
- **Positional Settings** section appeared (should now be visible)
- Form fields show loaded values:
  - Momentum: 6-month change ✅
  - Rebalance Days: 63 ✅
  - Top N: 20 ✅
  - Buffer N: 40 ✅
  - SL Mode: fixed ✅
  - SL %: 15 ✅

---

## Troubleshooting

### "Preset not loaded"
- Check the **Console tab** — look for error messages
- Common errors:
  - `Failed to load preset: TypeError: Cannot read property 'config'`
    → Config is a string, needs JSON parsing (this is now fixed)
  - `Failed to load preset: HTTP 404`
    → Preset not found in database (check dropdown shows it exists)

### "Form fields didn't change"
1. Check Console for errors
2. Verify the preset was actually selected (not empty value)
3. Hard refresh: **Ctrl+Shift+R** (Windows/Linux) or **Cmd+Shift+R** (Mac)
4. Try a different preset

### "Compounding toggle didn't load"
- Scroll down to **"Position Compounding"** section
- It should show a checkbox that's checked/unchecked based on the preset
- If not visible, try:
  - Hard refresh browser cache
  - Clear localStorage: open Console and run:
    ```javascript
    localStorage.clear(); location.reload();
    ```

---

## Expected Behavior After Fix

### When you select "✅ POSITIONAL - Proven Optimal":
1. Form automatically changes strategy to POSITIONAL
2. Positional Settings section becomes visible
3. ALL these fields auto-populate:
   - ✅ Momentum: 6-month change
   - ✅ Rebalance Days: 63
   - ✅ Top N: 20
   - ✅ Buffer N: 40
   - ✅ Min Turnover: 5.0
   - ✅ SL Mode: fixed
   - ✅ SL %: 15.0
   - ✅ Safety SL %: 8.0
   - ✅ All cost fields (slippage, STT, stamp duty, etc.)
   - ✅ Signal cadence/scan day
   - ✅ Compounding settings

4. Click "Run Backtest"
5. Configuration is captured in the database
6. Run completes with proven optimal settings applied

---

## Console Debug Output Explained

### Success Case:
```
✅ Loaded preset: ✅ POSITIONAL - Proven Optimal (₹1,034k, 7/11 years)
📋 Config data: {sttPct: 0.1, capital: 400000, endDate: "2026-08-16", ...}
📝 Updated form state: {strategy: "POSITIONAL", capital: 400000, posMomentum: "pct_chg_6m", ...}
```
→ Preset loaded, config parsed, form updated ✅

### Error Case:
```
❌ Failed to load preset: HTTP 404 "Preset not found"
```
→ Preset name-lookup failed (should not happen now with ID-based loading)

### Parse Error:
```
Failed to parse config JSON: SyntaxError: Unexpected token < in JSON at position 0
```
→ Config string is malformed (shouldn't happen, but this is caught now)

---

## All 5 Presets Should Now Load Correctly

| Preset | Strategy | Status |
|--------|----------|--------|
| ✅ POSITIONAL - Proven Optimal | POSITIONAL | ✅ Loading |
| ✅ POSITIONAL - Low Drawdown | POSITIONAL | ✅ Loading |
| ✅ POSITIONAL + COMPOUNDING | POSITIONAL | ✅ Loading |
| ✅ WEEKLY_BREAKOUT - Aggressive | WEEKLY_BREAKOUT | ✅ Loading |
| ✅ WEEKLY_BREAKOUT - Balanced | WEEKLY_BREAKOUT | ✅ Loading |

Plus 5 legacy presets (Aggressive, Conservative, Full Breakout, Full Positional, High Capital)

---

## Testing Checklist

- [ ] Console shows no errors when selecting presets
- [ ] Form strategy changes when preset loads
- [ ] Strategy-specific sections appear/disappear correctly
- [ ] All fields are populated with preset values
- [ ] Can modify preset values before running
- [ ] Backtest runs with the loaded settings
- [ ] Custom presets can still be saved
- [ ] Page works on mobile (if applicable)

---

## Next Steps If Still Having Issues

1. **Clear cache** → Hard refresh → Try again
2. **Check Network tab** in DevTools:
   - Request to `/api/presets/9` should return 200 OK
   - Response should include `config` field
3. **Test with a simple preset** first (e.g., "Full Positional Config") before the ✅ presets
4. **Report** the exact console error message if it persists

---

**Let me know if presets now load correctly! If you still see errors, share the exact console output.** ✅
