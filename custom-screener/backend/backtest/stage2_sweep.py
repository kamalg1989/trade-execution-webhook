"""One-off driver script: sweeps each Stage-2 (base-stage classification +
entry-technique threshold) individually, loosen + tighten, one at a time,
against the known-best baseline (run #54: QUANT-only, max_picks=2,
ema21_trail, safety_sl=10%, stacking_guard=OVERRIDE, Jan-Aug 2026 window).
Run manually via `python3 stage2_sweep.py` on the VPS; talks only to the
already-deployed /api/backtest/runs API. Companion to gate_sweep.py
(Stage 1). run_id=80 (stage2_base_stage_max_allowed=6) already done by hand
before this script existed — not repeated here.
"""
import json
import time
import urllib.request

BASE = {
    "start_date": "2026-01-01", "end_date": "2026-08-08", "track_mode": "QUANT",
    "capital": 400000, "max_picks_per_track": 2, "safety_sl_pct": 10.0,
    "stacking_guard": True, "stacking_guard_mode": "OVERRIDE",
    "exit_config": {
        "trailing": True, "breakeven": True, "ema10_trail": False, "ema21_trail": True,
        "ema50_trail": False, "swing_trail": False, "fixed_target": False,
        "half_booking": True, "chandelier_trail": False, "swing_break_exit": False,
        "failed_breakout_exit": False,
    },
}

COMBOS = [
    ("s2-basemax-tighten-2", {"stage2_base_stage_max_allowed": 2}),
    ("s2-basewidth-tighten-15", {"stage2_base_min_width_bars": 15}),
    ("s2-basewidth-loosen-6", {"stage2_base_min_width_bars": 6}),
    ("s2-bounce-tighten-20", {"stage2_base_bounce_min_pct": 20}),
    ("s2-bounce-loosen-5", {"stage2_base_bounce_min_pct": 5}),
    ("s2-trendbar-tighten-0.80", {"stage2_trend_bar_close_threshold": 0.80}),
    ("s2-trendbar-loosen-0.60", {"stage2_trend_bar_close_threshold": 0.60}),
    ("s2-pinbody-tighten-0.25", {"stage2_pin_bar_max_body_pct": 0.25}),
    ("s2-pinbody-loosen-0.45", {"stage2_pin_bar_max_body_pct": 0.45}),
    ("s2-pinwick-tighten-0.65", {"stage2_pin_bar_min_lower_wick_pct": 0.65}),
    ("s2-pinwick-loosen-0.45", {"stage2_pin_bar_min_lower_wick_pct": 0.45}),
    ("s2-barrange-tighten-1.0", {"stage2_min_bar_range_pct": 1.0}),
    ("s2-barrange-loosen-0.25", {"stage2_min_bar_range_pct": 0.25}),
    ("s2-pullback-trigger-ON", {"stage2_enable_pullback_trigger": True}),
    ("s2-breakoutretest-trigger-ON", {"stage2_enable_breakout_retest_trigger": True}),
]


def post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"http://localhost:8005/api{path}", data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def get(path):
    with urllib.request.urlopen(f"http://localhost:8005/api{path}", timeout=30) as r:
        return json.load(r)


def main():
    results = []
    for label, overrides in COMBOS:
        body = dict(BASE)
        body.update(overrides)
        body["notes"] = f"stage2-sweep: {label}"
        try:
            resp = post("/backtest/runs", body)
        except Exception as e:
            print(f"{label}: FAILED TO SUBMIT: {e}", flush=True)
            results.append({"label": label, "error": str(e)})
            continue
        run_id = resp["id"]
        print(f"=== {label} -> run {run_id} ===", flush=True)
        r = None
        while True:
            time.sleep(8)
            r = get(f"/backtest/runs/{run_id}")
            if r["status"] != "RUNNING":
                break
        if r["status"] != "COMPLETED":
            print(f"{label} run {run_id} status={r['status']} error={r.get('error')}", flush=True)
            results.append({"label": label, "run_id": run_id, "status": r["status"], "error": r.get("error")})
            continue
        s = get(f"/backtest/runs/{run_id}/summary")
        q = s["quant"]
        row = {
            "label": label, "run_id": run_id, "count": q["count"], "winRate": q["winRate"],
            "totalPnl": q["totalPnl"], "totalGrossPnl": q["totalGrossPnl"], "costDrag": q["costDrag"],
            "avgR": q["avgR"], "maxDrawdown": q["maxDrawdown"], "unrealizedPnl": q["unrealizedPnl"],
            "openPositionCount": q["openPositionCount"],
        }
        results.append(row)
        print("RESULT", json.dumps(row), flush=True)

    print("ALL DONE", flush=True)
    print(json.dumps(results), flush=True)


if __name__ == "__main__":
    main()
