"""Full validation campaign — every candidate config x 11 one-year windows.

Motivation: every "edge" found so far was tuned on two windows (2025 + 2026)
that turned out to be the SAME low-breadth regime, and the 2024 out-of-sample
test exposed the consequence — an absolute 40%-breadth cap blocked 100% of
days in a bull year. This runs the whole config set across a decade of
independent one-year windows spanning very different regimes (measured avg
%-above-200SMA per year: 2019 ~25%, 2021 ~85%) so each setting can be judged
on regime robustness, not on a single lucky pair of windows.

Two phases, both unattended:
  1. WARM  — pre-populate the Stage 2 caches for every window, in parallel OS
             processes (see warm_cache.py for why processes, not threads).
             One pass per distinct config-hash: production defaults, and the
             basemax=2 override every candidate config shares.
  2. RUN   — submit each (config, window) to the backtest API sequentially
             (the API enforces one run at a time) and record the summary.

Resumable: a (config, window) whose notes tag already exists as a COMPLETED
run is skipped, so the campaign can be killed and relaunched without redoing
work. Progress is appended to the log as RESULT lines plus a final JSON blob.

Run:  nohup python3 -m backtest.campaign > /root/campaign.log 2>&1 &
"""
from __future__ import annotations

import asyncio
import json
import multiprocessing as mp
import sys
import time
import urllib.request
from datetime import datetime

sys.path.insert(0, "/root/trade-execution-webhook")

API = "http://localhost:8005/api"
TAG = "campaign-v3"

# One-year windows. 2026 is year-to-date (data ends ~2026-08-10).
# v3 windows stop at 2024 ON PURPOSE. The harvested filing calendar
# (earnings_filings) is dense for 2016-2024 (~10-14k filings/yr across
# ~1.6-2.1k symbols) but thin for 2025 (3,958) and essentially empty for 2026
# (26 filings, 12 symbols). Since a missing filing is treated as "no
# constraint", running the earnings rules over 2025/26 would silently measure
# a no-op and dilute the result — so those windows are excluded rather than
# reported as if they tested anything.
WINDOWS = [(str(y), f"{y}-01-01", f"{y}-12-31") for y in range(2016, 2025)]

EXITS = {
    "trailing": True, "breakeven": True, "half_booking": True, "fixed_target": False,
    "ema10_trail": False, "ema21_trail": True, "ema50_trail": False,
    "chandelier_trail": False, "swing_trail": False,
    "failed_breakout_exit": False, "swing_break_exit": False,
}

BASE = {
    "track_mode": "QUANT", "capital": 400000, "max_picks_per_track": 2,
    "safety_sl_pct": 10.0, "stacking_guard": True, "stacking_guard_mode": "OVERRIDE",
    "exit_config": EXITS,
}

# Each entry is what gets ADDED to BASE. Ordered cheapest-hypothesis-first so a
# partial campaign still answers the most important questions.
# Campaign v2 — cost-edge filters (sql/015). The v1 campaign showed the real
# problem is not selection but frictions: avg gross move +0.704%/trade vs
# 0.522% costs, i.e. costs eat ~74% of the gross edge. These test the two
# mechanisms that attack that directly, on top of C (the most consistent v1
# config: 6/11 years positive, lowest average drawdown).
C = {"stage2_base_stage_max_allowed": 2, "entry_breadth_require_rising": True}

# Campaign v3 — earnings-event rules (sql/016 + sql/017), on top of C.
# Lead times deliberately kept SHORT. SEBI LODR only requires a few working
# days' prior intimation of a results board meeting, so ~2-3 days of foresight
# is something a real trader would have had; assuming more turns the harvested
# broadcast dates into a look-ahead oracle. P exists purely as a sensitivity
# probe — if the benefit only shows up at the longer lead, that is evidence of
# look-ahead leakage rather than a tradable edge, and the rule gets rejected.
CONFIGS = [
    ("M-C+noEntry3d", {**C, "avoid_entry_days_before_earnings": 3}),
    ("N-C+exit2d", {**C, "exit_days_before_earnings": 2}),
    ("O-C+noEntry3d+exit2d", {**C, "avoid_entry_days_before_earnings": 3,
                              "exit_days_before_earnings": 2}),
    ("P-C+noEntry10d(leak probe)", {**C, "avoid_entry_days_before_earnings": 10}),
    ("C-ref(2016-24)", {**C}),
]


def post(path, body):
    req = urllib.request.Request(f"{API}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def get(path):
    with urllib.request.urlopen(f"{API}{path}", timeout=60) as r:
        return json.load(r)


# ---------------------------------------------------------------- warm phase

def _warm_worker(args):
    days_iso, base_stage_max = args
    import screen_gpt
    from app.db import create_pool
    from backtest import funnel, funnel_stage2

    screen_gpt.load_tick_sizes()
    screen_gpt.DEBUG = False
    cfg = {} if base_stage_max is None else {"stage2_base_stage_max_allowed": base_stage_max}
    active = funnel_stage2.apply_overrides(cfg)
    chash = funnel_stage2.config_hash() if active else None

    async def go():
        pool = await create_pool()
        try:
            for iso in days_iso:
                d = datetime.strptime(iso, "%Y-%m-%d").date()
                if active:
                    await funnel_stage2.build_candidates(pool, d, 400000, chash)
                else:
                    await funnel.build_candidates(pool, d, 400000)
        finally:
            await pool.close()

    asyncio.run(go())
    return len(days_iso)


async def _all_days():
    from app.db import create_pool
    pool = await create_pool()
    try:
        rows = await pool.fetch(
            "SELECT DISTINCT time::date AS d FROM ohlcv_data "
            "WHERE time::date BETWEEN $1 AND $2 ORDER BY d",
            datetime.strptime(WINDOWS[0][1], "%Y-%m-%d").date(),
            datetime.strptime(WINDOWS[-1][2], "%Y-%m-%d").date())
        return [r["d"].isoformat() for r in rows]
    finally:
        await pool.close()


def warm(days, base_stage_max, workers=2):
    label = "production-default" if base_stage_max is None else f"basemax={base_stage_max}"
    print(f"[warm] {label}: {len(days)} days across {workers} procs", flush=True)
    t0 = time.time()
    slices = [days[i::workers] for i in range(workers)]
    with mp.Pool(workers) as p:
        p.map(_warm_worker, [(s, base_stage_max) for s in slices])
    print(f"[warm] {label} done in {(time.time()-t0)/60:.1f} min", flush=True)


# ----------------------------------------------------------------- run phase

def existing_done() -> set[str]:
    """Notes tags of runs already COMPLETED, so a relaunch skips them."""
    done = set()
    try:
        for r in get("/backtest/runs"):
            n = (r.get("params") or {}).get("notes") or ""
            if n.startswith(TAG) and r.get("status") == "COMPLETED":
                done.add(n)
    except Exception:
        pass
    return done


def main():
    days = asyncio.run(_all_days())
    print(f"campaign: {len(CONFIGS)} configs x {len(WINDOWS)} windows, {len(days)} trading days",
          flush=True)

    warm(days, base_stage_max=2)      # shared by configs B..G
    # NOTE: deliberately not warming the production-default hash. Config A is
    # last and only 11 runs; warming its 2,625 days costs more wall-clock than
    # letting those 11 runs populate the cache themselves.

    done = existing_done()
    results = []
    for label, over in CONFIGS:
        for wname, start, end in WINDOWS:
            note = f"{TAG}: {label} [{wname}]"
            if note in done:
                print(f"SKIP (already done) {note}", flush=True)
                continue
            body = {**BASE, "start_date": start, "end_date": end, "notes": note}
            body.update(over)
            try:
                run_id = post("/backtest/runs", body)["id"]
            except Exception as e:
                print(f"SUBMIT FAILED {note}: {e}", flush=True)
                continue
            while True:
                time.sleep(6)
                try:
                    r = get(f"/backtest/runs/{run_id}")
                except Exception:
                    continue
                if r["status"] != "RUNNING":
                    break
            if r["status"] != "COMPLETED":
                print(f"FAILED {note} run {run_id}: {r.get('error')}", flush=True)
                continue
            q = get(f"/backtest/runs/{run_id}/summary")["quant"]
            row = {"config": label, "window": wname, "run_id": run_id,
                   "trades": q["count"], "winRate": q["winRate"],
                   "realized": q["totalPnl"], "unrealized": q["unrealizedPnl"],
                   "total": round(q["totalPnl"] + q["unrealizedPnl"], 2),
                   "avgR": q["avgR"], "maxDD": q["maxDrawdown"],
                   "costDrag": q["costDrag"]}
            results.append(row)
            print("RESULT " + json.dumps(row), flush=True)

    print("CAMPAIGN DONE", flush=True)
    print(json.dumps(results), flush=True)


if __name__ == "__main__":
    main()
