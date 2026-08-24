"""Consolidate campaign.py's RESULT lines into a regime-robustness report.

Reads /root/campaign.log (or a path given as argv[1]) and prints:
  * a per-config x per-window grid of total P&L
  * per-config aggregates that actually matter for "consistent returns with
    low drawdown": total P&L, how many of the tested years were profitable,
    the WORST year, average and worst max-drawdown, and average avgR
  * each window's market-breadth regime, so a config's failures can be read
    against the regime it failed in

Deliberately reports "years profitable" and "worst year" rather than just
summed P&L: a config that makes a fortune in two years and bleeds in eight is
not what we're looking for, but summed P&L alone would hide that.

Works on a partial log, so it can be run while the campaign is still going.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict


def load(path):
    rows = []
    with open(path) as fh:
        for line in fh:
            if line.startswith("RESULT "):
                try:
                    rows.append(json.loads(line[7:]))
                except json.JSONDecodeError:
                    pass
    return rows


# Measured avg % of stocks above their 200SMA, per calendar year (from
# market_snapshot) — the regime each window represents.
BREADTH = {"2016": 57.3, "2017": 69.7, "2018": 33.9, "2019": 24.6, "2020": 47.1,
           "2021": 85.4, "2022": 50.4, "2023": 64.6, "2024": 70.0, "2025": 34.1,
           "2026ytd": 31.8}


def inr(v):
    if v is None:
        return "     —"
    return f"{v/1000:+8.1f}k"


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/root/campaign.log"
    rows = load(path)
    if not rows:
        print("no RESULT lines yet")
        return

    windows = [w for w in BREADTH if any(r["window"] == w for r in rows)]
    configs = sorted({r["config"] for r in rows})
    by = {(r["config"], r["window"]): r for r in rows}

    print(f"\n{'='*110}\nPER-WINDOW TOTAL P&L (realized + unrealized), by config\n{'='*110}")
    hdr = f"{'config':<24}" + "".join(f"{w:>10}" for w in windows)
    print(hdr)
    print(f"{'breadth %':<24}" + "".join(f"{BREADTH[w]:>10.0f}" for w in windows))
    print("-" * len(hdr))
    for c in configs:
        line = f"{c:<24}"
        for w in windows:
            r = by.get((c, w))
            line += f"{(r['total']/1000):>9.1f}k" if r else f"{'—':>10}"
        print(line)

    print(f"\n{'='*110}\nPER-CONFIG SUMMARY (across {len(windows)} one-year windows)\n{'='*110}")
    print(f"{'config':<24}{'total P&L':>12}{'yrs +ve':>9}{'worst yr':>12}"
          f"{'avg maxDD':>12}{'worst DD':>12}{'avg avgR':>10}{'trades':>8}")
    print("-" * 110)
    agg = []
    for c in configs:
        rs = [by[(c, w)] for w in windows if (c, w) in by]
        if not rs:
            continue
        tot = sum(r["total"] for r in rs)
        pos = sum(1 for r in rs if r["total"] > 0)
        worst = min(r["total"] for r in rs)
        dds = [r["maxDD"] for r in rs]
        avgrs = [r["avgR"] for r in rs if r["avgR"] is not None]
        trades = sum(r["trades"] for r in rs)
        agg.append((tot, c, pos, len(rs), worst, sum(dds)/len(dds), max(dds),
                    (sum(avgrs)/len(avgrs)) if avgrs else 0, trades))
    for tot, c, pos, n, worst, avgdd, maxdd, avgr, trades in sorted(agg, reverse=True):
        print(f"{c:<24}{tot/1000:>11.1f}k{f'{pos}/{n}':>9}{worst/1000:>11.1f}k"
              f"{avgdd/1000:>11.1f}k{maxdd/1000:>11.1f}k{avgr:>10.2f}{trades:>8}")

    print("\nNote: 'yrs +ve' and 'worst yr' matter more than summed P&L for a "
          "consistency goal —\na config can win on total while losing in most "
          "individual years.\n")


if __name__ == "__main__":
    main()
