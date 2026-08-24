"""DD attribution for run #1079 (Combo) vs a WIDE set of market internals."""
import asyncio, json, sys, urllib.request, datetime, statistics as st
sys.path.insert(0, "/root/trade-execution-webhook/custom-screener/backend")
from app.db import create_pool

def pearson(a, b):
    ma, mb = st.fmean(a), st.fmean(b)
    num = sum((x-ma)*(y-mb) for x, y in zip(a, b))
    da = sum((x-ma)**2 for x in a)**.5; db = sum((y-mb)**2 for y in b)**.5
    return num/(da*db) if da and db else 0.0

async def main():
    s = json.load(urllib.request.urlopen("http://127.0.0.1:8005/api/backtest/runs/1079/summary", timeout=300))
    cap = s["capital"]
    eq = [(datetime.date.fromisoformat(p["date"]), cap+p["quantRealizedCumPnl"]+p["quantUnrealizedPnl"]) for p in s["equityCurve"]]
    dd, pk = {}, 0.0
    for d, v in eq:
        pk = max(pk, v); dd[d] = (pk-v)/pk*100

    pool = await create_pool()
    rows = await pool.fetch("""
        SELECT indicator_date AS d,
          AVG(CASE WHEN close > sma_200 THEN 100.0 ELSE 0 END) AS b200,
          AVG(CASE WHEN close > sma_50  THEN 100.0 ELSE 0 END) AS b50,
          AVG(CASE WHEN pct_chg_3m > 0  THEN 100.0 ELSE 0 END) AS bmom,
          AVG(CASE WHEN is_new_52w_low  THEN 100.0 ELSE 0 END) AS newlows,
          AVG(CASE WHEN is_new_52w_high THEN 100.0 ELSE 0 END) AS newhighs,
          PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pct_chg_1m)         AS med1m,
          PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY atr_pct)           AS medatr,
          PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY dist_52w_high_pct) AS medd52,
          COUNT(*) AS n
        FROM stock_indicators
        WHERE turnover_1m_avg_cr >= 8 AND sma_200 IS NOT NULL
        GROUP BY indicator_date HAVING COUNT(*) >= 50 ORDER BY indicator_date""")
    idx = {r["d"]: float(r["level"]) for r in await pool.fetch(
        "SELECT d, level FROM index_proxy_daily WHERE proxy=$1 ORDER BY d", "SYNTH_EQW")}
    await pool.close()

    idd, ipk = {}, 0.0
    for d in sorted(idx):
        ipk = max(ipk, idx[d]); idd[d] = (ipk-idx[d])/ipk*100

    days = [r["d"] for r in rows if r["d"] in dd]
    M = {r["d"]: r for r in rows}
    sd = [dd[d] for d in days]
    def col(k): return [float(M[d][k]) if M[d][k] is not None else 0.0 for d in days]
    feats = {
      "pct_above_200sma": col("b200"), "pct_above_50sma": col("b50"),
      "pct_pos_3m_momentum": col("bmom"),
      "pct_new_52w_lows": col("newlows"), "pct_new_52w_highs": col("newhighs"),
      "median_1m_return": col("med1m"), "median_atr_mkt_vol": col("medatr"),
      "median_dist_52w_high": col("medd52"),
      "liquid_universe_size": [float(M[d]["n"]) for d in days],
    }
    ix = [idd.get(d) for d in days]
    hv = [i for i, v in enumerate(ix) if v is not None]
    print(f"run #1079 - {len(days)} matched days\n")
    print("CORRELATION with Combo DD depth (|r| ranked):")
    res = [(k, pearson(sd, v)) for k, v in feats.items()]
    res.append(("index_SYNTH_EQW_DD", pearson([sd[i] for i in hv], [ix[i] for i in hv])))
    for k, r in sorted(res, key=lambda x: -abs(x[1])):
        print(f"  {k:26} r = {r:+.3f}")

    print("\nCONDITIONAL - internals by Combo-DD bucket:")
    keys = ["pct_above_200sma","pct_above_50sma","pct_new_52w_lows","pct_new_52w_highs",
            "median_1m_return","median_atr_mkt_vol","median_dist_52w_high"]
    print("  bucket        days " + "".join(f"{h.split('_')[-1]:>9}" for h in keys) + "   idxDD")
    for lo, hi, lbl in ((0,5,"DD 0-5%"),(5,15,"DD 5-15%"),(15,101,"DD >15%")):
        sel = [i for i, v in enumerate(sd) if lo <= v < hi]
        if not sel: continue
        vals = "".join(f"{st.fmean([feats[k][i] for i in sel]):9.1f}" for k in keys)
        ixs = [ix[i] for i in sel if ix[i] is not None]
        print(f"  {lbl:12} {len(sel):5d}" + vals + f"  {st.fmean(ixs) if ixs else 0:6.1f}")

    print("\nPROBABILITY - P(Combo DD > 15% | state):")
    base = sum(1 for v in sd if v > 15)/len(sd)*100
    states = [
      ("b200 < 30%",        [i for i,v in enumerate(feats["pct_above_200sma"]) if v < 30]),
      ("new52wLows > 5%",   [i for i,v in enumerate(feats["pct_new_52w_lows"]) if v > 5]),
      ("medATR > 3.5%",     [i for i,v in enumerate(feats["median_atr_mkt_vol"]) if v > 3.5]),
      ("med1mRet < -5%",    [i for i,v in enumerate(feats["median_1m_return"]) if v < -5]),
      ("indexDD > 15%",     [i for i in hv if ix[i] > 15]),
    ]
    for lbl, sel in states:
        if len(sel) < 15:
            print(f"  {lbl:18} too few days ({len(sel)})"); continue
        p = sum(1 for i in sel if sd[i] > 15)/len(sel)*100
        print(f"  {lbl:18} {len(sel):5d} days -> {p:4.0f}%  (base {base:.0f}%)")

    print("\nWORST 4 COMBO DD EPISODES - market state at trough:")
    eps, cur = [], None
    for i in range(len(days)):
        if sd[i] > 10:
            if cur is None: cur = [i, i]
            elif sd[i] > sd[cur[1]]: cur[1] = i
        else:
            if cur: eps.append(cur); cur = None
    if cur: eps.append(cur)
    eps.sort(key=lambda e: -sd[e[1]])
    for start, tr in eps[:4]:
        d = days[tr]
        b2 = feats["pct_above_200sma"][tr]; nl = feats["pct_new_52w_lows"][tr]
        m1 = feats["median_1m_return"][tr]; ma = feats["median_atr_mkt_vol"][tr]
        print(f"  {d} DD {sd[tr]:.1f}% | b200 {b2:.0f}% | newlows {nl:.1f}% | med1m {m1:.1f}% | medATR {ma:.1f}% | idxDD {idd.get(d, 0):.1f}%")

asyncio.run(main())
