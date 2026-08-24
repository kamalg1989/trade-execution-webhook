#!/usr/bin/env python3
"""Create presets from best-known historical run configurations"""
import requests
import json

API = "http://127.0.0.1:8005/api/presets"

presets = [
    {
        "name": "✅ WEEKLY_BREAKOUT - Top Performer (₹16.6M)",
        "strategy": "WEEKLY_BREAKOUT",
        "config": {
            "strategy": "WEEKLY_BREAKOUT",
            "capital": 400000,
            "startDate": "2016-01-01",
            "endDate": "2026-08-08",
            "posMomentum": "pct_chg_6m",
            "posRebalanceDays": 63,
            "posTopN": 20,
            "posBufferN": 40,
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
        }
    },
    {
        "name": "✅ POSITIONAL - Top Performer (₹1.5M)",
        "strategy": "POSITIONAL",
        "config": {
            "strategy": "POSITIONAL",
            "capital": 400000,
            "startDate": "2016-01-01",
            "endDate": "2026-08-08",
            "posMomentum": "pct_chg_6m",
            "posRebalanceDays": 21,
            "posTopN": 10,
            "posBufferN": 20,
            "posMinTurnoverCr": 5.0,
            "posSlMode": "fixed",
            "posSlPct": 7.0,
            "safetySlPct": 8.0,
            "slippagePct": 0.1,
            "sttPct": 0.1,
            "stampDutyPct": 0.015,
            "exchangeChargesPct": 0.003,
            "dpCharge": 14.75,
            "maxPicksPerTrack": 3,
            "signalCadence": "daily",
            "signalScanDay": "last",
        }
    },
    {
        "name": "✅ BREAKOUT - Top Performer (₹657k)",
        "strategy": "BREAKOUT",
        "config": {
            "strategy": "BREAKOUT",
            "capital": 400000,
            "startDate": "2016-01-01",
            "endDate": "2026-08-08",
            "safetySlPct": 10.0,
            "slippagePct": 0.1,
            "sttPct": 0.1,
            "stampDutyPct": 0.015,
            "exchangeChargesPct": 0.003,
            "dpCharge": 14.75,
            "maxPicksPerTrack": 3,
            "signalCadence": "weekly",
            "signalScanDay": "last",
        }
    },
]

print("Creating proven presets from historical top performers...")
for p in presets:
    try:
        r = requests.post(API, json=p, timeout=5)
        if r.status_code == 200:
            print(f"✓ {p['name']}")
        else:
            print(f"✗ {p['name']}: {r.status_code}")
            if r.text:
                print(f"  Response: {r.text[:200]}")
    except Exception as e:
        print(f"✗ {p['name']}: {e}")

print("\n=== Available Presets ===")
try:
    r = requests.get(API)
    presets_list = r.json()
    for preset in sorted(presets_list, key=lambda x: x.get('name', '')):
        print(f"• {preset['name']} ({preset['strategy']})")
    print(f"\n✅ {len(presets_list)} total presets ready")
except Exception as e:
    print(f"Error listing presets: {e}")
