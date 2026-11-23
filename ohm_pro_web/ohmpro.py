"""OHM-PRO core: the backtest-passed rules only (FINAL2 + CLEAN 2R), shared by the daily compute and the validation replay.

Rules (claude/2026-11-23-OHM-IMPROVE*/CLEAN2R verdicts; research code /root/research/ohm_improve/prep*.py, sim*.py):
  universe   20d turnover >= Rs1 Cr, ETFs excluded, >= 300 bars
  setup      close > SMA50, close >= 75% of 252-bar high, 10-bar range <= 15%, |close/EMA21 - 1| <= 8%,
             50-day up/down volume >= 1.2, 60-day money flow > 0, no +/-40% jump in the last 252 bars
  entry bar  inside > HH-HL > pin > trend (range >= 0.5% of close); buy = bar high; stop = bar low (HH-HL: lower of 2 lows)
  gates      L: 1-yr return percentile >= 80% of liquid stocks | C and A >= 25% (missing = pass) |
             regime not DISTRIBUTION/DECLINE | JUNIORBEES close >= its SMA50
  fill       next bar of that stock if high >= buy, at max(open, buy); skip if stop > 8% below fill
  rank/size  by 1-yr return; risk 0.25% of equity (0.5% if 1-yr pct >= 95%), max 10%/stock, 20 positions, 0.1%/leg cost
  manage     close < stop -> sell all next open; close < EMA21 (from the day after fill) -> sell all next open;
             first 2R touch (high >= fill + 2R, from the day after fill): close >= 2R -> keep, no sale/add;
             close < 2R -> sell half next open, then first close >= 2R -> add half next open
  cash       idle cash earns 4%/yr (LIQUIDCASE)
Everything uses data up to each day's close only."""
import bisect, math, time
import numpy as np, pandas as pd, psycopg2

DB = dict(host='localhost', dbname='market_data', user='postgres', password='postgres')
BAD = ['BEES', 'ETF', 'LIQUID', 'GOLD', 'SILVER', 'NIFTY', 'SENSEX', 'CASE', 'BETA']
RISK, RISK_TOP, TOP_PCT, LEAD_PCT, EARN_MIN = 0.0025, 0.005, 0.95, 0.80, 0.25
MAXPOS, MAXW, COST, CASH_YIELD, MAXSTOP = 20, 0.10, 0.001, 0.04, 0.08
PH = {0: 'ADVANCE', 1: 'DISTRIBUTION', 2: 'DECLINE', 3: 'ACCUMULATION'}
TYP = {1: 'Inside bar', 2: 'Higher high, higher low', 3: 'Pin bar', 4: 'Trend bar'}
T0 = time.time()
import os as _os
F32 = _os.environ.get('OHMPRO_F32') == '1'   # validation only: mimic the research code's float32 rank matrix


def log(*a):
    print(f'[{time.time() - T0:5.0f}s]', *a, flush=True)


def complete_dates(conn, min_share=0.95):
    """Trading dates whose row count is at least 95% of the median of the 10 dates before (a partial day is not used)."""
    d = pd.read_sql("select (time at time zone 'Asia/Kolkata')::date d, count(*) n from ohlcv_data group by 1 order by 1", conn)
    n = d.n.values.astype(float)
    ok = np.ones(len(d), bool)
    for i in range(10, len(d)):
        ok[i] = n[i] >= min_share * np.median(n[i - 10:i])
    last_ok = len(d) - 1
    while last_ok >= 0 and not ok[last_ok]:
        last_ok -= 1
    return pd.to_datetime(d.d.values[:last_ok + 1]).values      # all dates up to the last complete one


def scan(conn, cal, i_from, i_to, load_from=None, keep_bars=True, need=()):
    """One pass over every symbol. For calendar index i in [i_from, i_to]: market state, entry candidates, and
    (keep_bars) each stock's o/h/l/c/EMA21 so the engine can manage trades. Same arithmetic as prep.py."""
    NC = i_to + 1
    above = np.zeros(NC, np.int32); valid = np.zeros(NC, np.int32)
    nifty = np.full(NC, -1, np.int8); jr_c = np.full(NC, np.nan); jr_m = np.full(NC, np.nan)
    rs_lists = {i: [] for i in range(i_from, NC)}
    cands, bars = [], {}
    q = "select symbol from ohlcv_data group by symbol having count(*)>=300 order by 1"
    syms = pd.read_sql(q, conn).symbol.tolist()
    lf = str(pd.Timestamp(load_from).date()) if load_from is not None else '1990-01-01'
    to = str(pd.Timestamp(cal[i_to]).date())
    for b in range(0, len(syms), 150):
        batch = syms[b:b + 150]
        df = pd.read_sql("select (time at time zone 'Asia/Kolkata')::date d, symbol s, open::float8 o, high::float8 h, "
                         "low::float8 l, close::float8 c, volume::float8 v from ohlcv_data where symbol = any(%s) "
                         "and (time at time zone 'Asia/Kolkata')::date between %s and %s", conn, params=(batch, lf, to))
        for s, g in df.groupby('s'):
            _one(s, g, cal, i_from, NC, above, valid, nifty, jr_c, jr_m, rs_lists, cands, bars, keep_bars, s in need)
        del df
        if b % 900 == 0:
            log(f'scan {b + len(batch)}/{len(syms)} candidates={len(cands)}')
    with np.errstate(invalid='ignore', divide='ignore'):
        br = np.where(valid > 0, above / np.maximum(valid, 1), np.nan)
    reg = np.full(NC, 1, np.int8)
    reg[br >= 0.60] = 0
    low = br <= 0.30
    reg[low & (nifty == 0)] = 2
    reg[low & (nifty != 0)] = 3
    jr_on = ~(jr_c < jr_m)                      # NaN (no data) = on, as in the backtest
    srt = {i: np.sort(np.array(v, dtype=np.float32 if F32 else np.float64)) for i, v in rs_lists.items()}
    for c in cands:
        a = srt[c['i']]
        c['lpct'] = float(np.searchsorted(a, c['r252'], 'right') / len(a)) if len(a) >= 50 and np.isfinite(c['r252']) else float('nan')
    mkt = dict(reg=reg, breadth=br, nifty=nifty, jr_c=jr_c, jr_m=jr_m, jr_on=jr_on)
    return mkt, cands, bars


def _one(s, g, cal, i_from, NC, above, valid, nifty, jr_c, jr_m, rs_lists, cands, bars, keep_bars, needed=False):
    g = g.drop_duplicates('d', keep='last').sort_values('d')
    g = g[g.v.notna() & (g.c > 0) & (g.l > 0) & (g.h >= g.l)]
    n = len(g)
    if n < 300:
        return
    gd = np.searchsorted(cal, pd.to_datetime(g.d).values)
    o, h, l, c, v = (g[k].values.astype('float64') for k in 'ohlcv')
    cs = pd.Series(c)
    sma50 = cs.rolling(50).mean().values
    sma200 = cs.rolling(200).mean().values
    ema21 = cs.ewm(span=21, adjust=False).mean().values
    pc = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
    tr[0] = h[0] - l[0]
    atr = pd.Series(tr).rolling(14).mean().values
    tov20 = pd.Series(c * v).rolling(20).mean().values
    win = gd >= i_from
    m = (tov20 >= 1e8) & ~np.isnan(sma200) & win
    np.add.at(valid, gd[m], 1)
    np.add.at(above, gd[m & (c > sma200)], 1)
    if s == 'NIFTYBEES':
        ok = ~np.isnan(sma200) & win
        nifty[gd[ok]] = (c[ok] > sma200[ok]).astype(np.int8)
    if s == 'JUNIORBEES':
        jr_c[gd[win]] = c[win]
        jr_m[gd[win]] = sma50[win]
    if any(b in s for b in BAD):
        return
    ret = c / pc - 1
    jrows = np.where(np.abs(ret) > 0.40)[0]
    hi252 = pd.Series(h).rolling(252).max().values
    hi10 = pd.Series(h).rolling(10).max().values
    lo10 = pd.Series(l).rolling(10).min().values
    rng10 = (hi10 - lo10) / hi10
    upv = pd.Series(np.where(c > pc, v, 0.0)).rolling(50).sum().values
    dnv = pd.Series(np.where(c < pc, v, 0.0)).rolling(50).sum().values
    with np.errstate(divide='ignore', invalid='ignore'):
        ratio = np.where(dnv > 0, upv / dnv, np.inf)
        rng = h - l
        mfm = np.where(rng > 0, ((c - l) - (h - c)) / rng, 0.0)
        r252 = c / np.r_[np.full(252, np.nan), c[:-252]] - 1
    mf60 = pd.Series(mfm * v).rolling(60).sum().values
    liq = (tov20 >= 1e7) & np.isfinite(r252) & win
    for j in np.where(liq)[0]:
        rs_lists[int(gd[j])].append(np.float32(r252[j]) if F32 else r252[j])
    with np.errstate(invalid='ignore'):
        setup = (c > sma50) & (c >= 0.75 * hi252) & (rng10 <= 0.15) & (np.abs(c / ema21 - 1) <= 0.08) \
            & (ratio >= 1.2) & (mf60 > 0) & (tov20 >= 1e7) & ~np.isnan(atr)
    setup[:260] = False
    ph = np.r_[np.nan, h[:-1]]; pl = np.r_[np.nan, l[:-1]]
    with np.errstate(invalid='ignore'):
        inside = (h < ph) & (l > pl); hhhl = (h > ph) & (l > pl)
        body = np.abs(c - o); lw = np.minimum(o, c) - l; uw = h - np.maximum(o, c)
        pin = (body <= 0.35 * rng) & ((lw >= 0.55 * rng) | (uw >= 0.55 * rng))
        trend = (c - l) >= 0.70 * rng
        okr = (rng / c) >= 0.005
    typ = np.zeros(n, int)
    typ[okr & trend] = 4; typ[okr & pin] = 3; typ[okr & hhhl] = 2; typ[okr & inside] = 1
    first = None
    for t in np.where(setup & (typ > 0) & win)[0]:
        if len(jrows) and np.any((jrows > t - 252) & (jrows <= t)):
            continue
        stp = pl[t] if typ[t] == 2 else l[t]
        cands.append(dict(sym=s, i=int(gd[t]), typ=int(typ[t]), buy=float(h[t]), stop=float(stp), close=float(c[t]),
                          r252=float(r252[t]), ratio=float(min(ratio[t], 1e6)), mf60=float(mf60[t]),
                          tov=float(tov20[t] / 1e7), rng10=float(rng10[t]), e21d=float(c[t] / ema21[t] - 1),
                          hi=float(c[t] / hi252[t])))
        first = t if first is None else first
    if keep_bars and (needed or first is not None):
        w = np.where(win)[0] if needed else np.arange(first, n)
        if len(w):
            bars[s] = dict(i=gd[w].astype(np.int32), o=o[w], h=h[w], l=l[w], c=c[w], e21=ema21[w])


def _growth(n, o_):
    if o_ > 0:
        return float(np.clip((n - o_) / o_, -1, 5))
    return 2.0 if n > 0 else -1.0


def earnings(conn, cands, cal):
    """Point-in-time C (latest quarter net profit YoY) and A (trailing 4 quarters YoY) for each candidate, as earn.py:
    only filings broadcast strictly before the signal date, consolidated first, older than 185 days = missing."""
    syms = sorted({c['sym'] for c in cands})
    if not syms:
        return
    f = pd.read_sql("select symbol,period_from,period_to,broadcast_date,consolidated,net_profit from earnings_fundamentals "
                    "where parse_status='ok' and net_profit is not null and symbol = any(%(s)s) union all "
                    "select symbol,period_from,period_to,broadcast_date,consolidated,net_profit from ohm_scr_earnings_if "
                    "where parse_status='ok' and net_profit is not null and symbol = any(%(s)s)", conn, params=dict(s=syms))
    f['np'] = f.net_profit.astype(float)
    f['pt'] = pd.to_datetime(f.period_to); f['pf'] = pd.to_datetime(f.period_from)
    f['bd'] = pd.to_datetime(f.broadcast_date).dt.normalize()
    f = f[f.bd.notna() & f.pt.notna()]
    ln = (f.pt - f.pf).dt.days
    f = f[ln.isna() | ln.between(80, 100)]
    f = f.sort_values(['symbol', 'consolidated', 'pt', 'bd']).drop_duplicates(['symbol', 'consolidated', 'pt'], keep='first')
    EV = {}
    for (s, typ), g in f.groupby(['symbol', 'consolidated']):
        g = g.sort_values('bd')
        bds, sts = [], []
        for bd in g.bd.unique():
            k = g[g.bd <= bd].sort_values('pt')
            pt = k.pt.values; npv = k.np.values; i = len(k) - 1
            d = (pt[i] - pt).astype('timedelta64[D]').astype(int)
            j = np.where((d >= 345) & (d <= 385))[0]
            qg = _growth(npv[i], npv[j[0]]) if len(j) else None
            cur = np.where((d >= 0) & (d < 330))[0]; prv = np.where((d >= 330) & (d < 695))[0]
            ag = _growth(npv[cur].sum(), npv[prv].sum()) if len(cur) == 4 and len(prv) == 4 else None
            bds.append(bd); sts.append((qg, ag, k.bd.iloc[i]))
        EV[(s, typ)] = (bds, sts)

    def state(s, typ, S):
        e = EV.get((s, typ))
        if not e:
            return None
        k = bisect.bisect_left(e[0], S) - 1
        if k < 0:
            return None
        qg, ag, bdl = e[1][k]
        if (S - bdl).days > 185:
            return None
        return qg, ag
    for c in cands:
        S = pd.Timestamp(cal[c['i']])
        r = None
        if S >= pd.Timestamp('2018-01-01'):
            r = state(c['sym'], 'Consolidated', S) or state(c['sym'], 'Non-Consolidated', S)
        c['C'] = None if r is None else r[0]
        c['A'] = None if r is None else r[1]


def gates(c, mkt):
    """Why a candidate is blocked ('' = passes). Market gates first."""
    i = c['i']
    if mkt['reg'][i] in (1, 2):
        return 'market phase ' + PH[int(mkt['reg'][i])]
    if not mkt['jr_on'][i]:
        return 'JUNIORBEES below its 50-day average'
    if np.isfinite(c['lpct']) and c['lpct'] < LEAD_PCT:
        return '1-yr return not in top 20%'
    if c.get('C') is not None and c['C'] < EARN_MIN:
        return 'quarter profit growth below 25%'
    if c.get('A') is not None and c['A'] < EARN_MIN:
        return 'yearly profit growth below 25%'
    return ''


def new_book(capital=400000.0):
    return dict(cash=float(capital), eq=float(capital), start=None, last_i=None, last_date=None,
                positions={}, pending=[], sched=[], closed=[], curve=[], fills=[])


class Bars:
    """Per-day access to one stock's o/h/l/c/EMA21 from scan(bars)."""
    def __init__(self, bars):
        self.b = bars

    def at(self, s, i):
        x = self.b.get(s)
        if x is None:
            return None
        k = int(np.searchsorted(x['i'], i))
        if k < len(x['i']) and x['i'][k] == i:
            return dict(o=float(x['o'][k]), h=float(x['h'][k]), l=float(x['l'][k]), c=float(x['c'][k]), e21=float(x['e21'][k]))
        return None


def step(book, i, date, bars, cands_today, mkt, prev_date=None, whole=True):
    """Advance the paper book by one trading day (calendar index i). Same order as the backtest:
    exits/half sales at the open, then adds, then new fills, then close checks; cash yield; equity."""
    P = book['positions']
    eq_prev = book['eq']
    log_ = []
    # 1) scheduled actions at this open (a stock with no bar today keeps its action for its next bar)
    keep = []
    todo = [a for a in book['sched'] if bars.at(a['sym'], i) is not None]
    keep = [a for a in book['sched'] if bars.at(a['sym'], i) is None]
    for grp in (('sell_half', 'sell_all'), ('buy_half',)):
        for a in [a for a in todo if a['act'] in grp]:
            p = P.get(a['sym'])
            if p is None:
                continue
            px = bars.at(a['sym'], i)['o']
            if a['act'] == 'sell_half':
                s_ = min(0.5 * p['sh0'], p['sh'])
                if whole: s_ = math.floor(s_)
                p['sh'] -= s_; p['proc'] += s_ * px * (1 - COST); book['cash'] += s_ * px * (1 - COST)
                p['half_sold'] = True
                log_.append(dict(sym=a['sym'], act='Sold half', px=px, qty=s_, why=a['why']))
            elif a['act'] == 'sell_all':
                q = p['sh'] + p['sha']
                p['proc'] += q * px * (1 - COST); book['cash'] += q * px * (1 - COST)
                R = (p['proc'] - p['cost']) / p['risk'] if p['risk'] > 0 else 0.0
                book['closed'].append(dict(sym=a['sym'], fill_date=p['fill_date'], exit_date=date, fill=p['fill'], stop=p['stop'],
                                           exit=px, R=round(R, 3), pnl=round(p['proc'] - p['cost'], 2), why=a['why'],
                                           half=p['half_sold'], added=p['added'], kept=p['kept'], typ=p['typ']))
                log_.append(dict(sym=a['sym'], act='Sold all', px=px, qty=q, why=a['why'], R=round(R, 2)))
                del P[a['sym']]
            else:
                p['add_tried'] = True                      # one attempt only, as in the backtest
                cur = (p['sh'] + p['sha']) * px
                notional = min(0.5 * p['sh0'] * px, max(0.0, MAXW * eq_prev - cur), book['cash'] / (1 + COST))
                q = notional / px
                if whole: q = math.floor(q)
                if q * px >= 1000:
                    book['cash'] -= q * px * (1 + COST); p['cost'] += q * px * (1 + COST); p['sha'] += q; p['added'] = True
                    log_.append(dict(sym=a['sym'], act='Bought half back', px=px, qty=q, why=a['why']))
    book['sched'] = keep
    # 2) new fills from yesterday's candidates (each lives until its stock's next bar)
    fills, still = [], []
    for c in book['pending']:
        b = bars.at(c['sym'], i)
        if b is None:
            still.append(c); continue
        if b['h'] >= c['buy']:
            fills.append((c, max(b['o'], c['buy'])))
    book['pending'] = still
    fills.sort(key=lambda x: -(x[0]['r252'] if np.isfinite(x[0]['r252']) else -9))
    for c, px in fills:
        if px <= c['stop'] or (px - c['stop']) / px > MAXSTOP:
            continue
        if len(P) >= MAXPOS or c['sym'] in P:
            continue
        rps = px - c['stop']
        rk = RISK_TOP if np.isfinite(c['lpct']) and c['lpct'] >= TOP_PCT else RISK
        notional = min(rk * eq_prev / rps * px, MAXW * eq_prev, book['cash'] / (1 + COST))
        q = notional / px
        if whole: q = math.floor(q)
        if q * px < 1000:
            continue
        book['cash'] -= q * px * (1 + COST)
        P[c['sym']] = dict(fill=px, stop=c['stop'], fill_date=date, sig_date=c['sig_date'], typ=c['typ'], risk_pct=rk,
                           sh0=q, sh=q, sha=0.0, cost=q * px * (1 + COST), proc=0.0, risk=q * rps, rows=-1,
                           touched=False, kept=False, half_sold=False, added=False, last=px)
        log_.append(dict(sym=c['sym'], act='Bought', px=px, qty=q, why=f"filled above {c['buy']:.2f}, risk {rk*100:.2f}%"))
    # 3) close checks -> actions for the next open
    for s, p in P.items():
        b = bars.at(s, i)
        if b is None:
            continue
        p['rows'] += 1; p['last'] = b['c']; p['e21'] = b['e21']; p['last_date'] = date
        tgt = p['fill'] + 2 * (p['fill'] - p['stop'])
        p['t2r'] = tgt
        exiting = False
        if b['c'] < p['stop']:
            book['sched'].append(dict(sym=s, act='sell_all', why='closed below stop')); exiting = True
        elif p['rows'] >= 1 and b['c'] < b['e21']:
            book['sched'].append(dict(sym=s, act='sell_all', why='closed below 21 EMA')); exiting = True
        if p['rows'] >= 1:
            if not p['touched'] and b['h'] >= tgt:
                p['touched'] = True
                if b['c'] >= tgt:
                    p['kept'] = True
                else:
                    book['sched'].append(dict(sym=s, act='sell_half', why='touched 2R but closed below it'))
            elif p['touched'] and not p['kept'] and p['half_sold'] and not p.get('add_tried') and not exiting and b['c'] >= tgt \
                    and not any(a['sym'] == s and a['act'] == 'buy_half' for a in book['sched']):
                book['sched'].append(dict(sym=s, act='buy_half', why='closed back above 2R'))
    # 4) cash yield, equity
    if prev_date is not None and book['cash'] > 0:
        days = (pd.Timestamp(date) - pd.Timestamp(prev_date)).days
        book['cash'] += book['cash'] * CASH_YIELD * days / 365.0
    book['eq'] = book['cash'] + sum((p['sh'] + p['sha']) * p['last'] for p in P.values())
    book['curve'].append([date, round(book['eq'], 2)])
    # 5) today's candidates that pass every gate become tomorrow's buy-stops
    for c in cands_today:
        if not c.get('block'):
            book['pending'].append(dict(sym=c['sym'], buy=c['buy'], stop=c['stop'], r252=c['r252'], lpct=c['lpct'],
                                        typ=c['typ'], sig_date=date))
    book['last_i'] = i; book['last_date'] = date
    book['fills'] = (book['fills'] + [dict(date=date, **x) for x in log_])[-400:]
    return log_


def replay(fill, stop, df):
    """Live trade guidance: replay the same rules over the stock's bars from the fill day (row 0).
    df columns: d, o, h, l, c, e21 (EMA21 from full history). Returns the state after the last close and
    the action for the next open, or the exit already due."""
    tgt = fill + 2 * (fill - stop)
    st = dict(touched=False, kept=False, half_sold=False, added=False, exit=None, events=[])
    pend = []
    for r, x in enumerate(df.itertuples(index=False)):
        for a in pend:                                     # yesterday's actions happen at this open
            if a == 'sell_half':
                st['half_sold'] = True; st['events'].append((str(x.d), 'sell half at open', round(x.o, 2)))
            elif a == 'buy_half':
                st['added'] = True; st['events'].append((str(x.d), 'buy half back at open', round(x.o, 2)))
            elif a.startswith('sell_all'):
                st['exit'] = (str(x.d), round(x.o, 2), a.split(':', 1)[1]); st['events'].append((str(x.d), 'sell all at open', round(x.o, 2)))
                return st, None
        pend = []
        exiting = False
        if x.c < stop:
            pend.append('sell_all:closed below stop'); exiting = True
        elif r >= 1 and x.c < x.e21:
            pend.append('sell_all:closed below 21 EMA'); exiting = True
        if r >= 1:
            if not st['touched'] and x.h >= tgt:
                st['touched'] = True
                if x.c >= tgt:
                    st['kept'] = True
                else:
                    pend.insert(0, 'sell_half')
            elif st['touched'] and not st['kept'] and st['half_sold'] and not st['added'] and not exiting and x.c >= tgt:
                pend.append('buy_half')
    nxt = None
    if pend:
        a = pend[-1] if any(p.startswith('sell_all') for p in pend) else pend[0]
        nxt = dict(act=a.split(':')[0], why=a.split(':', 1)[1] if ':' in a else
                   ('touched 2R but closed below it' if a == 'sell_half' else 'closed back above 2R'))
    return st, nxt
