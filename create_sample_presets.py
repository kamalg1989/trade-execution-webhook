#!/usr/bin/env python3
import requests
import json

API = "http://127.0.0.1:8005/api/presets"

presets = [
    {
        "name": "Full Breakout Config",
        "strategy": "BREAKOUT",
        "config": {
            "strategy": "BREAKOUT",
            "capital": 500000,
            "startDate": "2015-01-01",
            "endDate": "2026-08-16",
            "signalCadence": "daily",
            "signalScanDay": "last",
            "maxPicksPerTrack": 3,
            "safetySlPct": 8.0,
            "slippagePct": 0.1,
            "sttPct": 0.1,
            "stampDutyPct": 0.015,
            "exchangeChargesPct": 0.003,
            "dpCharge": 14.75,
        }
    },
    {
        "name": "Full Positional Config",
        "strategy": "POSITIONAL",
        "config": {
            "strategy": "POSITIONAL",
            "capital": 400000,
            "startDate": "2015-01-01",
            "endDate": "2026-08-16",
            "posMomentum": "pct_chg_6m",
            "posRebalanceDays": 21,
            "posTopN": 20,
            "posBufferN": 40,
            "posMinTurnoverCr": 5.0,
            "posSlMode": "trail",
            "posSlPct": 8.0,
            "signalCadence": "daily",
            "signalScanDay": "last",
            "maxPicksPerTrack": 5,
            "safetySlPct": 10.0,
            "slippagePct": 0.15,
            "sttPct": 0.1,
            "stampDutyPct": 0.015,
            "exchangeChargesPct": 0.003,
            "dpCharge": 14.75,
        }
    },
    {
        "name": "Aggressive Config",
        "strategy": "BREAKOUT",
        "config": {
            "strategy": "BREAKOUT",
            "capital": 300000,
            "startDate": "2016-01-01",
            "endDate": "2026-08-16",
            "signalCadence": "daily",
            "signalScanDay": "last",
            "maxPicksPerTrack": 5,
            "safetySlPct": 5.0,
            "slippagePct": 0.2,
            "sttPct": 0.1,
            "stampDutyPct": 0.015,
            "exchangeChargesPct": 0.003,
            "dpCharge": 14.75,
        }
    },
    {
        "name": "Conservative Config",
        "strategy": "BREAKOUT",
        "config": {
            "strategy": "BREAKOUT",
            "capital": 600000,
            "startDate": "2015-01-01",
            "endDate": "2026-08-16",
            "signalCadence": "weekly",
            "signalScanDay": "last",
            "maxPicksPerTrack": 1,
            "safetySlPct": 15.0,
            "slippagePct": 0.05,
            "sttPct": 0.1,
            "stampDutyPct": 0.015,
            "exchangeChargesPct": 0.003,
            "dpCharge": 14.75,
        }
    },
    {
        "name": "High Capital Config",
        "strategy": "POSITIONAL",
        "config": {
            "strategy": "POSITIONAL",
            "capital": 1000000,
            "startDate": "2016-01-01",
            "endDate": "2026-08-16",
            "posMomentum": "pct_chg_1y",
            "posRebalanceDays": 63,
            "posTopN": 30,
            "posBufferN": 50,
            "posMinTurnoverCr": 10.0,
            "posSlMode": "fixed",
            "posSlPct": 12.0,
            "signalCadence": "daily",
            "signalScanDay": "last",
            "maxPicksPerTrack": 4,
            "safetySlPct": 10.0,
            "slippagePct": 0.1,
            "sttPct": 0.1,
            "stampDutyPct": 0.015,
            "exchangeChargesPct": 0.003,
            "dpCharge": 14.75,
        }
    },
]

print("Creating sample presets...")
for p in presets:
    try:
        r = requests.post(API, json=p, timeout=5)
        if r.status_code == 200:
            print(f"✓ {p['name']}")
        else:
            print(f"✗ {p['name']}: {r.status_code} - {r.text}")
    except Exception as e:
        print(f"✗ {p['name']}: {e}")

print("\n=== Available Presets ===")
try:
    r = requests.get(API)
    presets_list = r.json()
    for preset in presets_list:
        print(f"• {preset['name']} ({preset['strategy']})")
    print(f"\n✅ {len(presets_list)} presets ready")
except Exception as e:
    print(f"Error: {e}")
