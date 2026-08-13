"""Validate the B5 outlier before believing it.

B5 — breakeven at 1R plus an EMA21 trail, with NO half-booking and NO R-ladder —
returned Rs.315,597 at Rs.51,203 drawdown: a return/drawdown of 6.16 against 2.64
for the next best and 1.92 for the baseline. Triple the return at the same
drawdown.

The exit breakdown says it is mechanically real rather than an artifact:
  TRAIL_SL        654 trades, avg hold 32d, +Rs.883,148
  STRUCTURAL_SL   523 trades, avg hold  9d, -Rs.491,939
  SAFETY_FLOOR     47 trades, avg hold  7d,  -Rs.89,335
Winners are held 3.5x longer than losers, and Rs.302k of the Rs.316k is REALIZED,
so it is not open-position markup. Removing the 2R half-book let winners run.

None of that is sufficient. A 3x outlier in a 14-cell grid is exactly the shape
of the eight ideas this project has already killed, every one of which had a
plausible mechanism attached. Two questions decide it:

  1. WHICH CHANGE DID IT? B5 turned off half_booking AND trailing together, so
     the credit is unattributed. The 2x2 separates them.
  2. DOES IT TRANSFER? Split-sample FIT(2016-20)/TEST(2021-26) against the same
     baseline. The failure mode to look for is the one that killed top-30:
     brilliant in-sample, mid-table out.

If the 2x2 shows one lever doing the work and the split holds, this is real. If
the effect only appears with both off, or evaporates on TEST, it is noise with a
good story.

Run:  nohup python3 -m backtest.b5_validation > /root/b5val.log 2>&1 &
"""
from __future__ import annotations

import json
import time
import urllib.request

API = "http://localhost:8005/api"
TAG = "B5VAL"

FULL = ("2016-01-01", "2026-08-08")
FIT = ("2016-01-01", "2020-12-31")
TEST = ("2021-01-01", "2026-08-08")

EXITS = {
    "breakeven": True, "half_booking": True, "trailing": True,
    "fixed_target": False,
    "ema10_trail": False, "ema21_trail": True, "ema50_trail": False,
    "chandelier_trail": False, "swing_trail": False,
    "failed_breakout_exit": False, "swing_break_exit": False,
    "next_open_exit": True,
}

BASE = {
    "strategy": "BREAKOUT", "track_mode": "QUANT", "capital": 400000,
    "stage2_base_stage_max_allowed": 2,
    "risk_per_trade_pct": 0.25, "max_capital_per_trade_pct": 10.0,
    "safety_sl_pct": 10.0,
    "stacking_guard": True, "stacking_guard_mode": "OVERRIDE",
    "signal_cadence": "weekly", "max_picks_per_track": 3,
    "entry_v2_buy_points": False, "base_stage_ladder": "prod",
}


def ex(**kw):
    return {"exit_config": {**EXITS, **kw}}


CASES = [
    # ---- 2x2: which of the two switches is doing the work? -----------------
    ("2x2 half=ON  trail=ON   (baseline)", FULL, ex()),
    ("2x2 half=OFF trail=OFF  (B5)", FULL, ex(half_booking=False, trailing=False)),
    ("2x2 half=OFF trail=ON", FULL, ex(half_booking=False)),
    ("2x2 half=ON  trail=OFF", FULL, ex(trailing=False)),
    # ---- split sample on B5 and the baseline -------------------------------
    ("SPLIT B5 FIT 2016-20", FIT, ex(half_booking=False, trailing=False)),
    ("SPLIT B5 TEST 2021-26", TEST, ex(half_booking=False, trailing=False)),
    ("SPLIT base FIT 2016-20", FIT, ex()),
    ("SPLIT base TEST 2021-26", TEST, ex()),
    # ---- is the EMA21 trail itself the load-bearing part? ------------------
    ("EMA50 trail, half=OFF trail=OFF", FULL,
     ex(half_booking=False, trailing=False, ema21_trail=False, ema50_trail=True)),
    ("EMA10 trail, half=OFF trail=OFF", FULL,
     ex(half_booking=False, trailing=False, ema21_trail=False, ema10_trail=True)),
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
    try:
        done = {(r.get("params") or {}).get("notes") or ""
                for r in get("/backtest/runs") if r.get("status") == "COMPLETED"}
    except Exception:
        done = set()

    print(f"{'case':<38}{'win%':>7}{'realized':>12}{'total':>12}"
          f"{'maxDD':>11}{'ret/DD':>8}{'trades':>8}", flush=True)

    for label, (s, e), over in CASES:
        notes = f"{TAG}: {label}"
        if notes in done:
            print(f"SKIP {label}", flush=True)
            continue
        body = {**BASE, **over, "start_date": s, "end_date": e, "notes": notes}
        try:
            rid = post("/backtest/runs", body)["id"]
        except Exception as exn:
            print(f"{label}: SUBMIT FAILED {exn}", flush=True)
            continue
        while True:
            time.sleep(20)
            r = get(f"/backtest/runs/{rid}")
            if r["status"] != "RUNNING":
                break
        if r["status"] != "COMPLETED":
            print(f"{label}: {r['status']}", flush=True)
            continue
        d = {x["id"]: x for x in get("/backtest/runs")}.get(rid, {})
        q = get(f"/backtest/runs/{rid}/summary")["quant"]
        tot = float(d.get("totalPnl") or 0)
        dd = float(q.get("maxDrawdown") or 0)
        print(f"{label:<38}{q.get('winRate',0):>7.1f}"
              f"{float(d.get('realizedPnl') or 0):>12,.0f}{tot:>12,.0f}"
              f"{dd:>11,.0f}{(tot/dd if dd else 0):>8.2f}"
              f"{d.get('tradeCount') or 0:>8}", flush=True)

    print("\nREAD: if half=OFF alone reproduces B5, half-booking was the problem.")
    print("If only half=OFF+trail=OFF works, the effect needs both and is more")
    print("fragile. If TEST collapses, it is the top-30 failure again.")
    print("ALLDONE", flush=True)


if __name__ == "__main__":
    main()
