#!/usr/bin/env python3
"""Intraday strategy grid backtest on ohlcv_15min (NIFTY50, 15-min bars).

Grid: {ORB15, ORB30, PDHL} x {long, short, both} x {eod, 2R-target}.
Entry: bar CLOSE beyond level -> enter next bar OPEN. SL: range midpoint (ORB)
/ opposite level (PDHL), checked intra-bar. Hard flat at 15:15 bar open.
1 trade/symbol/day, max 3 concurrent, Rs20k notional per trade on Rs1L.
Costs: 0.05%/leg slippage, Dhan intraday charges, Rs20/order brokerage.
"""
import sys, datetime as dt
import numpy as np, pandas as pd, psycopg2

REPO = "/root/trade-execution-webhook"
CAPITAL = 100_000
PER_TRADE = 20_000
MAX_CONC = 3
SLIP = 0.0005

def db():
    pws = [l.split("=",1)[1].strip() for l in open(f"{REPO}/.env")
           if l.startswith("DB_PASSWORD")]
    for pw in pws:
        try:
            return psycopg2.connect(dbname="market_data",
                user="market_data_user", host="localhost", password=pw)
        except psycopg2.OperationalError:
            pass
    sys.exit("ABORT: DB auth failed")

def costs(buy_val, sell_val):
    brok = 40.0                                   # Rs20 x 2 orders
    stt = 0.00025 * sell_val
    exch = 0.0000297 * (buy_val + sell_val)
    sebi = 0.000001 * (buy_val + sell_val)
    stamp = 0.00003 * buy_val
    gst = 0.18 * (brok + exch + sebi)
    return brok + stt + exch + sebi + stamp + gst

def load():
    q = ("SELECT symbol, time, open, high, low, close, volume "
         "FROM ohlcv_15min ORDER BY symbol, time")
    df = pd.read_sql(q, db(), parse_dates=["time"])
    df["time"] = df["time"].dt.tz_convert("Asia/Kolkata")
    df["date"] = df["time"].dt.date
    df["hm"] = df["time"].dt.strftime("%H:%M")
    for c in ("open","high","low","close"):
        df[c] = df[c].astype(float)
    # drop non-regular bars (keep 09:15..15:15)
    df = df[(df.hm >= "09:15") & (df.hm <= "15:15")].reset_index(drop=True)
    return df

def day_frames(df):
    """yield (symbol, date, day_df with prior-day high/low)"""
    for sym, g in df.groupby("symbol", sort=False):
        days = dict(tuple(g.groupby("date", sort=True)))
        dates = sorted(days)
        for i in range(1, len(dates)):
            prev = days[dates[i-1]]
            d = days[dates[i]]
            if len(d) < 10:            # partial session, skip
                continue
            yield sym, dates[i], d.reset_index(drop=True), \
                  float(prev.high.max()), float(prev.low.min())

def gen_signals(strat, d, pdh, pdl):
    """return (dir, entry_idx, level, sl) or None; entry at next bar open."""
    out = []
    if strat in ("ORB15","ORB30"):
        nb = 1 if strat == "ORB15" else 2
        if len(d) < nb + 3: return out
        rh = float(d.high[:nb].max()); rl = float(d.low[:nb].min())
        mid = (rh + rl) / 2
        for i in range(nb, len(d) - 2):
            if d.close[i] > rh:
                out.append(("L", i+1, rh, mid)); break
            if d.close[i] < rl:
                out.append(("S", i+1, rl, mid)); break
    else:  # PDHL
        for i in range(0, len(d) - 2):
            if d.close[i] > pdh:
                out.append(("L", i+1, pdh, pdl if pdl < d.open[i+1] else d.open[i+1]*0.995)); break
            if d.close[i] < pdl:
                out.append(("S", i+1, pdl, pdh if pdh > d.open[i+1] else d.open[i+1]*1.005)); break
    return out

def sim_trade(d, sig, use_target):
    side, ei, level, sl = sig
    entry = float(d.open[ei]) * (1 + SLIP if side == "L" else 1 - SLIP)
    risk = abs(entry - sl)
    if risk <= 0 or risk / entry > 0.05:   # junk risk, skip
        return None
    tgt = entry + 2*risk if side == "L" else entry - 2*risk
    exit_px, reason = None, "EOD"
    for i in range(ei, len(d)):
        hm = d.hm[i]
        if hm >= "15:15":
            exit_px = float(d.open[i]); break
        lo, hi = float(d.low[i]), float(d.high[i])
        if side == "L":
            if lo <= sl: exit_px, reason = sl, "SL"; break
            if use_target and hi >= tgt: exit_px, reason = tgt, "TGT"; break
        else:
            if hi >= sl: exit_px, reason = sl, "SL"; break
            if use_target and lo <= tgt: exit_px, reason = tgt, "TGT"; break
    if exit_px is None:
        exit_px = float(d.close.iloc[-1])
    exit_px *= (1 - SLIP) if side == "L" else (1 + SLIP)
    qty = max(int(PER_TRADE // entry), 1)
    if side == "L":
        gross = (exit_px - entry) * qty
        c = costs(entry*qty, exit_px*qty)
    else:
        gross = (entry - exit_px) * qty
        c = costs(exit_px*qty, entry*qty)
    return {"side": side, "gross": gross, "net": gross - c, "cost": c,
            "reason": reason, "entry_time": d.time[ei]}

def run_config(frames, strat, direction, use_target):
    trades = []
    by_day = {}
    for sym, date, d, pdh, pdl in frames:
        for sig in gen_signals(strat, d, pdh, pdl):
            if direction == "long" and sig[0] != "L": continue
            if direction == "short" and sig[0] != "S": continue
            t = sim_trade(d, sig, use_target)
            if t:
                t["symbol"], t["date"] = sym, date
                by_day.setdefault(date, []).append(t)
    for date in sorted(by_day):
        day = sorted(by_day[date], key=lambda t: t["entry_time"])[:MAX_CONC]
        trades.extend(day)
    if not trades:
        return None
    tdf = pd.DataFrame(trades)
    tdf["month"] = pd.to_datetime(tdf["date"].astype(str)).dt.to_period("M")
    monthly = tdf.groupby("month")["net"].sum() / CAPITAL * 100
    eq = CAPITAL + tdf["net"].cumsum()
    dd = ((eq - eq.cummax()) / eq.cummax()).min() * 100
    yrs = (tdf["date"].max() - tdf["date"].min()).days / 365.25
    return {"strat": strat, "dir": direction, "tgt": use_target,
            "trades": len(tdf), "win%": (tdf.net > 0).mean()*100,
            "net_total": tdf.net.sum(),
            "avg_mo%": monthly.mean(), "med_mo%": monthly.median(),
            "pos_mo%": (monthly > 0).mean()*100,
            "maxDD%": dd, "cost_share%": tdf.cost.sum() /
                max(tdf.gross[tdf.gross>0].sum(), 1) * 100,
            "cagr%": ((eq.iloc[-1]/CAPITAL)**(1/max(yrs,0.1))-1)*100}

def main():
    df = load()
    print(f"bars={len(df)} symbols={df.symbol.nunique()} "
          f"{df.date.min()} -> {df.date.max()}", flush=True)
    frames = list(day_frames(df))
    print(f"symbol-days={len(frames)}", flush=True)
    res = []
    for strat in ("ORB15","ORB30","PDHL"):
        for direction in ("long","short","both"):
            for tgt in (False, True):
                r = run_config(frames, strat, direction, tgt)
                if r: res.append(r)
                print(f"done {strat}/{direction}/tgt={tgt}", flush=True)
    out = pd.DataFrame(res).sort_values("avg_mo%", ascending=False)
    pd.set_option("display.width", 200)
    print(out.round(2).to_string(index=False))
    out.to_csv("/root/intraday_grid_results.csv", index=False)

if __name__ == "__main__":
    main()
