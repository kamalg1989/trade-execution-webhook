"""OHM /ohm/ nightly compute -> /root/ohm_web/data/ohm.json

Follows the course doc claude/2026-11-23-OHM-course-setup-as-taught.md and
the entry PDF "OHM - Entry Techniques".

Inputs (read only, nothing BAU is modified):
  /root/ohm_screener_web/data/latest.json  per-stock metrics, regime, breadth, IFP, entry bar
  market_data.ohlcv_data                    prices for MAs / EMAs / weekly move / listing age
  market_data.ohm_fundamentals              Coffee-can fields (fundamentals.py)
Funnel:
  tradeable  = NSE, 20-day turnover >= Rs 1 Cr
  routes     = Fundamentals (all Coffee-can rules) | Movers (>=20% in 5 sessions, above 200-day)
               | New listing (first bar within 2 years) | Sector (>=8 stocks and >=60% of the sector pass IFP)
  IFP check  = screen_gpt IFP >= 0.25 AND up/down-day volume ratio (50d) >= 1.2 AND 60-day fund flow > 0
  watchlist  = any route AND IFP check
  focus      = watchlist AND entry bar today AND close > 50-day AND near 52w high (>=75%)
               AND tight 10-day range (<=15%) AND within 8% of the 21 EMA
Entry / stop (PDF): HH-HL buy above higher of 2 bars, stop lower of 2 bars;
  inside / pin / trend bar buy above that bar's high, stop that bar's low. Buy = +1 tick.
"""
import json, math, os, sys, time, datetime as dt
import numpy as np, pandas as pd, psycopg2

sys.path.insert(0, '/root/trade-execution-webhook')
import tick_utils  # noqa: E402

SRC = '/root/ohm_screener_web/data/latest.json'
OUT = '/root/ohm_web/data/ohm.json'
DB = dict(host='localhost', dbname='market_data', user='postgres', password='postgres')
RISK_PCT, CAP_PCT, IFP_MIN = 0.25, 10.0, 0.25
UD_MIN = 1.2            # up-day avg volume / down-day avg volume, last 50 sessions
SEC_MIN_N, SEC_MIN_SHARE = 8, 0.6
SAFETY_PCT = 8.0   # same as BAU sl_engine safety SL (0.92)
COFFEE = [  # (field, min, label) — course V5 frame "Coffee can stocks with a twist"
    ('sales_g', 10, 'Sales growth > 10%'), ('sales_g3', 10, 'Sales growth 3Y > 10%'),
    ('profit_g', 10, 'Profit growth > 10%'), ('profit_g3', 10, 'Profit growth 3Y > 10%'),
    ('roce', 15, 'ROCE > 15%'), ('roe', 15, 'ROE > 15%'),
    ('qprofit_yoy', 20, 'Quarterly profit growth YoY > 20%'),
    ('roce3y', 15, 'Average ROCE 3Y > 15%'), ('mcap', 500, 'Market cap > ₹500 Cr')]
ENT_NAME = {'HH-HL': 'Higher high, higher low', 'Inside bar': 'Inside bar', 'Pin bar': 'Pin bar', 'Trend bar': 'Trend bar'}


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def capital():
    try:
        for ln in open('/root/trade-execution-webhook/.env'):
            if ln.startswith('CAPITAL='):
                return float(ln.split('=', 1)[1].strip().strip('"\''))
    except Exception:
        pass
    return 400000.0


def f(x, n=2):
    if x is None: return None
    try:
        x = float(x)
    except Exception:
        return None
    return None if not math.isfinite(x) else round(x, n)


def tick_of(s):
    import contextlib, io
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return float(tick_utils.get_tick_size(s) or 0.05)
    except Exception:
        return 0.05


def up_tick(p, t):
    return round(math.floor(p / t + 1e-9) * t + t, 2)


def main():
    t0 = time.time()
    src = json.load(open(SRC))
    rows = {r['s']: r for r in src['rows']}
    meta = src['meta']
    conn = psycopg2.connect(**DB)
    syms = list(rows)
    df = pd.read_sql("""select symbol, (time at time zone 'Asia/Kolkata')::date d, open::float8 o, high::float8 h,
        low::float8 l, close::float8 c, volume::float8 v from ohlcv_data where symbol = any(%s) and time >= now() - interval '800 days'""",
                     conn, params=(syms,))
    first = pd.read_sql("select symbol, min(time)::date f from ohlcv_data where symbol = any(%s) group by 1",
                        conn, params=(syms,)).set_index('symbol')['f']
    try:
        fund = pd.read_sql('select * from ohm_fundamentals', conn).set_index('symbol')
    except Exception:
        fund = pd.DataFrame()
    df['d'] = pd.to_datetime(df['d'])
    asof = pd.Timestamp(meta['asof'])
    df = df[df.d <= asof]
    C = df.pivot(index='d', columns='symbol', values='c').sort_index()
    H = df.pivot(index='d', columns='symbol', values='h').sort_index()
    L = df.pivot(index='d', columns='symbol', values='l').sort_index()
    V = df.pivot(index='d', columns='symbol', values='v').sort_index()
    ch = C.diff()
    ud50 = V.where(ch > 0).tail(50).mean() / V.where(ch < 0).tail(50).mean()   # up-day vs down-day volume
    mf60 = (np.sign(ch) * V * C).tail(60).sum()                                # 60-day fund flow (in minus out)
    c = C.ffill().iloc[-1]
    s50 = C.rolling(50, min_periods=40).mean().iloc[-1]
    s200 = C.rolling(200, min_periods=160).mean().iloc[-1]
    e10 = C.ewm(span=10, adjust=False).mean().iloc[-1]
    e21 = C.ewm(span=21, adjust=False).mean().iloc[-1]
    wk = (C.ffill().iloc[-1] / C.ffill().iloc[-6] - 1) * 100
    hp, lp = H.iloc[-2], L.iloc[-2]
    cutoff = (asof - pd.Timedelta(days=730)).date()
    data_start = df.d.min().date() + dt.timedelta(days=10)

    cap = capital()
    risk_amt, cap_amt = cap * RISK_PCT / 100, cap * CAP_PCT / 100
    phase = meta.get('regime')
    size_mult = 0.5 if phase == 'DISTRIBUTION' else 1.0
    buys_allowed = phase != 'DECLINE'

    liq = [s for s in syms if (rows[s].get('tov') or 0) >= 10]
    above50 = float((c[liq] > s50[liq]).mean() * 100) if liq else None
    above200 = float((c[liq] > s200[liq]).mean() * 100) if liq else None

    trad = [s for s in syms if (rows[s].get('tov') or 0) >= 1]
    # IFP check (course): up days on high volume, down days on low volume, money flowing in over 60 days
    ifp_ok = {s for s in trad if (rows[s].get('ifp') or 0) >= IFP_MIN and (ud50.get(s) or 0) >= UD_MIN
              and (mf60.get(s) or 0) > 0}
    sec_all, sec_ifp = {}, {}
    for s in trad:
        k = rows[s].get('sec')
        if not k: continue
        sec_all[k] = sec_all.get(k, 0) + 1
        sec_ifp[k] = sec_ifp.get(k, 0) + (s in ifp_ok)
    # sector scan: footprint across many stocks of one sector
    hot_secs = {k for k in sec_all if sec_ifp[k] >= SEC_MIN_N and sec_ifp[k] / sec_all[k] >= SEC_MIN_SHARE}

    def coffee(s):
        if fund.empty or s not in fund.index or not bool(fund.at[s, 'ok']):
            return None, []
        fails = []
        for k, mn, lab in COFFEE:
            v = fund.at[s, k]
            if v is None or not np.isfinite(v) or v <= mn:
                fails.append(lab)
        return len(fails) == 0, fails

    out, n_fund_pass, fund_cov = [], 0, 0
    for s in trad:
        r = rows[s]
        cf, fails = coffee(s)
        fund_cov += cf is not None
        n_fund_pass += bool(cf)
        routes = []
        if cf: routes.append('Fundamentals')
        if wk.get(s, 0) >= 20 and c.get(s, 0) > (s200.get(s) or 1e18): routes.append('Movers')
        fd = first.get(s)
        if fd is not None and fd > cutoff and fd > data_start: routes.append('New listing')
        if r.get('sec') in hot_secs: routes.append('Sector')
        r2 = dict(s=s, nm=r['nm'], sec=r.get('sec'), c=r['c'], chg=r['chg'], tov=r.get('tov'), rs=r.get('rs'),
                  ifp=r.get('ifp'), cq=r.get('cq'), ca=r.get('ca'), hi=r.get('hi'), r1=r.get('r1'), atr=r.get('atr'),
                  vx=r.get('vx'), ud=f(ud50.get(s), 2), mf=f((mf60.get(s) or 0) / 1e7, 1), pats=[p['t'] for p in (r.get('pats') or [])], routes=routes,
                  fundOk=cf, fundFails=fails, wk=f(wk.get(s), 1),
                  above50=bool(c.get(s, 0) > s50.get(s, np.inf)) if np.isfinite(s50.get(s, np.nan)) else None,
                  e21d=f((c[s] / e21[s] - 1) * 100, 1) if s in e21 and e21[s] else None,
                  e10d=f((c[s] - e10[s]) / r['atr'], 1) if r.get('atr') and s in e10 else None)
        if cf is not None:
            fr = fund.loc[s]
            r2['fund'] = {k: f(fr[k], 1) for k, _, _ in COFFEE}
        r2['watch'] = bool(routes) and s in ifp_ok
        # entry bar plan (PDF rules)
        ent = r.get('ent')
        r2['ent'] = ent
        if ent:
            t = tick_of(s)
            if ent == 'HH-HL':
                raw, stop = max(r['h'], float(hp.get(s, r['h']))), min(r['l'], float(lp.get(s, r['l'])))
            else:
                raw, stop = r['h'], r['l']
            entry = up_tick(raw, t)
            rps = entry - stop
            if rps > 0:
                q_risk, q_cap = int(risk_amt // rps), int(cap_amt // entry)
                full = max(0, min(q_risk, q_cap))
                half = full // 2
                sug = half if size_mult < 1 else full
                r2['plan'] = dict(entry=f(entry), stop=f(stop), tick=t, rps=f(rps), stopPct=f(rps / entry * 100, 2),
                                  full=full, half=half, qty=sug, limitBy='risk' if q_risk <= q_cap else 'capital cap',
                                  qRisk=q_risk, amount=f(sug * entry, 0), risk=f(sug * rps, 0),
                                  t2r=f(entry + 2 * rps), safety=f(math.floor(entry * (1 - SAFETY_PCT / 100) / t) * t),
                                  name=ENT_NAME.get(ent, ent))
        r2['focus'] = bool(r2['watch'] and ent and r2.get('plan') and r2['above50'] and (r.get('hi') or 0) >= 75
                           and (r.get('r1') or 99) <= 15 and r2['e21d'] is not None and abs(r2['e21d']) <= 8)
        out.append(r2)

    watch = [x for x in out if x['watch']]
    focus = [x for x in out if x['focus']]
    secs = {}
    for x in watch:
        k = x['sec'] or 'Other'
        secs[k] = secs.get(k, 0) + 1
    routes = {k: sum(1 for x in watch if k in x['routes']) for k in ('Fundamentals', 'Movers', 'New listing', 'Sector')}
    res = dict(meta=dict(asof=meta['asof'], generated=dt.datetime.now().isoformat(timespec='seconds'),
                         phase=phase, breadth=meta.get('breadth'), niftyAbove200=meta.get('niftyAbove200'),
                         above50=f(above50, 1), above200=f(above200, 1), capital=cap, riskPct=RISK_PCT, capPct=CAP_PCT,
                         sizeMult=size_mult, buysAllowed=buys_allowed, safetyPct=SAFETY_PCT, ifpMin=IFP_MIN,
                         coffee=[dict(k=k, min=m, label=lab) for k, m, lab in COFFEE],
                         fundCoverage=fund_cov, fundPass=n_fund_pass,
                         fundAsOf=str(fund['fetched_at'].max())[:16] if not fund.empty else None),
               funnel=dict(tradeable=len(trad), fundamentals=n_fund_pass, ifp=len(ifp_ok), watch=len(watch),
                           focus=len(focus), routes=routes, noRoute=len(ifp_ok) - len(watch)),
               sectors=sorted(secs.items(), key=lambda kv: -kv[1]),
               hotSectors=sorted(([k, sec_ifp[k], sec_all[k]] for k in hot_secs), key=lambda x: -x[1]),
               stocks=[x for x in out if x['watch'] or x['routes']])
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + '.tmp'
    json.dump(res, open(tmp, 'w'), separators=(',', ':'), default=lambda o: None)
    os.replace(tmp, OUT)
    log(f'wrote {OUT}: tradeable {len(trad)} watch {len(watch)} focus {len(focus)} routes {routes} '
        f'fund {n_fund_pass}/{fund_cov} phase {phase} in {time.time()-t0:.0f}s')


if __name__ == '__main__':
    main()
