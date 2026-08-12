"""Submit the portfolio candidates through the API so every one is reviewable
in the UI with its full trade log, not just as a line in a sweep log.

The set is chosen to make the review self-contained: it carries the baseline the
controls must beat, the top-N ladder that the plateau claim rests on, and the two
controls that FAILED. Including the failures is deliberate — a matrix showing
only what worked gives no way to judge how much better it is than what didn't,
and the rejected controls are the ones most likely to be proposed again.

Both the FIT and TEST windows for the leading candidate are included too, so the
out-of-sample check is visible in the UI rather than living only in a log file.

Run:  nohup python3 -m backtest.portfolio_ui > /root/portfolio_ui.log 2>&1 &
"""
from __future__ import annotations

import json
import time
import urllib.request

API = "http://localhost:8005/api"
TAG = "pf"

FULL = ("2016-01-01", "2026-08-08")
FIT = ("2016-01-01", "2020-12-31")
TEST = ("2021-01-01", "2026-08-08")

BASE = {
    "strategy": "PORTFOLIO", "track_mode": "QUANT", "capital": 400000,
    "pos_momentum": "pct_chg_6m", "pos_rebalance_days": 63,
    "pos_min_turnover_cr": 5.0,
}


def cfg(top_n: int, sl: float, **extra) -> dict:
    return {**BASE, "pos_top_n": top_n, "pos_buffer_n": top_n * 2,
            "pos_sl_pct": sl, **extra}


RUNS = [
    # --- the baseline every control must beat
    ("baseline-top20-nostop", FULL, cfg(20, 0.0)),
    ("stop15-top20", FULL, cfg(20, 15.0)),

    # --- the top-N ladder: the plateau claim stands or falls on these
    ("stop15-top25", FULL, cfg(25, 15.0)),
    ("stop15-top30", FULL, cfg(30, 15.0)),
    ("stop15-top35", FULL, cfg(35, 15.0)),
    ("stop15-top40", FULL, cfg(40, 15.0)),

    # --- the stop is a RANGE, so both ends are shown at the leading top-N
    ("stop20-top30", FULL, cfg(30, 20.0)),
    ("stop10-top30-REJECTED", FULL, cfg(30, 10.0)),

    # --- controls that FAILED, kept in so the comparison is honest
    ("top30-volscale-floor25-FAILED", FULL, cfg(30, 15.0, pf_vol_mode="pct",
                                                pf_vol_floor=25)),
    ("top30-ddthrottle10-FAILED", FULL, cfg(30, 15.0, pf_dd_throttle_at=0.10)),
    # --- controls that were roughly neutral
    ("top30-sector3", FULL, cfg(30, 15.0, pf_max_stocks_per_sector=3,
                                pf_max_per_sector_pct=30)),
    ("top30-volscale-floor75", FULL, cfg(30, 15.0, pf_vol_mode="pct",
                                         pf_vol_floor=75)),

    # --- out-of-sample check on the leading candidate, visible in the UI
    ("WF-fit-2016-20-top30", FIT, cfg(30, 15.0)),
    ("WF-test-2021-26-top30", TEST, cfg(30, 15.0)),
    ("WF-fit-2016-20-top20", FIT, cfg(20, 15.0)),
    ("WF-test-2021-26-top20", TEST, cfg(20, 15.0)),
]


def post(path, body):
    req = urllib.request.Request(f"{API}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
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
        print(f"[warn] could not read runs ({e}); nothing will be skipped", flush=True)
    return done


def main():
    done = existing_done()
    print(f"{len(RUNS)} portfolio runs ({len(done)} already complete)", flush=True)
    print(f"{'config':<34}{'CAGR%':>8}{'maxDD%':>9}{'ulcer':>8}{'Martin':>8}"
          f"{'w12m%':>9}{'final':>13}", flush=True)
    for label, (start, end), body in RUNS:
        notes = f"{TAG}: {label}"
        if notes in done:
            print(f"SKIP {notes}", flush=True)
            continue
        payload = {**body, "start_date": start, "end_date": end, "notes": notes}
        try:
            rid = post("/backtest/runs", payload)["id"]
        except Exception as e:
            print(f"{label} SUBMIT FAILED: {e}", flush=True)
            continue
        while True:
            time.sleep(6)
            r = get(f"/backtest/runs/{rid}")
            if r["status"] != "RUNNING":
                break
        if r["status"] != "COMPLETED":
            print(f"{label} run {rid} {r['status']}: {r.get('error')}", flush=True)
            continue
        print(f"{label:<34}{r.get('pfCagrPct', 0):>8.2f}{r.get('pfMaxDDPct', 0):>9.1f}"
              f"{r.get('pfUlcer', 0):>8.2f}{r.get('pfMartin', 0):>8.2f}"
              f"{r.get('pfWorst12mPct', 0):>9.1f}{r.get('pfFinalEquity', 0):>13,.0f}",
              flush=True)
    print("ALLDONE", flush=True)


if __name__ == "__main__":
    main()
