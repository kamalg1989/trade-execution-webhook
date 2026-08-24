"""Submit every positional stop-loss variant through the API, so each one is
reviewable in the UI rather than only existing as a number in a sweep log.

positional_sweep.py answers "which stop is best" fast, in-process, with 3
workers — but it writes JSON to disk, which means the UI knows nothing about it
and the trade-level detail (which names stopped out, when, at what R) is thrown
away. This runs the same configs through /api/backtest/runs instead: slower
(the API is one-run-at-a-time by design) but every run then has a row in the run
list, a full trade log, an equity curve and realized/unrealized P&L columns.

Configs are the stop-type ladder held on the plateau-supported rotation settings
(6m momentum, 63-day rebalance, top-20, buffer-40) so the only thing varying
across runs is the stop itself — otherwise a difference could not be attributed
to it.

Resumable: a (config, window) whose notes tag already exists as a COMPLETED run
is skipped, so this can be killed and relaunched without redoing work.

Run:  nohup python3 -m backtest.positional_sl_ui > /root/possl_ui.log 2>&1 &
"""
from __future__ import annotations

import json
import time
import urllib.request

API = "http://localhost:8005/api"
TAG = "pos-sl"

WINDOWS = [(str(y), f"{y}-01-01", f"{y}-12-31") for y in range(2016, 2026)]
WINDOWS.append(("2026ytd", "2026-01-01", "2026-08-08"))

BASE = {
    "strategy": "POSITIONAL", "track_mode": "QUANT", "capital": 400000,
    "pos_momentum": "pct_chg_6m", "pos_rebalance_days": 63,
    "pos_top_n": 20, "pos_buffer_n": 40, "pos_min_turnover_cr": 5.0,
}

# label -> (mode, pct). Ordered so the reference points come first: if the run
# is interrupted part-way, what exists is still a usable comparison rather than
# an arbitrary slice of the ladder.
CONFIGS = [
    ("none", "none", 0.0),
    ("fixed15", "fixed", 15.0),
    ("fixed10", "fixed", 10.0),
    ("fixed20", "fixed", 20.0),
    ("trail20", "trail", 20.0),
    ("trail25", "trail", 25.0),
    ("ema21", "ema21", 0.0),
    ("sma50", "sma50", 0.0),
    ("ema50", "ema50", 0.0),
    ("sma200", "sma200", 0.0),
]


def post(path, body):
    req = urllib.request.Request(f"{API}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def get(path):
    with urllib.request.urlopen(f"{API}{path}", timeout=60) as r:
        return json.load(r)


def existing_done() -> set[str]:
    done = set()
    try:
        for r in get("/backtest/runs"):
            n = (r.get("params") or {}).get("notes") or ""
            if n.startswith(TAG) and r.get("status") == "COMPLETED":
                done.add(n)
    except Exception as e:
        print(f"[warn] could not read existing runs ({e}) — nothing will be skipped",
              flush=True)
    return done


def main():
    done = existing_done()
    print(f"{len(CONFIGS)} stop variants x {len(WINDOWS)} windows = "
          f"{len(CONFIGS)*len(WINDOWS)} runs ({len(done)} already complete)", flush=True)

    results = []
    for label, mode, pct in CONFIGS:
        for wname, start, end in WINDOWS:
            notes = f"{TAG}: {label} [{wname}]"
            if notes in done:
                print(f"SKIP {notes}", flush=True)
                continue
            body = {**BASE, "start_date": start, "end_date": end,
                    "pos_sl_mode": mode, "pos_sl_pct": pct, "notes": notes}
            try:
                run_id = post("/backtest/runs", body)["id"]
            except Exception as e:
                print(f"{notes} SUBMIT FAILED: {e}", flush=True)
                continue
            while True:
                time.sleep(5)
                r = get(f"/backtest/runs/{run_id}")
                if r["status"] != "RUNNING":
                    break
            if r["status"] != "COMPLETED":
                print(f"{notes} run {run_id} {r['status']}: {r.get('error')}", flush=True)
                continue
            q = get(f"/backtest/runs/{run_id}/summary")["quant"]
            row = {"label": label, "window": wname, "run_id": run_id,
                   "count": q["count"], "winRate": q["winRate"],
                   "totalPnl": q["totalPnl"], "unrealizedPnl": q["unrealizedPnl"],
                   "avgR": q["avgR"], "maxDrawdown": q["maxDrawdown"]}
            results.append(row)
            print("RESULT", json.dumps(row), flush=True)

    print("ALLDONE", flush=True)
    print(json.dumps(results), flush=True)


if __name__ == "__main__":
    main()
