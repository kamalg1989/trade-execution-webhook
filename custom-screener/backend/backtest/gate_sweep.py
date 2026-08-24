"""One-off driver script: sweeps each Stage-1 gate threshold individually
(loosen + tighten) against the known-best baseline (run #54: QUANT-only,
max_picks=2, ema21_trail, safety_sl=10%, stacking_guard=OVERRIDE, Jan-Aug
2026 window), one gate changed at a time so results aren't conflated. Not
part of the app -- run manually via `python3 gate_sweep.py` on the VPS,
reads/writes only through the already-deployed /api/backtest/runs API.
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
    ("g1-turnover-loosen-5cr", {"gate_min_turnover_cr": 5}),
    ("g1-turnover-tighten-15cr", {"gate_min_turnover_cr": 15}),
    ("g2-baserange-loosen-25pct", {"gate_max_base_range_pct": 25}),
    ("g2-baserange-tighten-15pct", {"gate_max_base_range_pct": 15}),
    ("g3-volmult-loosen-0.6x", {"gate_min_vol_mult": 0.6}),
    ("g3-volmult-tighten-1.0x", {"gate_min_vol_mult": 1.0}),
    ("g4-upmove-loosen-10pct", {"gate_min_prior_upmove_pct": 10}),
    ("g4-upmove-tighten-25pct", {"gate_min_prior_upmove_pct": 25}),
    ("g5-giveback-loosen-40pct", {"gate_max_giveback_pct": 40}),
    ("g5-giveback-tighten-20pct", {"gate_max_giveback_pct": 20}),
    ("g6-dryup-loosen-1.6x", {"gate_max_vol_dryup_ratio": 1.6}),
    ("g6-dryup-tighten-1.0x", {"gate_max_vol_dryup_ratio": 1.0}),
    ("g7-dist-loosen-8pct", {"gate_max_dist_from_high_pct": -8}),
    ("g7-dist-tighten-2pct", {"gate_max_dist_from_high_pct": -2}),
    ("g8-ifp-loosen-0.15", {"gate_min_ifp_score": 0.15}),
    ("g8-ifp-tighten-0.35", {"gate_min_ifp_score": 0.35}),
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
        body["notes"] = f"gate-sweep: {label}"
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
            time.sleep(5)
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
