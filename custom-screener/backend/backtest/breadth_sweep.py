"""Driver: sweeps breadth-filter variants (level cap, rising requirement,
both) across both validation windows, plus stacked improvement levers, one
run at a time via the /api/backtest/runs API. Run on the VPS with
`nohup python3 breadth_sweep.py > /root/breadth_sweep.log 2>&1 &`.

Companion to gate_sweep.py (Stage 1) and stage2_sweep.py (Stage 2). Every
combo below sits on top of the current best-known base config
(basemax=2, top-2 picks, ema21 trail, safety_sl=10%, stacking OVERRIDE).
"""
import json
import time
import urllib.request

BASE = {
    "track_mode": "QUANT", "capital": 400000, "max_picks_per_track": 2,
    "safety_sl_pct": 10.0, "stacking_guard": True, "stacking_guard_mode": "OVERRIDE",
    "stage2_base_stage_max_allowed": 2,
    "exit_config": {
        "trailing": True, "breakeven": True, "ema10_trail": False, "ema21_trail": True,
        "ema50_trail": False, "swing_trail": False, "fixed_target": False,
        "half_booking": True, "chandelier_trail": False, "swing_break_exit": False,
        "failed_breakout_exit": False,
    },
}

WINDOWS = [("2025", "2025-01-01", "2025-08-08"), ("2026", "2026-01-01", "2026-08-08")]

# Round 2: re-validate Stage 1 / Stage 2 optima UNDER the new best config
# (lvl40+rising), since the original sweeps were run against the old
# pre-breadth-filter baseline and the optima may have shifted.
BEST = {"entry_breadth_max_pct": 40.0, "entry_breadth_require_rising": True}

# Round 4: trailing-mechanism comparison on the CURRENT best config
# (breadth lvl40+rising + VCP contraction 0.7 + risk 1%/trade). The earlier
# R-ladder-vs-EMA21 test was run on the pre-filter baseline, so the trade
# population has changed and it's worth re-asking.
BEST4 = {**BEST, "risk_per_trade_pct": 1.0, "max_contraction_ratio": 0.7}
EC = BASE["exit_config"]

COMBOS = [
    # pure R-ladder: half-book at +2R then ratchet SL to +(N-1)R, no EMA anchor
    ("Rladder-only", {**BEST4, "exit_config": {**EC, "ema21_trail": False}}),
    # "trail full": no half-booking, whole position rides the R-ladder
    ("Rladder-trailfull", {**BEST4,
        "exit_config": {**EC, "ema21_trail": False, "half_booking": False}}),
    # EMA21 + R-ladder together = the current best, re-run for a clean
    # same-batch reference point
    ("EMA21+Rladder(ref)", {**BEST4, "exit_config": {**EC}}),
]


def post(path, body):
    req = urllib.request.Request(
        f"http://localhost:8005/api{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def get(path):
    with urllib.request.urlopen(f"http://localhost:8005/api{path}", timeout=30) as r:
        return json.load(r)


def main():
    results = []
    for label, overrides in COMBOS:
        for wname, start, end in WINDOWS:
            body = {**BASE, "start_date": start, "end_date": end}
            body.update(overrides)
            body["notes"] = f"breadth-sweep: {label} [{wname}]"
            try:
                run_id = post("/backtest/runs", body)["id"]
            except Exception as e:
                print(f"{label}[{wname}] SUBMIT FAILED: {e}", flush=True)
                continue
            print(f"=== {label} [{wname}] -> run {run_id} ===", flush=True)
            while True:
                time.sleep(8)
                r = get(f"/backtest/runs/{run_id}")
                if r["status"] != "RUNNING":
                    break
            if r["status"] != "COMPLETED":
                print(f"{label}[{wname}] run {run_id} {r['status']}: {r.get('error')}", flush=True)
                continue
            q = get(f"/backtest/runs/{run_id}/summary")["quant"]
            row = {"label": label, "window": wname, "run_id": run_id, "count": q["count"],
                   "winRate": q["winRate"], "totalPnl": q["totalPnl"], "avgR": q["avgR"],
                   "maxDrawdown": q["maxDrawdown"], "unrealizedPnl": q["unrealizedPnl"]}
            results.append(row)
            print("RESULT", json.dumps(row), flush=True)
    print("ALL DONE", flush=True)
    print(json.dumps(results), flush=True)


if __name__ == "__main__":
    main()
