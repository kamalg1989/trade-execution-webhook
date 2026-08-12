"""The six entry-v2 cases, submitted through the API for UI tracking.

One variable per run against a common baseline, so any difference is
attributable. Order matters: the cheap, already-validated changes come first, so
if the run is interrupted what exists is still a usable comparison.

THE BASELINE USES next_open_exit=True. Production's sl_engine.py runs at 18:00
IST, when Dhan rejects market orders, so it places forever orders with a trigger
just below the close WHICH FILL AT THE NEXT OPEN. The close-fill model every
earlier breakout run used is therefore not what production does, and it
understates it badly — Rs.35,406 vs Rs.125,437 over the same decade. Comparing
entry v2 against the close-fill figure would flatter it by ~Rs.90k of pure
modelling artifact.

stage2_base_stage_max_allowed=2 is set on every case including the baseline, so
it is held CONSTANT rather than being a variable. Its hash is already warm from
earlier campaigns; passing 4 explicitly would route onto a cold config-hashed
cache and run ~60x slower for an identical result.

Run:  nohup python3 -m backtest.entry_v2_cases > /root/entryv2.log 2>&1 &
"""
from __future__ import annotations

import json
import time
import urllib.request

API = "http://localhost:8005/api"
TAG = "EV2"
WINDOW = ("2016-01-01", "2026-08-08")

EXITS = {
    "breakeven": True, "half_booking": True, "trailing": True,
    "fixed_target": False,
    "ema10_trail": False, "ema21_trail": True, "ema50_trail": False,
    "chandelier_trail": False, "swing_trail": False,
    "failed_breakout_exit": False, "swing_break_exit": False,
    # What production actually does — see module docstring.
    "next_open_exit": True,
}

BASE = {
    "strategy": "BREAKOUT", "track_mode": "QUANT", "capital": 400000,
    "max_picks_per_track": 3,
    "stage2_base_stage_max_allowed": 2,
    "risk_per_trade_pct": 0.25,
    "max_capital_per_trade_pct": 10.0,
    "safety_sl_pct": 10.0,
    "stacking_guard": True, "stacking_guard_mode": "OVERRIDE",
    "exit_config": EXITS,
}

CASES = [
    ("0 baseline (next-open exits)", {}),
    ("1 + base ladder v2 (1/.75/.5/.25)", {"base_stage_ladder": "v2"}),
    ("2 + entry v2 buy-point gate", {"entry_v2_buy_points": True}),
    ("3 + ladder AND buy-point gate", {"base_stage_ladder": "v2",
                                       "entry_v2_buy_points": True}),
    ("4 + weekly cadence", {"base_stage_ladder": "v2",
                            "entry_v2_buy_points": True,
                            "signal_cadence": "weekly",
                            "signal_scan_day": "last"}),
    ("5 weekly cadence only", {"signal_cadence": "weekly",
                               "signal_scan_day": "last"}),
]


def post(path, body):
    req = urllib.request.Request(f"{API}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def get(path):
    with urllib.request.urlopen(f"{API}{path}", timeout=120) as r:
        return json.load(r)


def main():
    done = set()
    try:
        for r in get("/backtest/runs"):
            n = (r.get("params") or {}).get("notes") or ""
            if n.startswith(TAG) and r.get("status") == "COMPLETED":
                done.add(n)
    except Exception as e:
        print(f"[warn] could not read runs ({e})", flush=True)

    print(f"{len(CASES)} entry-v2 cases, {WINDOW[0]} .. {WINDOW[1]}\n", flush=True)
    print(f"{'case':<38}{'trades':>8}{'win%':>7}{'realized':>13}"
          f"{'unreal':>13}{'total':>13}{'maxDD':>11}", flush=True)

    for label, over in CASES:
        notes = f"{TAG}: {label}"
        if notes in done:
            print(f"SKIP {label}", flush=True)
            continue
        body = {**BASE, **over, "start_date": WINDOW[0], "end_date": WINDOW[1],
                "notes": notes}
        try:
            rid = post("/backtest/runs", body)["id"]
        except Exception as e:
            print(f"{label}: SUBMIT FAILED {e}", flush=True)
            continue
        print(f"  -> run {rid} started…", flush=True)
        while True:
            time.sleep(15)
            r = get(f"/backtest/runs/{rid}")
            if r["status"] != "RUNNING":
                break
        if r["status"] != "COMPLETED":
            print(f"{label}: run {rid} {r['status']} {r.get('error')}", flush=True)
            continue
        d = {x["id"]: x for x in get("/backtest/runs")}.get(rid, {})
        q = get(f"/backtest/runs/{rid}/summary")["quant"]
        print(f"{label:<38}{(d.get('tradeCount') or 0):>8}{q.get('winRate', 0):>7.1f}"
              f"{(d.get('realizedPnl') or 0):>13,.0f}{(d.get('unrealizedPnl') or 0):>13,.0f}"
              f"{(d.get('totalPnl') or 0):>13,.0f}{q.get('maxDrawdown', 0):>11,.0f}",
              flush=True)

    print("\nALLDONE", flush=True)


if __name__ == "__main__":
    main()
