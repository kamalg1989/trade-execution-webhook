# Stop Loss Tracker Alignment Fixes — Desktop & Mobile

## Summary
Applied consistent dropdown + button alignment fixes to both desktop and mobile Stop Loss Tracker views to prevent overflow and ensure proper flex spacing. All changes focus on preventing text overflow and maintaining single-line layouts.

---

## Files Updated

### 1. **StopLossTracker.jsx** (Desktop)

#### Unprotected Positions Section (Set SL button)
**Location:** Lines 160–178

**Changes:**
- Added `flex-nowrap min-w-0` to container div (line 161)
- Added `min-w-0` to select element (line 165) — allows select to shrink when needed
- Added `whitespace-nowrap` + `flex-shrink-0` to button (line 174)
- Changed button text from "Set SL" → "Set" (line 176)
- Shortened option labels from `"₹{price} · {label} ({pct}% vs buy)"` → `"₹{price} · {label} ({pct}%)"` (line 170)

**Before:**
```jsx
<div className="flex items-stretch gap-2">
  <select ...>
    <option>₹{price} · {label} ({pct}% vs buy)</option>
  </select>
  <button ...>Set SL</button>
</div>
```

**After:**
```jsx
<div className="flex items-stretch gap-2 flex-nowrap min-w-0">
  <select className="min-w-0 ...">
    <option>₹{price} · {label} ({pct}%)</option>
  </select>
  <button className="... whitespace-nowrap">Set</button>
</div>
```

---

#### Protected Positions Section (Move SL button)
**Location:** Lines 220–239

**Changes:**
- Added `flex-nowrap min-w-0` to container div (line 220)
- Added `min-w-0` to select element (line 226)
- Added `flex-shrink-0 whitespace-nowrap` to button (line 236)
- Changed button text from "Move SL" → "Move" (line 238)
- Changed placeholder from `'Trail SL to…'` → `'Trail…'` and `'SL already highest'` → `'SL highest'` (line 227)
- Shortened option labels (line 230)

**Before:**
```jsx
<div className="flex flex-wrap items-stretch gap-2">
  <select ...>
    <option>{trailOpts.length ? 'Trail SL to…' : 'SL already highest'}</option>
    <option>₹{price} · {label} ({pct}% vs buy)</option>
  </select>
  <button ...>Move SL</button>
</div>
```

**After:**
```jsx
<div className="flex items-stretch gap-2 flex-nowrap min-w-0">
  <select className="min-w-0 ...">
    <option>{trailOpts.length ? 'Trail…' : 'SL highest'}</option>
    <option>₹{price} · {label} ({pct}%)</option>
  </select>
  <button className="... flex-shrink-0 whitespace-nowrap">Move</button>
</div>
```

---

### 2. **StopLossTrackerMobile.jsx** (Mobile)
Already updated in previous work session with identical patterns:
- Icon-only buttons (removed text labels)
- `flex-nowrap min-w-0` on containers
- Shortened placeholders and option text
- `whitespace-nowrap` on buttons

---

## Why These Changes Work

| Issue | Root Cause | Fix |
|-------|-----------|-----|
| Buttons overflow/appear outside | Flex container allows wrapping | `flex-nowrap` prevents wrapping |
| Dropdown pushes button off | Select doesn't shrink | `min-w-0` allows select to shrink |
| Text gets cut off on mobile | Labels too long | Shortened text + icon-only buttons |
| Misaligned heights | Items can stretch unevenly | `items-stretch` + explicit `h-9` on button |

---

## Deployment Instructions

Since the local build environment has file descriptor issues preventing `npm run build`, use one of these approaches:

### Option 1: Deploy from Local Machine (Recommended)
```bash
cd /Users/kamal/IdeaProjects/trade-execution-webhook/web-platform
npm install
npm run build
./deploy.sh
```

### Option 2: Git Pull on VPS
```bash
ssh root@165.232.187.97
cd /root/trade-execution-webhook/web-platform
git pull origin main
npm run build
cp -r dist /root/web-app/
```

### Option 3: Manual SCP
```bash
# From local machine after successful npm run build:
scp -r web-platform/dist root@165.232.187.97:/root/web-app/
ssh root@165.232.187.97 systemctl restart nginx
```

---

## Verification

After deployment, test alignment on:
1. **Desktop:** Resize browser to narrow width (< 768px) to simulate mobile-like tight space
2. **Mobile:** Check both unprotected (Set button) and protected (Move button) sections

**Expected behavior:**
- Dropdown and button stay on single line
- No text overflow
- Button icons visible (no clipping)
- Vertical alignment centered

---

## Files Included in Outputs
- `StopLossTracker.jsx` — Desktop component with alignment fixes
- `StopLossTrackerMobile.jsx` — Mobile component reference

Both source files in workspace folder ready for build + deploy.
