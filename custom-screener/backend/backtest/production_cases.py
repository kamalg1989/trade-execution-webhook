"""Four fixed production cases, 2016-2026, submitted through the API for UI review.

Four runs. Not a sweep — that is the point. Each isolates ONE change against the
same production baseline over the same decade, so any difference is attributable.

  1. PRODUCTION AS-IS. screen_gpt.py's live settings: base_stage_max_allowed=4,
     0.25% risk per trade, 10% max capital per trade, 3 alerts/day, the R-ladder
     exits (breakeven at +1R, half-book at +2R, EMA21 trail), 8% safety floor.
     This is the measured baseline everything else is compared to.

  2. + base_stage_max_allowed = 2. Trade only early-stage bases. This was the
     standout in the single-window Stage 2 sweep, but the 11-year campaign
     showed it CUTS RETURN (Rs.169k vs Rs.202k) while cutting trades 23% and
     drawdown 25% — a risk trade-off, not the free win it first appeared. Run
     here over one continuous decade to see it on a compounding basis.

  3. HALF SIZE. 0.125% risk per trade and 5% max capital per trade — every
     position exactly half its production size. Expected to halve both return
     and drawdown; included because if it does NOT scale linearly, something in
     the sizing interacts with the cost floor (the flat Rs.14.75 DP charge and
     the slippage minimum hurt small positions disproportionately).

  4. FULL SIZE + REALISTIC EXIT FILLS. Production sizing, but close-triggered
     exits fill at the NEXT session's open instead of at the triggering close.
     This is the honest execution model: a rule that reads the close cannot be
     executed at that close, because the close is only known once the session
     has ended. The gap between case 1 and case 4 is the size of the optimism
     baked into every breakout number in this report.

Run:  nohup python3 -m backtest.production_cases > /root/prodcases.log 2>&1 &
"""
from __future__ import annotations

import json
import time
import urllib.request

API = "http://localhost:8005/api"
TAG = "PROD"
WINDOW = ("2016-01-01", "2026-08-08")

# screen_gpt.py's live exit ladder.
EXITS = {
    "breakeven": True, "half_booking": True, "trailing": True,
    "fixed_target": False,
    "ema10_trail": False, "ema21_trail": True, "ema50_trail": False,
    "chandelier_trail": False, "swing_trail": False,
    "failed_breakout_exit": False, "swing_break_exit": False,
    "next_open_exit": False,
}

# Production defaults. max_picks_per_track=3 mirrors MAX_ALERTS_PER_RUN;
# stage2_base_stage_max_allowed=4 mirrors BASE_STAGE_MAX_ALLOWED; risk and
# capital caps mirror the 0.0025 / 0.10 literals in calculate_position().
#
# NOTE ON stage2_base_stage_max_allowed. It is deliberately ABSENT here even
# though production's value is 4. funnel_stage2.apply_overrides() returns True
# if ANY stage2_* key is non-None, which routes the run onto the config-HASHED
# signal cache. That cache is cold for hash(basemax=4), so passing the value
# explicitly forces a full Stage 2 recompute of ~60 symbols on every one of
# 2,625 days — measured at 1.6 days/min, i.e. ~27 hours for one run, with four
# Postgres backends saturated. Omitting it uses screen_gpt's own default (4,
# identically) via the warm shared cache and runs ~60x faster. Case 2 sets it
# to 2, whose hash is already warm from the earlier campaigns.
PROD = {
    "strategy": "BREAKOUT", "track_mode": "QUANT", "capital": 400000,
    "max_picks_per_track": 3,
    "risk_per_trade_pct": 0.25,
    "max_capital_per_trade_pct": 10.0,
    "safety_sl_pct": 8.0,
    "stacking_guard": True, "stacking_guard_mode": "OVERRIDE",
    "exit_config": EXITS,
}

CASES = [
    ("1. production as-is", {}),
    ("2. production + baseStage<=2", {"stage2_base_stage_max_allowed": 2}),
    ("3. production at 50% size",
     {"risk_per_trade_pct": 0.125, "max_capital_per_trade_pct": 5.0}),
    ("4. production, next-open exit fills",
     {"exit_config": {**EXITS, "next_open_exit": True}}),
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

    print(f"{len(CASES)} production cases over {WINDOW[0]} .. {WINDOW[1]}\n", flush=True)
    print(f"{'case':<40}{'trades':>8}{'win%':>7}{'realized':>13}{'unreal':>13}"
          f"{'total':>13}{'maxDD':>11}", flush=True)

    for label, over in CASES:
        notes = f"{TAG}: {label}"
        if notes in done:
            print(f"SKIP {label}", flush=True)
            continue
        body = {**PROD, **over, "start_date": WINDOW[0], "end_date": WINDOW[1],
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
        print(f"{label:<40}{(d.get('tradeCount') or 0):>8}{q.get('winRate', 0):>7.1f}"
              f"{(d.get('realizedPnl') or 0):>13,.0f}{(d.get('unrealizedPnl') or 0):>13,.0f}"
              f"{(d.get('totalPnl') or 0):>13,.0f}{q.get('maxDrawdown', 0):>11,.0f}",
              flush=True)

    print("\nALLDONE", flush=True)


if __name__ == "__main__":
    main()
