#!/usr/bin/env python3
"""Intraday pairs stat-arb on ohlcv_15min NIFTY50.

Formation: rolling 60 trading days of 15m closes, re-formed every 21 days.
All 1225 pairs -> Engle-Granger coint p<0.05, hedge beta from OLS,
spread half-life 2..25 bars (must mean-revert intraday-fast). Top 10 by p.
Trading (next 21 days): z from FROZEN formation mean/std. Enter |z|>2
(z>2: short A long B; z<-2: long A short B). Exit |z|<0.25, |z|>4 stop,
or forced flat 15:15 daily (intraday constraint). Re-entry allowed.
Rs20k/leg. Gross AND net (4 orders: Rs80 brokerage, slip 0.05%/leg,
STT 0.025% on 2 sell legs, exch/stamp/GST).
"""
import sys, warnings
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, psycopg2
from statsmodels.tsa.stattools import coint

REPO="/root/trade-execution-webhook"
LEG=20000.0
def db():
    for pw in [l.split("=",1)[1].strip() for l in open(f"{REPO}/.env") if l.startswith("DB_PASSWORD")]:
        try: return psycopg2.connect(dbname="market_data",user="market_data_user",host="localhost",password=pw)
        except psycopg2.OperationalError: pass
    sys.exit("DB auth failed")

def half_life(s):
    ds=s.diff().dropna(); lag=s.shift().dropna().loc[ds.index]
    b=np.polyfit(lag,ds,1)[0]
    return -np.log(2)/b if b<0 else 1e9

def cost(buy,sell):
    brok=40.0; stt=0.00025*sell; exch=0.0000297*(buy+sell)
    sebi=0.000001*(buy+sell); stamp=0.00003*buy
    return brok+stt+exch+sebi+stamp+0.18*(brok+exch+sebi)

df=pd.read_sql("SELECT symbol,time,close FROM ohlcv_15min ORDER BY time",db(),parse_dates=["time"])
df["time"]=df.time.dt.tz_convert("Asia/Kolkata")
px=df.pivot_table(index="time",columns="symbol",values="close").astype(float)
px=px.between_time("09:15","15:15")
dates=sorted(set(px.index.date)); D=len(dates)
day_of=np.array([dates.index(d) for d in px.index.date])
hm=px.index.strftime("%H:%M")
print(f"bars={len(px)} days={D} syms={px.shape[1]}",flush=True)

syms=list(px.columns); trades=[]
for f0 in range(60,D-1,21):
    form=px[(day_of>=f0-60)&(day_of<f0)].dropna(axis=1)
    trad=px[(day_of>=f0)&(day_of<min(f0+21,D))]
    thm=hm[(day_of>=f0)&(day_of<min(f0+21,D))]
    cands=[]
    cols=list(form.columns)
    for i in range(len(cols)):
        a=form[cols[i]].values
        for j in range(i+1,len(cols)):
            b=form[cols[j]].values
            try: p=coint(a,b,trend="c",maxlag=1,autolag=None)[1]
            except Exception: continue
            if p<0.05:
                beta=np.polyfit(b,a,1)[0]
                sp=pd.Series(a-beta*b)
                hl=half_life(sp)
                if 2<=hl<=25:
                    cands.append((p,cols[i],cols[j],beta,sp.mean(),sp.std()))
    cands.sort()
    for p,A,B,beta,mu,sd in cands[:10]:
        if sd<=0: continue
        pa=trad[A].values; pb=trad[B].values
        z=(pa-beta*pb-mu)/sd
        pos=0; ea=eb=0.0
        for k in range(len(z)):
            if np.isnan(z[k]): continue
            flat_now = thm[k]>="15:15"
            if pos==0:
                if not flat_now and abs(z[k])>2 and thm[k]<"15:00":
                    pos=-1 if z[k]>2 else 1     # -1: short A long B
                    ea,eb=pa[k],pb[k]
            else:
                stop=abs(z[k])>4
                done=abs(z[k])<0.25
                if flat_now or stop or done or k==len(z)-1:
                    qa=LEG/ea; qb=LEG/eb
                    if pos==1:  gross=qa*(pa[k]-ea)-qb*(pb[k]-eb)
                    else:       gross=-qa*(pa[k]-ea)+qb*(pb[k]-eb)
                    slip=0.0005*(qa*(ea+pa[k])+qb*(eb+pb[k]))
                    c=cost(LEG+ (qa*pa[k] if pos==-1 else qb*pb[k]),
                           LEG+ (qa*pa[k] if pos==1  else qb*pb[k]))+slip
                    trades.append(dict(A=A,B=B,gross=gross,net=gross-c,
                        why=("EOD" if flat_now else "STOP" if stop else "Z0"),
                        gp=gross/(2*LEG)*100,np_=(gross-c)/(2*LEG)*100))
                    pos=0
    print(f"formation day {f0}: pairs={len(cands)}",flush=True)

t=pd.DataFrame(trades)
if t.empty: print("NO TRADES"); sys.exit()
for col,tag in (("gp","GROSS"),("np_","NET  ")):
    w=t[t[col]>0]; l=t[t[col]<=0]
    pf=w[col].sum()/abs(l[col].sum()) if len(l) else float("inf")
    print(f"{tag}: n={len(t)} win%={len(w)/len(t)*100:.1f} PF={pf:.2f} "
          f"avg/trade={t[col].mean():.3f}% total={t[col].sum():.0f}%-units")
print(t.why.value_counts().to_dict())
t.to_csv("/root/pairs_trades.csv",index=False)
