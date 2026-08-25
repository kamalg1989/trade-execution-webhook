#!/usr/bin/env python3
"""Short ATM straddle with 30% leg-SL, bounded via bhavcopy HIGH.

Same entry/costs as straddle_backtest.py. Leg-SL rule: if a sold leg
day-HIGH >= (1+SL)*entry, that leg is bought back at (1+SL)*entry (+slip);
firing is deterministic (high crossed), only fill quality is assumed.
Surviving leg held to close. Grid over SL in {0.25,0.30,0.40,0.50,None}.
"""
import sys
import numpy as np, pandas as pd, psycopg2

REPO="/root/trade-execution-webhook"
LOT={"NIFTY":65,"BANKNIFTY":30}; MARGIN={"NIFTY":150000,"BANKNIFTY":200000}
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
    pairs.append((d,sym,(exp-d).days==0,c0.open,c0.high,c0.close,p0.open,p0.high,p0.close))
P=pd.DataFrame(pairs,columns=["date","sym","expd","co","ch","cc","po","ph","pc"])
print(f"straddle-days={len(P)}",flush=True)

def leg_exit(o,h,c,sl):
    """returns (exit_px, stopped) for one sold leg"""
    if sl is not None and h>=o*(1+sl):
        return o*(1+sl),True
    return c,False

def run(sl):
    out=[]
    for r in P.itertuples():
        ce_x,ce_s=leg_exit(r.co,r.ch,r.cc,sl)
        pe_x,pe_s=leg_exit(r.po,r.ph,r.pc,sl)
        ent=r.co+r.po; ext=ce_x+pe_x
        gross=ent-ext
        slip=0.005*(ent+ext); stt=0.001*ent; exch=0.0005*(ent+ext)
        lot=LOT[r.sym]; brok=80.0/lot; gst=0.18*(brok+exch)
        net=gross-slip-stt-exch-brok-gst
        out.append(dict(date=r.date,sym=r.sym,expd=r.expd,net=net,
            mpct=net*lot/MARGIN[r.sym]*100,stopped=ce_s or pe_s,both=ce_s and pe_s))
    return pd.DataFrame(out)

print("   SL seg                      n   win%     PF  avg%mgn   worst%  stop%")
for sl in (0.25,0.30,0.40,0.50,None):
    t=run(sl)
    for sym in ("NIFTY","BANKNIFTY"):
        for lab,seg in (("EXPIRY",t[(t.sym==sym)&t.expd]),("NONEXP",t[(t.sym==sym)&~t.expd])):
            w=seg[seg.net>0]; l=seg[seg.net<=0]
            pf=w.net.sum()/abs(l.net.sum()) if len(l) and l.net.sum()<0 else 99
            print(f"{str(sl):>5} {sym+chr(32)+lab:<20} {len(seg):>5} {len(w)/len(seg)*100:>6.1f} {pf:>6.2f} "
                  f"{seg.mpct.mean():>8.3f} {seg.mpct.min():>8.2f} {seg.stopped.mean()*100:>6.1f}",flush=True)
