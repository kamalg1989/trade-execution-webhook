"""Unattended campaign: find the best low-trade-count breakout setup.

TWO PHASES, the second chosen from the first's results, so this converges rather
than just enumerating.

  PHASE A — INTAKE. Trade count is governed by picks-per-scan x scans-per-year,
  NOT by gate strictness. Measured: the funnel yields ~23 candidates/day and only
  4.5% of days have fewer than 3, so a cap of 3 binds almost every day and
  filtering the pool changes WHICH three are taken, not how many. The entry-v2
  gate cut candidates 62% and trade count went UP. So intake is the lever.

  PHASE B — STOPS AND EXITS, run on whichever Phase A config wins on
  return-per-drawdown. Drawdown is the binding constraint on how much capital
  can be allocated, so it is scored on ratio rather than absolute P&L.

WHAT IS HELD CONSTANT, and why it is not re-tested:
  * entry_v2_buy_points = FALSE. Tested and rejected: daily Rs.194k -> Rs.102k,
    weekly Rs.99k -> Rs.37k, worse drawdown and win rate in both. The detectors
    provably fire on real patterns — the patterns simply do not predict.
  * base_stage_ladder = 'prod'. The v2 ladder cost 18% of return for 9% less
    drawdown — worse than proportional.
  * next_open_exit = TRUE. Production's sl_engine runs at 18:00 IST when Dhan
    rejects market orders and places forever orders that fill at the NEXT OPEN.
    Modelling close-fills understates production by ~Rs.90k over the decade.
  * stage2_base_stage_max_allowed = 2, whose config hash is already warm.

Resumable: a case whose notes tag already COMPLETED is skipped.

Run:  nohup python3 -m backtest.intake_campaign > /root/intake.log 2>&1 &
"""
from __future__ import annotations

import json
import time
import urllib.request

API = "http://localhost:8005/api"
TAG = "INTAKE"
WINDOW = ("2016-01-01", "2026-08-08")

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
    "risk_per_trade_pct": 0.25,
    "max_capital_per_trade_pct": 10.0,
    "safety_sl_pct": 10.0,
    "stacking_guard": True, "stacking_guard_mode": "OVERRIDE",
    "entry_v2_buy_points": False,
    "base_stage_ladder": "prod",
    "exit_config": EXITS,
}

# ---- PHASE A: intake ------------------------------------------------------
PHASE_A = [
    ("A1 weekly x1  (~42/yr)", {"signal_cadence": "weekly", "max_picks_per_track": 1}),
    ("A2 weekly x2  (~83/yr)", {"signal_cadence": "weekly", "max_picks_per_track": 2}),
    ("A3 weekly x3  (~124/yr)", {"signal_cadence": "weekly", "max_picks_per_track": 3}),
    ("A4 monthly x4 (~43/yr)", {"signal_cadence": "monthly", "max_picks_per_track": 4}),
    ("A5 monthly x8 (~86/yr)", {"signal_cadence": "monthly", "max_picks_per_track": 8}),
    ("A6 daily x1   (~127/yr)", {"signal_cadence": "daily", "max_picks_per_track": 1}),
]

# ---- PHASE B: stops / exits, applied to the Phase A winner ----------------
# Each isolates ONE mechanism. Absolute P&L is not the score — drawdown is what
# limits allocation, so these are ranked on return per unit of drawdown.
PHASE_B = [
    ("B1 safety 8% (production)", {"safety_sl_pct": 8.0}),
    ("B2 safety 12%", {"safety_sl_pct": 12.0}),
    ("B3 safety 15%", {"safety_sl_pct": 15.0}),
    ("B4 R-ladder only (no EMA21)", {"exit_config": {**EXITS, "ema21_trail": False}}),
    ("B5 EMA21 only (no R-ladder)", {"exit_config": {**EXITS, "trailing": False,
                                                     "half_booking": False}}),
    ("B6 + fixed 2R target", {"exit_config": {**EXITS, "fixed_target": True}}),
    ("B7 + failed-breakout exit", {"exit_config": {**EXITS,
                                                   "failed_breakout_exit": True}}),
    ("B8 EMA50 trail instead of EMA21", {"exit_config": {**EXITS, "ema21_trail": False,
                                                          "ema50_trail": True}}),
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


def completed_notes() -> set[str]:
    try:
        return {(r.get("params") or {}).get("notes") or ""
                for r in get("/backtest/runs") if r.get("status") == "COMPLETED"}
    except Exception as e:
        print(f"[warn] {e}", flush=True)
        return set()


def run_case(label: str, over: dict) -> dict | None:
    notes = f"{TAG}: {label}"
    if notes in completed_notes():
        print(f"SKIP {label}", flush=True)
        return None
    body = {**BASE, **over, "start_date": WINDOW[0], "end_date": WINDOW[1],
            "notes": notes}
    try:
        rid = post("/backtest/runs", body)["id"]
    except Exception as e:
        print(f"{label}: SUBMIT FAILED {e}", flush=True)
        return None
    while True:
        time.sleep(20)
        r = get(f"/backtest/runs/{rid}")
        if r["status"] != "RUNNING":
            break
    if r["status"] != "COMPLETED":
        print(f"{label}: {r['status']} {r.get('error')}", flush=True)
        return None
    d = {x["id"]: x for x in get("/backtest/runs")}.get(rid, {})
    q = get(f"/backtest/runs/{rid}/summary")["quant"]
    total = float(d.get("totalPnl") or 0)
    dd = float(q.get("maxDrawdown") or 0)
    out = {"label": label, "run": rid, "trades": d.get("tradeCount") or 0,
           "win": q.get("winRate", 0), "total": total, "dd": dd,
           # Return per unit of drawdown. Drawdown is what limits how much
           # capital can be allocated, so a config earning less with far less
           # pain can be the better one even at lower absolute P&L.
           "ratio": (total / dd) if dd else 0.0,
           "per_yr": round((d.get("tradeCount") or 0) / 10.6)}
    print(f"{label:<34}{out['trades']:>7}{out['per_yr']:>7}{out['win']:>7.1f}"
          f"{total:>13,.0f}{dd:>11,.0f}{out['ratio']:>8.2f}", flush=True)
    return out


def header():
    print(f"{'case':<34}{'trades':>7}{'/yr':>7}{'win%':>7}"
          f"{'total':>13}{'maxDD':>11}{'ret/DD':>8}", flush=True)


def main():
    print(f"CAMPAIGN {WINDOW[0]} .. {WINDOW[1]}\n", flush=True)
    print("PHASE A — INTAKE (trade count = picks x scans; gates cannot do this)")
    header()
    a_results = [r for c in PHASE_A if (r := run_case(*c))]

    if not a_results:
        print("\nPhase A produced nothing; stopping.", flush=True)
        return

    # Winner on return-per-drawdown, not absolute P&L.
    best = max(a_results, key=lambda r: r["ratio"])
    print(f"\nPHASE A WINNER on return/drawdown: {best['label']} "
          f"(ratio {best['ratio']:.2f}, {best['per_yr']}/yr)", flush=True)

    winner_over = dict(next(o for l, o in PHASE_A if l == best["label"]))
    print(f"\nPHASE B — STOPS & EXITS, on {best['label']}")
    header()
    b_results = [r for lbl, ov in PHASE_B
                 if (r := run_case(f"{lbl} @{best['label'].split()[0]}",
                                   {**winner_over, **ov}))]

    print("\n" + "=" * 88)
    print("CONSOLIDATED — ranked by return per unit of drawdown")
    print("=" * 88)
    header()
    for r in sorted(a_results + b_results, key=lambda r: -r["ratio"]):
        print(f"{r['label']:<34}{r['trades']:>7}{r['per_yr']:>7}{r['win']:>7.1f}"
              f"{r['total']:>13,.0f}{r['dd']:>11,.0f}{r['ratio']:>8.2f}")
    print("\nALLDONE", flush=True)


if __name__ == "__main__":
    main()
