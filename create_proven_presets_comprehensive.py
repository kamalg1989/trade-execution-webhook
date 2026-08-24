#!/usr/bin/env python3
"""Create comprehensive presets from proven 11-year backtests (BACKTEST_REPORT.md)"""
import requests
import json

API = "http://127.0.0.1:8005/api/presets"

presets = [
    {
        "name": "✅ POSITIONAL - Proven Optimal (₹1,034k, 7/11 years)",
        "strategy": "POSITIONAL",
        "config": {
            "strategy": "POSITIONAL",
            "capital": 400000,
            "startDate": "2016-01-01",
            "endDate": "2026-08-16",
            "trackMode": "BOTH",
            "posMomentum": "pct_chg_6m",
            "posRebalanceDays": 63,
            "posTopN": 20,
            "posBufferN": 40,
            "posMinTurnoverCr": 5.0,
            "posSlMode": "fixed",
            "posSlPct": 15.0,
            "safetySlPct": 8.0,
            "slippagePct": 0.1,
            "sttPct": 0.1,
            "stampDutyPct": 0.015,
            "exchangeChargesPct": 0.003,
            "dpCharge": 14.75,
            "maxPicksPerTrack": 3,
            "signalCadence": "daily",
            "signalScanDay": "last",
            "compoundingEnabled": False,
            "compoundingMinCapital": 400000,
            "compoundingMode": "profit_only",
        }
    },
    {
        "name": "✅ POSITIONAL - Low Drawdown (17-25% DD, EMA21 SL)",
        "strategy": "POSITIONAL",
        "config": {
            "strategy": "POSITIONAL",
            "capital": 400000,
            "startDate": "2016-01-01",
            "endDate": "2026-08-16",
            "trackMode": "BOTH",
            "posMomentum": "pct_chg_6m",
            "posRebalanceDays": 63,
            "posTopN": 20,
            "posBufferN": 40,
            "posMinTurnoverCr": 5.0,
            "posSlMode": "ema21",  # EMA21 produces lowest drawdown
            "posSlPct": 0,  # Dynamic, not fixed %
            "safetySlPct": 8.0,
            "slippagePct": 0.1,
            "sttPct": 0.1,
            "stampDutyPct": 0.015,
            "exchangeChargesPct": 0.003,
            "dpCharge": 14.75,
            "maxPicksPerTrack": 3,
            "signalCadence": "daily",
            "signalScanDay": "last",
            "compoundingEnabled": False,
            "compoundingMinCapital": 400000,
            "compoundingMode": "profit_only",
        }
    },
    {
        "name": "✅ POSITIONAL + COMPOUNDING (₹1,034k with reinvestment)",
        "strategy": "POSITIONAL",
        "config": {
            "strategy": "POSITIONAL",
            "capital": 400000,
            "startDate": "2016-01-01",
            "endDate": "2026-08-16",
            "trackMode": "BOTH",
            "posMomentum": "pct_chg_6m",
            "posRebalanceDays": 63,
            "posTopN": 20,
            "posBufferN": 40,
            "posMinTurnoverCr": 5.0,
            "posSlMode": "fixed",
            "posSlPct": 15.0,
            "safetySlPct": 8.0,
            "slippagePct": 0.1,
            "sttPct": 0.1,
            "stampDutyPct": 0.015,
            "exchangeChargesPct": 0.003,
            "dpCharge": 14.75,
            "maxPicksPerTrack": 3,
            "signalCadence": "daily",
            "signalScanDay": "last",
            "compoundingEnabled": True,  # ← Key difference: enable compounding
            "compoundingMinCapital": 400000,
            "compoundingMode": "profit_only",
        }
    },
    {
        "name": "✅ WEEKLY_BREAKOUT - Aggressive Growth (₹1,376k, 50% DD)",
        "strategy": "WEEKLY_BREAKOUT",
        "config": {
            "strategy": "WEEKLY_BREAKOUT",
            "capital": 400000,
            "startDate": "2016-01-01",
            "endDate": "2026-08-16",
            "trackMode": "BOTH",
            "posMomentum": "pct_chg_6m",
            "posRebalanceDays": 63,
            "posTopN": 5,        # Concentrated portfolio
            "posBufferN": 10,    # 2×topN for rebalance hysteresis
            "posMinTurnoverCr": 5.0,
            "posSlMode": "fixed",
            "posSlPct": 15.0,
            "safetySlPct": 10.0,
            "slippagePct": 0.1,
            "sttPct": 0.1,
            "stampDutyPct": 0.015,
            "exchangeChargesPct": 0.003,
            "dpCharge": 14.75,
            "maxPicksPerTrack": 3,
            "signalCadence": "daily",
            "signalScanDay": "last",
            "compoundingEnabled": False,
            "compoundingMinCapital": 400000,
            "compoundingMode": "profit_only",
        }
    },
    {
        "name": "✅ WEEKLY_BREAKOUT - Balanced (₹969k, 42% DD, Recommended)",
        "strategy": "WEEKLY_BREAKOUT",
        "config": {
            "strategy": "WEEKLY_BREAKOUT",
            "capital": 400000,
            "startDate": "2016-01-01",
            "endDate": "2026-08-16",
            "trackMode": "BOTH",
            "posMomentum": "pct_chg_6m",
            "posRebalanceDays": 63,
            "posTopN": 20,       # Diversified
            "posBufferN": 40,    # 2×topN for rebalance hysteresis
            "posMinTurnoverCr": 5.0,
            "posSlMode": "fixed",
            "posSlPct": 15.0,
            "safetySlPct": 10.0,
            "slippagePct": 0.1,
            "sttPct": 0.1,
            "stampDutyPct": 0.015,
            "exchangeChargesPct": 0.003,
            "dpCharge": 14.75,
            "maxPicksPerTrack": 3,
            "signalCadence": "daily",
            "signalScanDay": "last",
            "compoundingEnabled": False,
            "compoundingMinCapital": 400000,
            "compoundingMode": "profit_only",
        }
    },
]

print("🔄 Creating comprehensive proven presets from 11-year BACKTEST_REPORT...\n")

# Delete old presets with ✅ to clean up before creating new ones
print("Cleaning up old presets...")
try:
    existing = requests.get(API).json()
    for p in existing:
        if "✅" in p.get("name", ""):
            try:
                del_url = f"{API}/{p['id']}"
                requests.delete(del_url, timeout=5)
                print(f"  Removed: {p['name']}")
            except:
                pass
except:
    pass

print("\n📝 Creating new presets...\n")
created_count = 0

for p in presets:
    try:
        r = requests.post(API, json=p, timeout=5)
        if r.status_code == 200:
            print(f"✓ {p['name']}")
            created_count += 1
        else:
            print(f"✗ {p['name']}: {r.status_code}")
            if r.text:
                print(f"  Response: {r.text[:200]}")
    except Exception as e:
        print(f"✗ {p['name']}: {e}")

print(f"\n{'='*70}")
print(f"✅ Successfully created {created_count}/{len(presets)} presets\n")

print("📋 All Available Presets:\n")
try:
    r = requests.get(API)
    presets_list = sorted(r.json(), key=lambda x: x.get('name', ''))
    for i, preset in enumerate(presets_list, 1):
        strategy = preset.get('strategy', '?')
        print(f"{i:2}. {preset['name']:55} | {strategy:18}")
    print(f"\n{'='*70}")
    print(f"Total: {len(presets_list)} presets ready to load\n")

    print("💡 Next Step: Open Backtest UI, click 'Load saved preset...', and select one!\n")
except Exception as e:
    print(f"Error listing presets: {e}")
