"""Does the breakout edge survive if you scan WEEKLY or MONTHLY instead of daily?

THE QUESTION. Production scans nightly and alerts the top 3. Case 1 of the
production suite showed that over one continuous decade this earns ~Rs.35k on
Rs.4L — about 0.8%/yr — with a ~32% drawdown, across 4,012 trades. The portfolio
work says the problem is turnover: the same funnel's picks, held in a 63-session
book, produced the best risk-adjusted result measured anywhere in this project.

This tests the missing middle. Identical funnel, identical exits, identical
sizing — only the SCAN FREQUENCY changes.

WHAT IS AND IS NOT GATED. Only signal generation. Exits, entry fills and daily
mark-to-market still run every session. A weekly scan must never imply a weekly
stop-loss: that would be a materially more dangerous system than the one under
test, and the comparison would be meaningless.

SCAN DAY IS A ROBUSTNESS CHECK, NOT A SELECTION. A scan on any day sees all data
up to that day and none beyond it, so first-vs-last is a phase choice, not an
information one. 'last' is the headline because it matches a real weekend or
month-end review — decide with the whole period visible, act on the next open.
'first' is run purely to confirm the answer does not depend on that arbitrary
choice. If the two disagree materially, the correct conclusion is that the
result is fragile and NEITHER number should be trusted — not that the better one
is right.

Run:  nohup python3 -m backtest.cadence_cases > /root/cadence.log 2>&1 &
"""
from __future__ import annotations

import json
import time
import urllib.request

API = "http://localhost:8005/api"
TAG = "CADENCE"
WINDOW = ("2016-01-01", "2026-08-08")

# Production's live exit ladder: R-based trailing AND the EMA21 trail, which is
# what screen_gpt + sl_engine actually run together today.
EXITS = {
    "breakeven": True, "half_booking": True, "trailing": True,
    "fixed_target": False,
    "ema10_trail": False, "ema21_trail": True, "ema50_trail": False,
    "chandelier_trail": False, "swing_trail": False,
    "failed_breakout_exit": False, "swing_break_exit": False,
    "next_open_exit": False,
}

# Production + base_stage_max_allowed = 2, 10% safety SL.
# stage2_base_stage_max_allowed=2 routes to the config-hashed signal cache,
# whose hash is already warm from the earlier campaigns — unlike hash(4), which
# is cold and would make each run ~60x slower for an identical result.
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
    # headline — matches how the review would actually be done
    ("weekly, last session  [HEADLINE]", {"signal_cadence": "weekly", "signal_scan_day": "last"}),
    ("monthly, last session [HEADLINE]", {"signal_cadence": "monthly", "signal_scan_day": "last"}),
    # robustness — same thing, opposite phase. NOT for picking a winner.
    ("weekly, first session [robustness]", {"signal_cadence": "weekly", "signal_scan_day": "first"}),
    ("monthly, first session [robustness]", {"signal_cadence": "monthly", "signal_scan_day": "first"}),
    # in-batch reference so the comparison is not against a remembered number
    ("daily [reference = prod + baseStage<=2]", {"signal_cadence": "daily"}),
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

    print(f"{len(CASES)} cadence cases, {WINDOW[0]} .. {WINDOW[1]}\n", flush=True)
    print(f"{'case':<44}{'trades':>8}{'win%':>7}{'realized':>13}"
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
        print(f"{label:<44}{(d.get('tradeCount') or 0):>8}{q.get('winRate', 0):>7.1f}"
              f"{(d.get('realizedPnl') or 0):>13,.0f}{(d.get('unrealizedPnl') or 0):>13,.0f}"
              f"{(d.get('totalPnl') or 0):>13,.0f}{q.get('maxDrawdown', 0):>11,.0f}",
              flush=True)

    print("\nHEADLINE = last-session scans. The first-session rows exist only to")
    print("show the answer does not hinge on an arbitrary phase choice.")
    print("ALLDONE", flush=True)


if __name__ == "__main__":
    main()
