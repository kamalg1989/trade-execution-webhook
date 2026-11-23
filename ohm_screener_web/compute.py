#!/usr/bin/env python3
"""OHM Screener (web) — nightly compute.

CAN SLIM + OHM metrics for every liquid NSE stock, written to
/root/ohm_screener_web/data/latest.json for the /ohm-screener/ page.
New module: reads market_data only, changes nothing else.
IFP / entry-technique / regime rules copied from screen_gpt.py (not imported).
VCP / RS / stop rules follow /root/canslim/run3.py (the tested setup).
"""
import json, os, sys, time, datetime as dt
import numpy as np, pandas as pd, psycopg2
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from patterns import detect as detect_patterns

BASE = '/root/ohm_screener_web'
OUT = BASE + '/data/latest.json'
NSE = '/root/canslim/nse'
T0 = time.time()


def log(*a):
    print(f'[{time.time() - T0:5.0f}s]', *a, flush=True)


# ---- screen_gpt constants (copied) ----
IFP_VOL_SURGE_MULTIPLE = 1.5
IFP_UP_DAY_CLOSE_POS_MIN = 0.60
IFP_LOOKBACK_DAYS = 100
TREND_BAR_CLOSE_THRESHOLD = 0.70
PIN_BAR_MAX_BODY_PCT = 0.35
PIN_BAR_MIN_LOWER_WICK_PCT = 0.55
MIN_BAR_RANGE_PCT = 0.005
REGIME_BULLISH_THRESHOLD = 0.60
REGIME_BEARISH_THRESHOLD = 0.30

BAD = ['BEES', 'ETF', 'LIQUID', 'GOLD', 'SILVER', 'NIFTY', 'SENSEX', 'CASE', 'BETA']


def fnum(x, nd=2):
    if x is None:
        return None
    try:
        x = float(x)
    except Exception:
        return None
    if not np.isfinite(x):
        return None
    return round(x, nd)


def main():
    conn = psycopg2.connect(host='localhost', user='postgres', password='postgres', dbname='market_data')
    q = ("select (time at time zone 'Asia/Kolkata')::date d, symbol s, open::float8 o, high::float8 h, "
         "low::float8 l, close::float8 c, volume::float8 v from ohlcv_data "
         "where time >= now() - interval '450 days'")
    df = pd.read_sql(q, conn)
    log('rows', len(df))
    W = {k: df.pivot_table(index='d', columns='s', values=k, aggfunc='last').astype('float32') for k in 'ohlcv'}
    del df
    C = W['c']
    # drop thin dates (holidays / partial loads)
    cnt = C.notna().sum(1)
    good = cnt >= 0.5 * cnt.rolling(20, min_periods=1).median()
    for k in W:
        W[k] = W[k][good]
    O, H, L, C, V = W['o'], W['h'], W['l'], W['c'], W['v']
    asof = C.index[-1]
    log('as of', asof, 'shape', C.shape)

    Cp = C.shift(1)
    tov20 = (C * V).rolling(20, min_periods=16).mean()
    sma50 = C.rolling(50, min_periods=40).mean()
    sma200 = C.rolling(200, min_periods=160).mean()
    hi252 = H.rolling(252, min_periods=200).max()
    tr = np.maximum(H - L, np.maximum((H - Cp).abs(), (L - Cp).abs()))
    atr14 = tr.rolling(14, min_periods=12).mean()
    vol50 = V.rolling(50, min_periods=40).mean().shift(1)
    vol10 = V.rolling(10, min_periods=8).mean().shift(1)
    hi10 = H.rolling(10, min_periods=8).max().shift(1)
    lo10 = L.rolling(10, min_periods=8).min().shift(1)
    r1 = (hi10 - lo10) / hi10
    r2 = (H.rolling(20, min_periods=16).max().shift(11) - L.rolling(20, min_periods=16).min().shift(11)) / H.rolling(20, min_periods=16).max().shift(11)
    a3 = H.rolling(30, min_periods=24).max().shift(31)
    r3 = (a3 - L.rolling(30, min_periods=24).min().shift(31)) / a3
    hi60 = H.rolling(60, min_periods=48).max().shift(1)
    lo60 = L.rolling(60, min_periods=48).min().shift(1)
    dep = (hi60 - lo60) / hi60
    hi252p = H.rolling(252, min_periods=200).max().shift(1)

    t = -1
    last = lambda X: X.iloc[t]
    c, h, l, o, v = last(C), last(H), last(L), last(O), last(V)
    cp, lp, hp = C.iloc[-2], L.iloc[-2], H.iloc[-2]

    vS = (last(r1) < last(r2)) & (last(r2) < last(r3)) & (last(r1) <= 0.12) & (last(dep) <= 0.35) & \
         (last(hi10) >= 0.95 * last(hi60)) & (last(vol10) < last(vol50))
    vL = (last(r1) < last(r3)) & (last(r1) <= 0.15) & (last(dep) <= 0.40) & \
         (last(hi10) >= 0.92 * last(hi60)) & (last(vol10) < 1.1 * last(vol50))

    # relative strength (run3: 0.4*3m + 0.2*(6m+9m+12m))
    def ret(n):
        return c / C.iloc[-1 - n] - 1 if len(C) > n else c * np.nan
    rsraw = 0.4 * ret(63) + 0.2 * (ret(126) + ret(189) + ret(252))

    # IFP (screen_gpt.compute_ifp_score, vectorised)
    va20 = V.rolling(20).mean()
    wv = slice(-IFP_LOOKBACK_DAYS, None)
    up = (C > Cp).iloc[wv]
    rng = (H - L).iloc[wv]
    cpos = ((C - L).iloc[wv] / rng.where(rng > 0)).fillna(0)
    surge = up & (V.iloc[wv] > IFP_VOL_SURGE_MULTIPLE * va20.iloc[wv]) & (cpos >= IFP_UP_DAY_CLOSE_POS_MIN)
    dry = (~up) & (V.iloc[wv] < va20.iloc[wv])
    ifp = (surge.sum() + dry.sum()) / IFP_LOOKBACK_DAYS
    nbars = C.notna().sum()
    ifp[nbars < 20 + IFP_LOOKBACK_DAYS] = np.nan

    liquid10 = last(tov20) >= 1e8
    keep = (last(tov20) >= 2e7) & (nbars >= 200) & (c > 0)
    keep &= ~pd.Series([any(b in s for b in BAD) for s in C.columns], index=C.columns)
    syms = list(C.columns[keep.values])
    log('kept', len(syms))

    # breadth / regime (liquid >= Rs10cr)
    m200 = liquid10 & last(sma200).notna()
    breadth = float((c[m200] > last(sma200)[m200]).mean())
    nb = 'NIFTYBEES'
    nifty_above = bool(c[nb] > last(sma200)[nb]) if nb in C.columns and np.isfinite(last(sma200).get(nb, np.nan)) else None
    if breadth >= REGIME_BULLISH_THRESHOLD:
        regime = 'ADVANCE'
    elif breadth <= REGIME_BEARISH_THRESHOLD:
        regime = 'DECLINE' if nifty_above is False else 'ACCUMULATION'
    else:
        regime = 'DISTRIBUTION'

    # ---- earnings: C (latest quarter YoY), A (TTM YoY) — run.py logic ----
    f = pd.read_sql("select symbol,period_from,period_to,broadcast_date,consolidated,net_profit "
                    "from earnings_fundamentals where parse_status='ok' and net_profit is not null "
                    "union all select symbol,period_from,period_to,broadcast_date,consolidated,net_profit "
                    "from ohm_scr_earnings_if where parse_status='ok' and net_profit is not null", conn)
    f['np'] = f.net_profit.astype(float)
    f['pt'] = pd.to_datetime(f.period_to); f['pf'] = pd.to_datetime(f.period_from); f['bd'] = pd.to_datetime(f.broadcast_date)
    ln = (f.pt - f.pf).dt.days
    f = f[ln.isna() | ln.between(80, 100)]
    f = f.sort_values(['symbol', 'consolidated', 'pt', 'bd']).drop_duplicates(['symbol', 'consolidated', 'pt'], keep='first')
    asof_ts = pd.Timestamp(asof)
    f = f[f.bd <= asof_ts + pd.Timedelta(days=1)]
    earn_latest = str(f.bd.max().date()) if len(f) else None

    def growth(n, o_):
        if o_ > 0:
            return float(np.clip((n - o_) / o_, -1, 5))
        return 2.0 if n > 0 else -1.0
    EARN = {}
    for (s, typ), g in f.groupby(['symbol', 'consolidated']):
        g = g.sort_values('pt')
        i = len(g) - 1
        pt = g.pt.values; npv = g.np.values
        d = (pt[i] - pt).astype('timedelta64[D]').astype(int)
        j = np.where((d >= 345) & (d <= 385))[0]
        qg = growth(npv[i], npv[j[0]]) if len(j) else None
        cur = np.where((d >= 0) & (d < 330))[0]; prv = np.where((d >= 330) & (d < 695))[0]
        ag = growth(npv[cur].sum(), npv[prv].sum()) if len(cur) == 4 and len(prv) == 4 else None
        bd = g.bd.iloc[i]
        if (asof_ts - bd).days > 185:
            continue
        rec = EARN.setdefault(s, {})
        rec[typ] = (qg, ag, str(bd.date()), str(pd.Timestamp(pt[i]).date()))
    def earn(s):
        r = EARN.get(s, {})
        for typ in ('Consolidated', 'Non-Consolidated'):
            if typ in r:
                return r[typ]
        return (None, None, None, None)

    # ---- names / sector / promoter ----
    names = dict(pd.read_sql("select symbol, security_name from symbols_meta", conn).values)
    conn.close()
    sector, promo = {}, {}
    try:
        for ln_ in open(NSE + '/sector.jsonl'):
            r = json.loads(ln_)
            dd = r.get('data') or {}
            if dd.get('basicIndustry'):
                sector[r['symbol']] = dd['basicIndustry'].strip()
    except FileNotFoundError:
        pass
    try:
        for ln_ in open(NSE + '/shp.jsonl'):
            r = json.loads(ln_)
            rows = [x for x in (r.get('data') or []) if x.get('pr_and_prgrp') not in (None, '', '-')]
            def pdate(x):
                try:
                    return dt.datetime.strptime(x['date'], '%d-%b-%Y')
                except Exception:
                    return dt.datetime(1900, 1, 1)
            rows.sort(key=pdate)
            if rows:
                cur_ = float(rows[-1]['pr_and_prgrp'])
                prv_ = float(rows[-2]['pr_and_prgrp']) if len(rows) > 1 else None
                promo[r['symbol']] = (cur_, None if prv_ is None else cur_ - prv_)
    except FileNotFoundError:
        pass

    # ---- entry technique on last bar (screen_gpt.detect_entry_technique) ----
    def entry_type(s):
        d1h, d1l = hp[s], lp[s]
        d2o, d2h, d2l, d2c = o[s], h[s], l[s], c[s]
        if not all(np.isfinite([d1h, d1l, d2o, d2h, d2l, d2c])):
            return None
        rg = d2h - d2l
        if rg < d2c * MIN_BAR_RANGE_PCT:
            return None
        cpos_ = (d2c - d2l) / rg if rg > 0 else 0
        body = abs(d2c - d2o) / rg if rg > 0 else 0
        lw = (min(d2o, d2c) - d2l) / rg if rg > 0 else 0
        uw = (d2h - max(d2o, d2c)) / rg if rg > 0 else 0  # inverted hammer
        if d2h > d1h and d2l > d1l:
            return 'HH-HL'
        if d2h <= d1h and d2l >= d1l:
            return 'Inside bar'
        if body <= PIN_BAR_MAX_BODY_PCT and (lw >= PIN_BAR_MIN_LOWER_WICK_PCT or uw >= PIN_BAR_MIN_LOWER_WICK_PCT):
            return 'Pin bar'
        if cpos_ >= TREND_BAR_CLOSE_THRESHOLD:
            return 'Trend bar'
        return None

    # percentiles over liquid (>= Rs10cr) names, 0..99
    def pct(series):
        base = series[liquid10 & series.notna()]
        if not len(base):
            return pd.Series(np.nan, index=series.index)
        srt = np.sort(base.values)
        p = np.searchsorted(srt, series.values, side='right') / len(srt) * 99
        return pd.Series(np.where(series.notna(), p, np.nan), index=series.index)
    npos = c / last(hi252)
    rs_p, n_p, ifp_p = pct(rsraw), pct(npos), pct(ifp)
    cq = pd.Series({s: earn(s)[0] for s in C.columns}, dtype=float)
    ca = pd.Series({s: earn(s)[1] for s in C.columns}, dtype=float)
    c_p, a_p = pct(cq), pct(ca)

    def pats_for(s):
        try:
            p = detect_patterns(H[s].values, L[s].values, C[s].values, V[s].values)
        except Exception as e:
            log('pattern error', s, repr(e)); p = []
        if bool(vS[s]):  # strict VCP only (loose is too common to be useful)
            p.insert(0, {'t': 'vcp', 'piv': fnum(last(hi10)[s]), 'dep': fnum(last(dep)[s] * 100, 1), 'wk': 12.0, 'ol': []})
        return p

    out = []
    for s in syms:
        et = entry_type(s)
        # course PDF: single bars and inside bar -> that bar's low; HH-HL / none -> lower of 2 bars
        stop = l[s] if et in ('Inside bar', 'Pin bar', 'Trend bar') else min(l[s], lp[s])
        stop = max(stop, 0.88 * c[s])  # -12% safety stop (run.py)
        qg, ag, ebd, ept = earn(s)
        pr = promo.get(s, (None, None))
        sma_ok = bool(c[s] > last(sma200)[s] and last(sma50)[s] > last(sma200)[s])
        out.append({
            's': s, 'nm': names.get(s, s), 'sec': sector.get(s),
            'c': fnum(c[s]), 'h': fnum(h[s]), 'l': fnum(l[s]), 'chg': fnum((c[s] / cp[s] - 1) * 100),
            'tov': fnum(last(tov20)[s] / 1e7, 1),
            'trd': sma_ok, 'a200': bool(c[s] > last(sma200)[s]),
            'hi': fnum(npos[s] * 100, 1),
            'rs': fnum(rs_p[s], 0), 'rsr': fnum(rsraw[s] * 100, 1),
            'ifp': fnum(ifp[s], 2), 'ifpP': fnum(ifp_p[s], 0),
            'cq': fnum(None if qg is None else qg * 100, 0), 'ca': fnum(None if ag is None else ag * 100, 0),
            'cP': fnum(c_p[s], 0), 'aP': fnum(a_p[s], 0), 'nP': fnum(n_p[s], 0),
            'ebd': ebd, 'ept': ept,
            'pro': fnum(pr[0], 1), 'proD': fnum(pr[1], 2),
            'vS': bool(vS[s]), 'vL': bool(vL[s]),
            'dep': fnum(last(dep)[s] * 100, 1), 'r1': fnum(last(r1)[s] * 100, 1),
            'piv': fnum(last(hi10)[s]), 'piv52': fnum(last(hi252p)[s]),
            'vx': fnum(v[s] / last(vol50)[s], 2),
            'atr': fnum(last(atr14)[s]), 'stp': fnum(stop),
            'ent': et,
            'pats': pats_for(s),
        })
    meta = {
        'asof': str(asof), 'generated': dt.datetime.now().isoformat(timespec='seconds'),
        'breadth': round(breadth * 100, 1), 'regime': regime, 'niftyAbove200': nifty_above,
        'earnLatest': earn_latest, 'n': len(out),
        'promoNote': 'promoter data snapshot from NSE harvest (not refreshed nightly)',
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + '.tmp'
    with open(tmp, 'w') as fh:
        json.dump({'meta': meta, 'rows': out}, fh, separators=(',', ':'))
    os.replace(tmp, OUT)
    log('wrote', OUT, len(out), 'rows; breadth', meta['breadth'], regime)


def alert(msg):
    try:
        import urllib.request, urllib.parse
        env = {}
        for ln_ in open('/root/trade-execution-webhook/.env'):
            if '=' in ln_ and not ln_.startswith('#'):
                k, v_ = ln_.strip().split('=', 1)
                env[k] = v_.strip().strip('"').strip("'")
        tok, chat = env.get('TELEGRAM_TOKEN'), env.get('TELEGRAM_CHAT_ID')
        if tok and chat:
            data = urllib.parse.urlencode({'chat_id': chat, 'text': msg}).encode()
            urllib.request.urlopen(f'https://api.telegram.org/bot{tok}/sendMessage', data, timeout=15)
    except Exception:
        pass


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        log('FAILED', repr(e))
        alert(f'OHM Screener web compute FAILED: {e!r}')
        raise
