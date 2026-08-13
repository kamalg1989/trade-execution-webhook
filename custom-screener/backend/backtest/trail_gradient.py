"""Final validation: is 'looser trail = better' a GRADIENT or a lucky cell?

Five independent tests have now pointed the same way — every mechanism that cuts
a winner early costs money:

    half-booking at 2R      OFF  beats ON      1.92 -> 2.09
    R-ladder ratchet        OFF  beats ON      1.92 -> 3.63
    both off                                          6.16
    safety stop             15% > 12% > 8%     2.55 / 2.46 / 1.43
    trail speed             EMA50 > EMA21 > EMA10   7.12 / 6.16 / 2.51
    failed-breakout exit    catastrophic       win rate 22.8%, negative

That last row is the important one: the trail-speed result is MONOTONIC in
looseness across three settings. A gradient across an ordered axis is the shape
of a real effect; an isolated spike is the shape of noise. This project has been
burned by spikes eight times and has never yet seen a clean monotone axis
survive, so it is worth spending runs to establish whether this one does.

Two open questions:
  1. Does the gradient CONTINUE past EMA50, or is there an optimum? If looser is
     always better the limit is 'no trail at all', which cannot be true — so
     finding where it turns tells us whether we are on a real curve or riding
     noise off the edge of the tested range.
  2. Does EMA50 TRANSFER? B5/EMA21 beat the baseline in both FIT and TEST
     (1.37 vs -0.04, 4.62 vs 2.08). EMA50 has only been run on the full window,
     and its drawdown is nearly double (Rs.94k vs Rs.51k) at a 29.2% win rate,
     so it needs the same split before it can be preferred.

Run:  nohup python3 -m backtest.trail_gradient > /root/trail.log 2>&1 &
"""
from __future__ import annotations

import json
import time
import urllib.request

API = "http://localhost:8005/api"
TAG = "TRAIL"

FULL = ("2016-01-01", "2026-08-08")
FIT = ("2016-01-01", "2020-12-31")
TEST = ("2021-01-01", "2026-08-08")

# half_booking and trailing OFF throughout — established as the better base by
# the 2x2. Only the trail MECHANISM and the safety floor vary here.
EXITS = {
    "breakeven": True, "half_booking": False, "trailing": False,
    "fixed_target": False,
    "ema10_trail": False, "ema21_trail": False, "ema50_trail": False,
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
    # ---- 1. does the gradient continue past EMA50? -------------------------
    # Chandelier at rising ATR multiples is the only looser-than-EMA50 trail
    # available, and it is a genuine ordered axis rather than a new mechanism.
    ("grad EMA10 (tightest)", FULL, ex(ema10_trail=True)),
    ("grad EMA21", FULL, ex(ema21_trail=True)),
    ("grad EMA50", FULL, ex(ema50_trail=True)),
    ("grad chandelier 3 ATR", FULL, ex(chandelier_trail=True), {"chandelier_atr_mult": 3.0}),
    ("grad chandelier 5 ATR", FULL, ex(chandelier_trail=True), {"chandelier_atr_mult": 5.0}),
    ("grad NO trail (structural SL only)", FULL, ex()),

    # ---- 2. does EMA50 transfer? ------------------------------------------
    ("split EMA50 FIT 2016-20", FIT, ex(ema50_trail=True)),
    ("split EMA50 TEST 2021-26", TEST, ex(ema50_trail=True)),
    ("split notrail FIT 2016-20", FIT, ex()),
    ("split notrail TEST 2021-26", TEST, ex()),
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

    for case in CASES:
        label, (s, e), over = case[0], case[1], case[2]
        extra = case[3] if len(case) > 3 else {}
        notes = f"{TAG}: {label}"
        if notes in done:
            print(f"SKIP {label}", flush=True)
            continue
        body = {**BASE, **over, **extra,
                "start_date": s, "end_date": e, "notes": notes}
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

    print("\nREAD: a monotone curve that TURNS somewhere is a real effect with an")
    print("optimum. A curve still rising at 'no trail at all' means the metric is")
    print("rewarding risk we are not measuring, and ret/DD is the wrong score.")
    print("ALLDONE", flush=True)


if __name__ == "__main__":
    main()
