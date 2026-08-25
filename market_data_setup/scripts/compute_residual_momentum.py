#!/usr/bin/env python3
"""Residual momentum (Hanauer & Windmueller) feature table.

Per symbol, rolling 252d single-factor OLS vs NIFTYBEES daily returns:
  beta_t = rolling_cov(ri,rm)/rolling_var(rm); alpha_t = mean(ri)-beta_t*mean(rm)
  resid_t = ri_t - alpha_t - beta_t*rm_t          (point-in-time params)
Scores (H&W standardization): resmom_L = sum(resid over [t-L, t-22]) /
  (std(resid same span)) for L in {252,126} (i.e. 12-1 and 6-1 months).
rf omitted: a constant shifts alpha only, residuals unchanged.
Chunked 400 symbols/pass for the 2GB VPS. Table: stock_residual_momentum.
Rerun-safe: full-refresh per chunk via delete+insert on chunk symbols.
"""
import sys
import numpy as np, pandas as pd, psycopg2
from psycopg2.extras import execute_values

REPO="/root/trade-execution-webhook"
W=252; SKIP=21; CHUNK=400
def db():
    for pw in [l.split("=",1)[1].strip() for l in open(f"{REPO}/.env") if l.startswith("DB_PASSWORD")]:
        try: return psycopg2.connect(dbname="market_data",user="market_data_user",host="localhost",password=pw)
        except psycopg2.OperationalError: pass
    sys.exit("DB auth failed")

cn=db(); cn.autocommit=True; cur=cn.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS stock_residual_momentum(
    symbol text, date date, resmom_252 real, resmom_126 real, beta_252 real,
    PRIMARY KEY(symbol,date))""")
mkt=pd.read_sql("SELECT d,level FROM index_proxy_daily WHERE proxy=%s ORDER BY d",cn,params=("NIFTYBEES",))
mkt["rm"]=mkt.level.pct_change()
mkt=mkt.set_index("d").rm.dropna()
cur.execute("SELECT DISTINCT symbol FROM ohlcv_data ORDER BY symbol")
syms=[r[0] for r in cur.fetchall()]
print(f"symbols={len(syms)} mkt_days={len(mkt)}",flush=True)

def score(res,L):
    s=res.rolling(L-SKIP).sum().shift(SKIP)
    sd=res.rolling(L-SKIP).std().shift(SKIP)
    return s/sd

for c0 in range(0,len(syms),CHUNK):
    chunk=syms[c0:c0+CHUNK]
    q="SELECT symbol,time::date AS d,close FROM ohlcv_data WHERE symbol = ANY(%s)"
    px=pd.read_sql(q,cn,params=(chunk,))
    wide=px.pivot_table(index="d",columns="symbol",values="close").astype("float64")
    wide=wide.reindex(mkt.index)
    ri=wide.pct_change()
    rm=mkt.reindex(ri.index)
    mu_m=rm.rolling(W).mean(); var_m=rm.rolling(W).var()
    cov=ri.rolling(W).cov(rm)
    beta=cov.div(var_m,axis=0)
    alpha=ri.rolling(W).mean().sub(beta.mul(mu_m,axis=0))
    resid=ri.sub(alpha).sub(beta.mul(rm,axis=0))
    r252=resid.apply(lambda col: score(col,252))
    r126=resid.apply(lambda col: score(col,126))
    rows=[]
    for sym in wide.columns:
        m=pd.DataFrame({"a":r252[sym],"b":r126[sym],"c":beta[sym]}).dropna(subset=["a","b"])
        for d,rr in m.iterrows():
            rows.append((sym,d,float(rr.a),float(rr.b),float(rr.c) if np.isfinite(rr.c) else None))
    cur.execute("DELETE FROM stock_residual_momentum WHERE symbol = ANY(%s)",(chunk,))
    for i in range(0,len(rows),50000):
        execute_values(cur,"INSERT INTO stock_residual_momentum VALUES %s",rows[i:i+50000])
    print(f"chunk {c0//CHUNK+1}: syms={len(wide.columns)} rows={len(rows)}",flush=True)

cur.execute("SELECT count(*),count(DISTINCT symbol),min(date),max(date) FROM stock_residual_momentum")
print("DONE:",cur.fetchone(),flush=True)
