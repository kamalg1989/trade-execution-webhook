#!/usr/bin/env python3
"""80% Value-Area re-entry rule on ohlcv_15min. GROSS metrics first.

Filter days: gap>1% vs prev close OR first-bar RVOL>2 (vs 20d avg first-bar vol).
Yesterday VA: volume-at-price (100 bins, bar volume spread across bar range),
POC=max bin, VA=expand around POC to 70% volume.
Signal (09:15-11:00 only): open outside VA, then 2 consecutive 15m closes
inside VA -> enter next bar open. Long if opened below VAL (target VAH),
short if opened above VAH (target VAL). SL just outside entered VA edge
(0.25% beyond). Skip if (target-entry)/(entry-SL) < 2. Exit: TGT/SL/15:15.
"""
import sys, datetime as dt
import numpy as np, pandas as pd, psycopg2

REPO="/root/trade-execution-webhook"
def db():
    for pw in [l.split("=",1)[1].strip() for l in open(f"{REPO}/.env") if l.startswith("DB_PASSWORD")]:
        try: return psycopg2.connect(dbname="market_data",user="market_data_user",host="localhost",password=pw)
        except psycopg2.OperationalError: pass
    sys.exit("DB auth failed")

def value_area(d):
    lo, hi = d.low.min(), d.high.max()
    if hi <= lo: return None
    edges = np.linspace(lo, hi, 101)
    vol = np.zeros(100)
    for _,b in d.iterrows():
        i0 = np.searchsorted(edges, b.low, "right")-1
        i1 = np.searchsorted(edges, b.high, "left")
        i0=max(i0,0); i1=min(max(i1,i0+1),100)
        vol[i0:i1] += b.volume/(i1-i0)
    poc = int(vol.argmax()); tot = vol.sum()
    if tot<=0: return None
    l=r=poc; acc=vol[poc]
    while acc < 0.70*tot:
        lv = vol[l-1] if l>0 else -1
        rv = vol[r+1] if r<99 else -1
        if rv>=lv and r<99: r+=1; acc+=vol[r]
        elif l>0: l-=1; acc+=vol[l]
        else: break
    return edges[l], edges[r+1]   # VAL, VAH

df = pd.read_sql("SELECT symbol,time,open,high,low,close,volume FROM ohlcv_15min ORDER BY symbol,time", db(), parse_dates=["time"])
df["time"]=df.time.dt.tz_convert("Asia/Kolkata")
df["date"]=df.time.dt.date; df["hm"]=df.time.dt.strftime("%H:%M")
df=df[(df.hm>="09:15")&(df.hm<="15:15")]
for c in ("open","high","low","close"): df[c]=df[c].astype(float)

trades=[]
for sym,g in df.groupby("symbol",sort=False):
    days=dict(tuple(g.groupby("date",sort=True))); dates=sorted(days)
    fbv={dte:days[dte].iloc[0].volume for dte in dates}   # first-bar volume
    for i in range(21,len(dates)):
        prev,d0=days[dates[i-1]],days[dates[i]].reset_index(drop=True)
        if len(d0)<10 or len(prev)<10: continue
        va=value_area(prev)
        if not va: continue
        val,vah=va
        o=d0.open[0]; pc=prev.close.iloc[-1]
        gap=abs(o/pc-1)*100
        rv=fbv[dates[i]]/max(np.mean([fbv[dates[j]] for j in range(i-20,i)]),1)
        if gap<1.0 and rv<2.0: continue                    # day filter
        if val<=o<=vah: continue                           # must open OUTSIDE VA
        side="L" if o<val else "S"
        # find 2 consecutive closes inside VA, bars 0.. (entry by 11:00)
        ei=None
        for k in range(1,len(d0)-1):
            if d0.hm[k]>"11:00": break
            if val<=d0.close[k]<=vah and val<=d0.close[k-1]<=vah:
                ei=k+1; break
        if ei is None or ei>=len(d0): continue
        e=d0.open[ei]
        if side=="L": sl=val*0.9975; tgt=vah
        else: sl=vah*1.0025; tgt=val
        risk=abs(e-sl); rew=abs(tgt-e)
        if risk<=0 or rew/risk<2.0: continue               # 1:2 R:R gate
        xp=None; why="EOD"
        for k in range(ei,len(d0)):
            if d0.hm[k]>="15:15": xp=d0.open[k]; break
            lo,hi=d0.low[k],d0.high[k]
            if side=="L":
                if lo<=sl: xp,why=sl,"SL"; break
                if hi>=tgt: xp,why=tgt,"TGT"; break
            else:
                if hi>=sl: xp,why=sl,"SL"; break
                if lo<=tgt: xp,why=tgt,"TGT"; break
        if xp is None: xp=d0.close.iloc[-1]
        pnl=(xp-e) if side=="L" else (e-xp)
        trades.append(dict(sym=sym,date=dates[i],side=side,e=e,x=xp,why=why,
                           r=pnl/risk,pct=pnl/e*100))

t=pd.DataFrame(trades)
if t.empty: print("NO TRADES"); sys.exit()
def rep(x,tag):
    w=x[x.pct>0]; l=x[x.pct<=0]
    pf=w.pct.sum()/abs(l.pct.sum()) if len(l) and l.pct.sum()<0 else float("inf")
    print(f"{tag}: n={len(x)} win%={len(w)/len(x)*100:.1f} avgW={w.pct.mean():.2f}% "
          f"avgL={l.pct.mean():.2f}% PF={pf:.2f} avgR={x.r.mean():.3f} "
          f"exp/trade={x.pct.mean():.3f}%")
rep(t,"ALL   ")
rep(t[t.side=="L"],"LONG  ")
rep(t[t.side=="S"],"SHORT ")
print(t.why.value_counts().to_dict())
t.to_csv("/root/va80_trades.csv",index=False)
