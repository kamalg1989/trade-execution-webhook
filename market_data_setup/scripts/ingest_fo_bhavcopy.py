#!/usr/bin/env python3
"""Ingest NSE F&O bhavcopy (NIFTY+BANKNIFTY OPTIDX/FUTIDX) into fo_bhavcopy.

Old format (<2024-07-08): /content/historical/DERIVATIVES/YYYY/MMM/foDDMMMYYYYbhav.csv.zip
UDiFF (>=2024-07-08):     /content/fo/BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip
Falls back to the other format on 404; both 404 = holiday, skipped.
Traded rows only (contracts/vol > 0). Resumable: skips dates already in DB.
"""
import io, sys, time, zipfile, csv, datetime as dt
import requests, psycopg2
from psycopg2.extras import execute_values

REPO="/root/trade-execution-webhook"
START=dt.date(2019,1,1); CUTOVER=dt.date(2024,7,8)
H={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36","Accept":"*/*","Referer":"https://www.nseindia.com/"}
MON=["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]

def db():
    for pw in [l.split("=",1)[1].strip() for l in open(f"{REPO}/.env") if l.startswith("DB_PASSWORD")]:
        try: return psycopg2.connect(dbname="market_data",user="market_data_user",host="localhost",password=pw)
        except psycopg2.OperationalError: pass
    sys.exit("DB auth failed")

def url_old(d): return (f"https://nsearchives.nseindia.com/content/historical/DERIVATIVES/"
    f"{d.year}/{MON[d.month-1]}/fo{d.day:02d}{MON[d.month-1]}{d.year}bhav.csv.zip")
def url_new(d): return (f"https://nsearchives.nseindia.com/content/fo/"
    f"BhavCopy_NSE_FO_0_0_0_{d.strftime(chr(37)+chr(89)+chr(37)+chr(109)+chr(37)+chr(100))}_F_0000.csv.zip")

def get(s,u):
    for a in range(3):
        try:
            r=s.get(u,headers=H,timeout=40)
            if r.status_code==404: return None
            if r.status_code==200: return r.content
        except requests.RequestException: pass
        time.sleep(5*(a+1))
    sys.exit(f"ABORT: repeated failure {u}")

def parse_old(raw,d):
    z=zipfile.ZipFile(io.BytesIO(raw)); f=io.TextIOWrapper(z.open(z.namelist()[0]))
    rows=[]
    for r in csv.DictReader(f):
        if r["SYMBOL"] not in ("NIFTY","BANKNIFTY"): continue
        if r["INSTRUMENT"] not in ("OPTIDX","FUTIDX"): continue
        if int(float(r["CONTRACTS"] or 0))<=0: continue
        exp=dt.datetime.strptime(r["EXPIRY_DT"],"%d-%b-%Y").date()
        ot=r["OPTION_TYP"].strip() or "XX"
        rows.append((d,r["SYMBOL"],r["INSTRUMENT"],exp,float(r["STRIKE_PR"]),ot,
            float(r["OPEN"]),float(r["HIGH"]),float(r["LOW"]),float(r["CLOSE"]),
            float(r["SETTLE_PR"]),int(float(r["CONTRACTS"])),int(float(r["OPEN_INT"])),None))
    return rows

def parse_new(raw,d):
    z=zipfile.ZipFile(io.BytesIO(raw)); f=io.TextIOWrapper(z.open(z.namelist()[0]))
    rows=[]
    for r in csv.DictReader(f):
        if r["TckrSymb"] not in ("NIFTY","BANKNIFTY"): continue
        if r["FinInstrmTp"] not in ("IDO","IDF"): continue
        vol=int(float(r["TtlTradgVol"] or 0))
        if vol<=0: continue
        inst="OPTIDX" if r["FinInstrmTp"]=="IDO" else "FUTIDX"
        ot=(r["OptnTp"] or "XX").strip() or "XX"
        strike=float(r["StrkPric"] or 0)
        und=float(r["UndrlygPric"]) if r["UndrlygPric"] else None
        rows.append((d,r["TckrSymb"],inst,
            dt.datetime.strptime(r["XpryDt"],"%Y-%m-%d").date(),strike,ot,
            float(r["OpnPric"]),float(r["HghPric"]),float(r["LwPric"]),float(r["ClsPric"]),
            float(r["SttlmPric"]),vol,int(float(r["OpnIntrst"] or 0)),und))
    return rows

def main():
    conn=db(); conn.autocommit=True; cur=conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS fo_bhavcopy(
        trade_date date, symbol text, instrument text, expiry date,
        strike numeric, opt_type text, open numeric, high numeric, low numeric,
        close numeric, settle numeric, contracts bigint, oi bigint, underlying numeric,
        PRIMARY KEY(trade_date,symbol,instrument,expiry,strike,opt_type))""")
    cur.execute("SELECT DISTINCT trade_date FROM fo_bhavcopy")
    have={r[0] for r in cur.fetchall()}
    s=requests.Session()
    s.get("https://www.nseindia.com",headers=H,timeout=15)
    d=START; today=dt.date.today(); n_days=0
    while d<=today:
        if d.weekday()<5 and d not in have:
            first,second=(url_old,url_new) if d<CUTOVER else (url_new,url_old)
            raw=get(s,first(d)); fmt=first
            if raw is None:
                raw=get(s,second(d)); fmt=second
            if raw is not None:
                rows=parse_old(raw,d) if fmt is url_old else parse_new(raw,d)
                if rows:
                    execute_values(cur,"INSERT INTO fo_bhavcopy VALUES %s ON CONFLICT DO NOTHING",rows)
                n_days+=1
                if n_days%50==0: print(f"[{dt.datetime.now():%H:%M}] {d} days={n_days}",flush=True)
            time.sleep(1.0)
        d+=dt.timedelta(days=1)
    cur.execute("SELECT count(*),count(DISTINCT trade_date),min(trade_date),max(trade_date) FROM fo_bhavcopy")
    print("DONE:",cur.fetchone(),flush=True)

if __name__=="__main__": main()
