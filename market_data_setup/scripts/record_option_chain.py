#!/usr/bin/env python3
"""15-min option-chain snapshot recorder (NIFTY + BANKNIFTY) via Dhan v2.

Cron: 0,15,30,45 9-15 * * 1-5. In-script gate: 09:15-15:30 IST weekdays only
(bypass with --force). Per index: 2 nearest expiries, strikes within +-5% of
spot with any quote. Table option_chain_snap. Dhan optionchain rate limit:
1 req / 3 s -> sleep between calls. Aborts loudly on token/DB failure.
"""
import sys, os, time, datetime as dt
import requests, psycopg2
from psycopg2.extras import execute_values

REPO="/root/trade-execution-webhook"
sys.path.insert(0,REPO)
IDX={"NIFTY":13,"BANKNIFTY":25}
N_EXP=2; BAND=0.05

def env():
    for l in open(f"{REPO}/.env"):
        l=l.strip()
        if l and "=" in l and not l.startswith("#"):
            k,v=l.split("=",1); os.environ.setdefault(k,v)

def db():
    for pw in [l.split("=",1)[1].strip() for l in open(f"{REPO}/.env") if l.startswith("DB_PASSWORD")]:
        try: return psycopg2.connect(dbname="market_data",user="market_data_user",host="localhost",password=pw)
        except psycopg2.OperationalError: pass
    sys.exit("ABORT: DB auth failed")

def main():
    now=dt.datetime.now()
    if "--force" not in sys.argv:
        if now.weekday()>4: return
        hm=now.strftime("%H:%M")
        if hm<"09:15" or hm>"15:30": return
    env()
    from web_api.dhan_client import get_token
    H={"access-token":get_token(),"client-id":os.environ["DHAN_CLIENT_ID"],"Content-Type":"application/json"}
    snap=now.replace(second=0,microsecond=0)
    conn=db(); conn.autocommit=True; cur=conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS option_chain_snap(
        snap_time timestamptz, symbol text, expiry date, strike numeric,
        opt_type text, ltp numeric, bid numeric, ask numeric, iv numeric,
        oi bigint, volume bigint, delta numeric, spot numeric,
        PRIMARY KEY(snap_time,symbol,expiry,strike,opt_type))""")
    total=0
    for sym,scrip in IDX.items():
        r=requests.post("https://api.dhan.co/v2/optionchain/expirylist",headers=H,
            json={"UnderlyingScrip":scrip,"UnderlyingSeg":"IDX_I"},timeout=20)
        if r.status_code!=200: sys.exit(f"ABORT expirylist {sym}: {r.status_code} {r.text[:120]}")
        exps=r.json()["data"][:N_EXP]
        time.sleep(3.2)
        for exp in exps:
            r=requests.post("https://api.dhan.co/v2/optionchain",headers=H,
                json={"UnderlyingScrip":scrip,"UnderlyingSeg":"IDX_I","Expiry":exp},timeout=25)
            if r.status_code!=200:
                print(f"WARN chain {sym} {exp}: {r.status_code}",flush=True); time.sleep(3.2); continue
            d=r.json().get("data",{})
            spot=float(d.get("last_price") or 0)
            rows=[]
            for ks,legs in (d.get("oc") or {}).items():
                k=float(ks)
                if spot<=0 or abs(k-spot)/spot>BAND: continue
                for ot in ("ce","pe"):
                    L=legs.get(ot) or {}
                    ltp=float(L.get("last_price") or 0)
                    bid=float(L.get("top_bid_price") or 0); ask=float(L.get("top_ask_price") or 0)
                    if ltp<=0 and bid<=0 and ask<=0: continue
                    rows.append((snap,sym,exp,k,ot.upper(),ltp,bid,ask,
                        float(L.get("implied_volatility") or 0),
                        int(L.get("oi") or 0),int(L.get("volume") or 0),
                        float((L.get("greeks") or {}).get("delta") or 0),spot))
            if rows:
                execute_values(cur,"INSERT INTO option_chain_snap VALUES %s ON CONFLICT DO NOTHING",rows)
                total+=len(rows)
            time.sleep(3.2)
    print(f"[{snap}] rows={total}",flush=True)

if __name__=="__main__": main()
