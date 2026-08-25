#!/usr/bin/env python3
"""Pre-deployment checks: SL-fill slippage stress, yearly audit, sizing.

Trigger stays high>=1.30x entry; STRESS varies the FILL (1.30/1.35/1.40/1.45).
Yearly audit at base fill 1.30. Sizing: margin 1.35L/pair, buffer sized off
stressed worst day. Expiry-day segment only (the edge)."""
import sys
import numpy as np, pandas as pd, psycopg2

REPO="/root/trade-execution-webhook"
LOT={"NIFTY":65,"BANKNIFTY":30}; MARGIN={"NIFTY":150000,"BANKNIFTY":200000}
TRIG=0.30
def db():
    for pw in [l.split("=",1)[1].strip() for l in open(f"{REPO}/.env") if l.startswith("DB_PASSWORD")]:
        try: return psycopg2.connect(dbname="market_data",user="market_data_user",host="localhost",password=pw)
        except psycopg2.OperationalError: pass
    sys.exit("DB auth failed")

cn=db()
opt=pd.read_sql("SELECT trade_date,symbol,expiry,strike,opt_type,open,high,close FROM fo_bhavcopy WHERE instrument=%s AND open>0",cn,params=("OPTIDX",))
fut=pd.read_sql("SELECT trade_date,symbol,expiry,open FROM fo_bhavcopy WHERE instrument=%s AND open>0",cn,params=("FUTIDX",))
for c in ("open","high","close"): opt[c]=opt[c].astype(float)
fut["open"]=fut.open.astype(float); opt["strike"]=opt.strike.astype(float)
fut=fut.sort_values("expiry").groupby(["trade_date","symbol"]).first().rename(columns={"open":"und"})
pairs=[]
for (d,sym),g in opt.groupby(["trade_date","symbol"]):
    try: und=fut.loc[(d,sym),"und"]
    except KeyError: continue
    exp=g.expiry.min(); ge=g[g.expiry==exp]
    ce=ge[ge.opt_type=="CE"]; pe=ge[ge.opt_type=="PE"]
    ks=np.intersect1d(ce.strike.unique(),pe.strike.unique())
    if len(ks)==0: continue
    k=ks[np.abs(ks-und).argmin()]
    if abs(k-und)/und>0.02: continue
    c0=ce[ce.strike==k].iloc[0]; p0=pe[pe.strike==k].iloc[0]
    if (exp-d).days==0:
        pairs.append((d,sym,c0.open,c0.high,c0.close,p0.open,p0.high,p0.close))
P=pd.DataFrame(pairs,columns=["date","sym","co","ch","cc","po","ph","pc"])
P["date"]=pd.to_datetime(P.date)
print(f"expiry straddle-days={len(P)}",flush=True)

def run(fill_mult):
    out=[]
    for r in P.itertuples():
        ce_x=r.co*fill_mult if r.ch>=r.co*(1+TRIG) else r.cc
        pe_x=r.po*fill_mult if r.ph>=r.po*(1+TRIG) else r.pc
        ent=r.co+r.po; ext=ce_x+pe_x; gross=ent-ext
        slip=0.005*(ent+ext); stt=0.001*ent; exch=0.0005*(ent+ext)
        lot=LOT[r.sym]; brok=80.0/lot; gst=0.18*(brok+exch)
        net=gross-slip-stt-exch-brok-gst
        out.append(dict(date=r.date,sym=r.sym,net=net,rup=net*lot,
            mpct=net*lot/MARGIN[r.sym]*100))
    return pd.DataFrame(out)

print(); print("=== 1. SL-FILL SLIPPAGE STRESS (trigger 30%, fill varies) ===")
print("fill  sym        n   win%    PF  avg%mgn  worst%  totRs/lot")
for fm in (1.30,1.35,1.40,1.45):
    t=run(fm)
    for sym in ("NIFTY","BANKNIFTY"):
        x=t[t.sym==sym]; w=x[x.net>0]; l=x[x.net<=0]
        pf=w.net.sum()/abs(l.net.sum())
        print(f"{fm:.2f}  {sym:<9}{len(x):>4} {len(w)/len(x)*100:>6.1f} {pf:>5.2f} {x.mpct.mean():>8.3f} {x.mpct.min():>7.2f} {x.rup.sum():>10.0f}",flush=True)

print(); print("=== 2. YEARLY AUDIT (fill 1.30) ===")
t=run(1.30); t["yr"]=t.date.dt.year
print("yr    sym        n  win%    PF  maxDD%  netRs/lot  avg%mgn")
for sym in ("NIFTY","BANKNIFTY"):
    for yr,x in t[t.sym==sym].groupby("yr"):
        w=x[x.net>0]; l=x[x.net<=0]
        pf=w.net.sum()/abs(l.net.sum()) if len(l) and l.net.sum()<0 else 99
        eq=x.sort_values("date").mpct.cumsum(); dd=(eq-eq.cummax()).min()
        print(f"{yr}  {sym:<9}{len(x):>3} {len(w)/len(x)*100:>5.1f} {pf:>5.2f} {dd:>7.2f} {x.rup.sum():>10.0f} {x.mpct.mean():>8.3f}",flush=True)

print(); print("=== 3. SIZING (margin 1.35L/pair, stressed-fill worst day) ===")
ws=run(1.45); worst=abs(ws.mpct.min())/100
print(f"stressed worst single day = {ws.mpct.min():.2f}% of margin")
def size(cap,margin=135000,worst_frac=worst,cushion=2.0):
    # reserve cushion x stressed-worst-day on deployed margin + 10% MTM buffer
    lots=0
    while lots+1:
        need=(lots+1)*margin*(1+cushion*worst_frac+0.10)
        if need>cap: break
        lots+=1
    return lots, cap-lots*margin
for cap in (100000,500000,1500000,2500000):
    lots,free=size(cap)
    print(f"capital Rs{cap:>9,}: max_lots={lots}  reserve=Rs{free:>9,.0f}  " 
          +("CANNOT TRADE - below 1-lot margin+buffer" if lots==0 else f"deployed=Rs{135000*lots:,}"),flush=True)
