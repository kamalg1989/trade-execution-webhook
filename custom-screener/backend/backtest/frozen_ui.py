"""Run the FROZEN configuration through the API: continuously, year by year, and
across the variations worth inspecting — so all of it is reviewable in the UI
with full trade logs, realized and unrealized P&L.

THE ONE THING TO UNDERSTAND BEFORE READING THE PER-YEAR ROWS. A calendar-year run
RESETS CAPITAL to Rs.4L on 1 January and LIQUIDATES nothing — it simply starts a
fresh book. That is exactly the framing this whole exercise concluded was
misleading, because it cannot compound and cannot show a drawdown that spans a
year boundary. The per-year runs are included because seeing which names were
held and what happened in a specific year is genuinely useful for inspection, NOT
because summing them is a valid measure of the strategy.

  * To judge the STRATEGY, read the CONTINUOUS row. It is the only one whose
    CAGR and max drawdown mean anything.
  * To inspect a YEAR — which stocks, which stops fired, what the trade log looks
    like — read that year's row.

Summing the eleven per-year P&L figures does NOT give the continuous result, and
the difference is not a rounding error: compounding over a decade is most of the
outcome.

Also submitted are the frozen config's near neighbours, so the sensitivity that
the report describes can be seen rather than taken on trust: the two ends of the
supported stop range, the diversification alternatives, and the two rejected
overlays (kept precisely so the comparison is visible rather than asserted).

Run:  nohup python3 -m backtest.frozen_ui > /root/frozen_ui.log 2>&1 &
"""
from __future__ import annotations

import json
import time
import urllib.request

API = "http://localhost:8005/api"
TAG = "FROZEN"

# The frozen configuration — BACKTEST_REPORT section 10. Every risk overlay off.
FROZEN = {
    "strategy": "PORTFOLIO", "track_mode": "QUANT", "capital": 400000,
    "pos_momentum": "pct_chg_6m", "pos_rebalance_days": 63,
    "pos_top_n": 20, "pos_buffer_n": 40, "pos_min_turnover_cr": 5.0,
    "pos_sl_pct": 15.0,
    "pf_vol_mode": "none", "pf_dd_throttle_at": 0.0,
    "pf_max_stocks_per_sector": 99, "pf_max_per_sector_pct": 100.0,
    "pf_max_per_stock_pct": 100.0, "pf_require_sector": False,
}

FULL = ("2016-01-01", "2026-08-08")
YEARS = [(str(y), f"{y}-01-01", f"{y}-12-31") for y in range(2016, 2026)]
YEARS.append(("2026ytd", "2026-01-01", "2026-08-08"))


def runs() -> list[tuple]:
    out = [("continuous 2016-2026 [THE ONE THAT COUNTS]", FULL[0], FULL[1], {})]
    for label, s, e in YEARS:
        out.append((f"year {label} [capital resets - inspection only]", s, e, {}))

    # --- variations, on the full continuous window so they are comparable
    v = [
        ("stop 20% (other end of supported range)", {"pos_sl_pct": 20.0}),
        ("stop 10% (REJECTED out of sample)", {"pos_sl_pct": 10.0}),
        ("no stop (baseline the stop must beat)", {"pos_sl_pct": 0.0}),
        ("top 35 (lower drawdown, costs CAGR)", {"pos_top_n": 35, "pos_buffer_n": 70}),
        ("top 45 (lower drawdown still)", {"pos_top_n": 45, "pos_buffer_n": 90}),
        ("12m momentum instead of 6m", {"pos_momentum": "pct_chg_1y"}),
        ("3m momentum instead of 6m", {"pos_momentum": "pct_chg_3m"}),
        ("rebalance 21d (monthly)", {"pos_rebalance_days": 21}),
        ("rebalance 126d (half-yearly)", {"pos_rebalance_days": 126}),
        ("vol scaling floor 25% (REJECTED)", {"pf_vol_mode": "pct", "pf_vol_floor": 25}),
        ("dd throttle -10% (REJECTED)", {"pf_dd_throttle_at": 0.10}),
        ("sector cap 3/sector", {"pf_max_stocks_per_sector": 3,
                                 "pf_max_per_sector_pct": 30.0}),
    ]
    out += [(f"variant: {lbl}", FULL[0], FULL[1], ov) for lbl, ov in v]
    return out


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
        print(f"[warn] could not read runs ({e}); nothing skipped", flush=True)
    return done


def main():
    jobs = runs()
    done = existing_done()
    print(f"{len(jobs)} runs ({len(done)} already complete)\n", flush=True)
    print(f"{'run':<48}{'realized':>12}{'unreal':>12}{'total':>12}"
          f"{'CAGR%':>8}{'maxDD%':>8}{'trades':>8}", flush=True)

    for label, start, end, over in jobs:
        notes = f"{TAG}: {label}"
        if notes in done:
            print(f"SKIP {label}", flush=True)
            continue
        body = {**FROZEN, **over, "start_date": start, "end_date": end,
                "notes": notes}
        try:
            rid = post("/backtest/runs", body)["id"]
        except Exception as e:
            print(f"{label}: SUBMIT FAILED {e}", flush=True)
            continue
        while True:
            time.sleep(6)
            r = get(f"/backtest/runs/{rid}")
            if r["status"] != "RUNNING":
                break
        if r["status"] != "COMPLETED":
            print(f"{label}: run {rid} {r['status']} {r.get('error')}", flush=True)
            continue
        # Re-read from the LIST endpoint: realized/unrealized are computed there
        # as aggregates over backtest_trades and are not on the detail payload.
        lst = {x["id"]: x for x in get("/backtest/runs")}
        d = lst.get(rid, {})
        print(f"{label:<48}{(d.get('realizedPnl') or 0):>12,.0f}"
              f"{(d.get('unrealizedPnl') or 0):>12,.0f}"
              f"{(d.get('totalPnl') or 0):>12,.0f}"
              f"{(r.get('pfCagrPct') or 0):>8.2f}{(r.get('pfMaxDDPct') or 0):>8.1f}"
              f"{(d.get('tradeCount') or 0):>8}", flush=True)

    print("\nNOTE: summing the per-year rows does NOT equal the continuous row.")
    print("Each year restarts at Rs.4L; the continuous run compounds.", flush=True)
    print("ALLDONE", flush=True)


if __name__ == "__main__":
    main()
