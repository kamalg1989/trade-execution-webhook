"""Analyse the positional sweep — designed to detect self-deception, not to
find a winner.

With 48 configs a top scorer always exists. These three views decide whether it
means anything:

  1. FIT vs TEST. Rank every config on 2016-2020, then on 2021-2026, and
     correlate the two rankings (Spearman). High correlation => the parameter
     surface carries information that transfers. Near zero => the ranking is
     noise and no config should be adopted regardless of its total.

  2. PER-AXIS MARGINALS. Averaging over everything else, does performance vary
     SMOOTHLY along each axis? A plateau is evidence of a real effect; an
     isolated spike surrounded by poor neighbours is evidence of luck.

  3. ROBUSTNESS, not maximum. Configs ranked by worst-year and by
     return/drawdown, because a ~100%-deployed book lives or dies on its bad
     years, not its best.
"""
from __future__ import annotations

import json
import statistics as st
import sys
from collections import defaultdict

FIT = {"2016", "2017", "2018", "2019", "2020"}
TEST = {"2021", "2022", "2023", "2024", "2025", "2026ytd"}


def key(r):
    return (r["momentum"], r["rebalance"], r["top_n"])


def spearman(a: dict, b: dict) -> float:
    common = [k for k in a if k in b]
    if len(common) < 3:
        return 0.0
    ra = {k: i for i, k in enumerate(sorted(common, key=lambda k: -a[k]))}
    rb = {k: i for i, k in enumerate(sorted(common, key=lambda k: -b[k]))}
    n = len(common)
    d2 = sum((ra[k] - rb[k]) ** 2 for k in common)
    return 1 - 6 * d2 / (n * (n * n - 1))


def main():
    rows = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "/root/possweep.json"))
    by_cfg = defaultdict(list)
    for r in rows:
        by_cfg[key(r)].append(r)

    fit_tot, test_tot, agg = {}, {}, {}
    for k, rs in by_cfg.items():
        f = sum(r["total"] for r in rs if r["window"] in FIT)
        t = sum(r["total"] for r in rs if r["window"] in TEST)
        fit_tot[k], test_tot[k] = f, t
        tots = [r["total"] for r in rs]
        agg[k] = {
            "total": sum(tots), "fit": f, "test": t,
            "yrs_pos": sum(1 for v in tots if v > 0), "n": len(tots),
            "worst": min(tots),
            "maxDDpct": max(r["maxDDpct"] for r in rs),
            "trades": sum(r["trades"] for r in rs) / max(len(rs), 1),
        }

    rho = spearman(fit_tot, test_tot)
    print("=" * 100)
    print("1. DOES THE PARAMETER RANKING TRANSFER?  (the question that decides everything)")
    print("=" * 100)
    print(f"Spearman rank correlation, FIT(2016-20) vs TEST(2021-26): {rho:+.2f}")
    print("   > +0.5  ranking transfers — a sweet spot is meaningful")
    print("   ~  0    ranking is noise — DO NOT adopt any config from this grid")
    print("   < -0.3  actively inverted — the 'best' config is a trap\n")

    best_fit = sorted(fit_tot, key=lambda k: -fit_tot[k])[:5]
    print("Top-5 chosen on FIT only, and how they then did on TEST:")
    print(f"{'config':<34}{'FIT':>12}{'TEST':>12}{'TEST rank':>11}")
    test_rank = {k: i + 1 for i, k in enumerate(sorted(test_tot, key=lambda k: -test_tot[k]))}
    for k in best_fit:
        print(f"{str(k):<34}{fit_tot[k]/1000:>11.0f}k{test_tot[k]/1000:>11.0f}k"
              f"{test_rank[k]:>8}/{len(test_tot)}")

    print("\n" + "=" * 100)
    print("2. PER-AXIS MARGINALS  (plateau = real, spike = luck)")
    print("=" * 100)
    for axis, idx in (("momentum", 0), ("rebalance_days", 1), ("top_n", 2)):
        buckets = defaultdict(list)
        for k, a in agg.items():
            buckets[k[idx]].append(a["total"])
        print(f"\n  {axis}:")
        for v in sorted(buckets, key=lambda x: (str(type(x)), x)):
            vals = buckets[v]
            print(f"    {str(v):<14} mean {st.mean(vals)/1000:>8.0f}k   "
                  f"median {st.median(vals)/1000:>8.0f}k   n={len(vals)}")

    print("\n" + "=" * 100)
    print("3. RANKED BY ROBUSTNESS  (worst year and drawdown, not peak P&L)")
    print("=" * 100)
    print(f"{'config':<34}{'total':>10}{'yrs+':>6}{'worst yr':>11}{'maxDD%':>9}{'trades/yr':>11}")
    ranked = sorted(agg.items(), key=lambda kv: (-kv[1]["yrs_pos"], kv[1]["worst"]), reverse=False)
    ranked = sorted(agg.items(), key=lambda kv: (kv[1]["yrs_pos"], -kv[1]["maxDDpct"]), reverse=True)
    for k, a in ranked[:12]:
        print(f"{str(k):<34}{a['total']/1000:>9.0f}k{a['yrs_pos']:>4}/{a['n']}"
              f"{a['worst']/1000:>10.0f}k{a['maxDDpct']:>8.0f}%{a['trades']:>11.0f}")

    print("\nReminder: a ~100%-deployed book is judged on worst year and maxDD.")
    print("If section 1 shows no transfer, sections 2-3 are describing noise.")


if __name__ == "__main__":
    main()
