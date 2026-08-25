#!/usr/bin/env python3
"""Daily short ATM straddle on real NSE option prices (fo_bhavcopy).

Per day+symbol: nearest expiry, ATM strike = closest to FUT open (nearest FUT),
both legs must have open>0. Sell CE+PE at open, buy back at close.
Gross pts = entry_premium - exit_premium. Costs: slippage 0.5% of premium each
way per leg, STT 0.1% on sold premium, exch 0.05% of premium both ways,
brokerage Rs80 flat per straddle (4 orders), GST 18% on brok+exch.
Lots (current): NIFTY 65, BANKNIFTY 30. Margin proxy: Rs1.5L (NIFTY) / Rs2L (BN).
"""
import sys, datetime as dt
import numpy as np, pandas as pd, psycopg2

REPO="/root/trade-execution-webhook"
LOT={"NIFTY":65,"BANKNIFTY":30}; MARGIN={"NIFTY":150000,"BANKNIFTY":200000}
def db():
    for pw in [l.split("=",1)[1].strip() for l in open(f"{REPO}/.env") if l.startswith("DB_PASSWORD")]:
        try: return psycopg2.connect(dbname="market_data",user="market_data_user",host="localhost",password=pw)
        except psycopg2.OperationalError: pass
    sys.exit("DB auth failed")

cn=db()
opt=pd.read_sql("SELECT trade_date,symbol,expiry,strike,opt_type,open,close FROM fo_bhavcopy WHERE instrument=%s AND open>0",cn,params=("OPTIDX",))
fut=pd.read_sql("SELECT trade_date,symbol,expiry,open FROM fo_bhavcopy WHERE instrument=%s AND open>0",cn,params=("FUTIDX",))
for c in ("open","close"): opt[c]=opt[c].astype(float)
fut["open"]=fut.open.astype(float); opt["strike"]=opt.strike.astype(float)
fut=fut.sort_values("expiry").groupby(["trade_date","symbol"]).first().rename(columns={"open":"und"})

rows=[]
for (d,sym),g in opt.groupby(["trade_date","symbol"]):
    try: und=fut.loc[(d,sym),"und"]
    except KeyError: continue
    exp=g.expiry.min()
    ge=g[g.expiry==exp]
    ce=ge[ge.opt_type=="CE"]; pe=ge[ge.opt_type=="PE"]
    ks=np.intersect1d(ce.strike.unique(),pe.strike.unique())
    if len(ks)==0: continue
    k=ks[np.abs(ks-und).argmin()]
    if abs(k-und)/und>0.02: continue      # no strike near ATM -> skip
    c0=ce[ce.strike==k].iloc[0]; p0=pe[pe.strike==k].iloc[0]
    ent=c0.open+p0.open; ext=c0.close+p0.close
    if ent<=0: continue
    gross=ent-ext
    slip=0.005*(ent+ext); stt=0.001*ent; exch=0.0005*(ent+ext)
    lot=LOT[sym]; brok_pts=80.0/lot
    gst=0.18*(brok_pts+exch)
    net=gross-slip-stt-exch-brok_pts-gst
    dte=(exp-d).days
    rows.append(dict(date=d,sym=sym,dte=dte,expiry_day=dte==0,ent=ent,
        gross=gross,net=net,gpct=gross/ent*100,npct=net/ent*100,
        rup=net*lot,mpct=net*lot/MARGIN[sym]*100))

t=pd.DataFrame(rows)
def rep(x,tag):
    if len(x)==0: print(tag,"n=0"); return
    for col,lab in (("gross","G"),("net","N")):
        w=x[x[col]>0]; l=x[x[col]<=0]
        pf=w[col].sum()/abs(l[col].sum()) if len(l) and l[col].sum()<0 else 99
        eq=x.sort_values("date")[col].cumsum(); dd=(eq-eq.cummax()).min()
        sh=x[col].mean()/x[col].std()*np.sqrt(252) if x[col].std()>0 else 0
        print(f"{tag} {lab}: n={len(x)} win%={len(w)/len(x)*100:.1f} PF={pf:.2f} "
              f"avg={x[col].mean():.2f}pts ({x[col.replace(chr(103),chr(110)) if col==chr(103)+chr(114)+chr(111)+chr(115)+chr(115) else (chr(110)+chr(112)+chr(99)+chr(116))].mean() if col==chr(110)+chr(101)+chr(116) else x.gpct.mean():.2f}%prem) "
              f"maxDD={dd:.0f}pts Sharpe={sh:.2f} avg_margin_ret={x.mpct.mean() if col==chr(110)+chr(101)+chr(116) else float(chr(48)):.3f}%")
for sym in ("NIFTY","BANKNIFTY"):
    x=t[t.sym==sym]
    rep(x,f"{sym} ALL      ")
    rep(x[x.expiry_day],f"{sym} EXPIRY   ")
    rep(x[~x.expiry_day],f"{sym} NONEXPIRY")
    print()
t.to_csv("/root/straddle_trades.csv",index=False)
print("rows:",len(t))
