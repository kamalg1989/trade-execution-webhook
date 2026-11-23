#!/usr/bin/env python3
"""OHM-PRO daily compute (/ohm-pro/). Runs after the close (timer); idempotent.
- processes every complete trading day not yet in the paper book (a partial day is skipped until its data is complete)
- writes data/paper.json (paper book state) and data/pro.json (page data)
Backtest-passed rules only: see ohmpro.py. Usage: compute.py [--reset]"""
import datetime as dt, json, math, os, sys
import numpy as np, pandas as pd, psycopg2
sys.path.insert(0, '/root/ohm_pro_web')
import ohmpro as O

ROOT = '/root/ohm_pro_web'
STATE, OUT = ROOT + '/data/paper.json', ROOT + '/data/pro.json'
sys.path.insert(0, '/root/trade-execution-webhook')


def capital():
    try:
        for ln in open('/root/trade-execution-webhook/.env'):
            if ln.startswith('CAPITAL='):
                return float(ln.split('=', 1)[1].strip().strip('"\''))
    except Exception:
        pass
    return 400000.0


def tick_of(sym):
    import contextlib, io
    try:
        import tick_utils
        with contextlib.redirect_stdout(io.StringIO()):
            return float(tick_utils.get_tick_size(sym) or 0.05)
    except Exception:
        return 0.05


def up_tick(p, t):
    return round(math.floor(p / t + 1e-9) * t + t, 2)


def f(x, n=2):
    try:
        x = float(x)
        return round(x, n) if math.isfinite(x) else None
    except Exception:
        return None


def save(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w') as fh:
        json.dump(obj, fh, separators=(',', ':'), default=lambda o: o.item() if hasattr(o, 'item') else str(o))
    os.replace(tmp, path)


def main():
    os.makedirs(ROOT + '/data', exist_ok=True)
    if '--reset' in sys.argv and os.path.exists(STATE):
        os.replace(STATE, STATE + '.bak-' + dt.datetime.now().strftime('%Y%m%d%H%M%S'))
    book = json.load(open(STATE)) if os.path.exists(STATE) else O.new_book(capital())
    conn = psycopg2.connect(**O.DB)
    cal = O.complete_dates(conn)
    i_to = len(cal) - 1
    if book['last_date'] is None:
        i_from = i_to                                     # a new book starts on the latest complete day
        book['start'] = str(pd.Timestamp(cal[i_to]).date())
    else:
        i_from = int(np.searchsorted(cal, np.datetime64(book['last_date']))) + 1
    i_scan = min(i_from, i_to)                            # always scan the latest day for the page
    need = set(book['positions']) | {c['sym'] for c in book['pending']} | {a['sym'] for a in book['sched']}
    load_from = pd.Timestamp(cal[i_scan]) - pd.Timedelta(days=760)
    mkt, cands, bars = O.scan(conn, cal, i_scan, i_to, load_from=load_from, keep_bars=True, need=need)
    O.earnings(conn, cands, cal)
    byday = {}
    for c in cands:
        c['block'] = O.gates(c, mkt)
        byday.setdefault(c['i'], []).append(c)
    B = O.Bars(bars)
    prev = book['last_date']
    for i in range(i_from, i_to + 1):
        d = str(pd.Timestamp(cal[i]).date())
        acts = O.step(book, i, d, B, byday.get(i, []), mkt, prev, whole=True)
        O.log(d, 'actions', len(acts), 'equity', round(book['eq']))
        prev = d
    save(STATE, book)
    write_page(conn, cal, i_to, mkt, byday.get(i_to, []), book)
    conn.close()


def write_page(conn, cal, i, mkt, today, book):
    cap = capital()
    names = dict(pd.read_sql("select symbol, security_name from symbols_meta", conn).values)
    reg = int(mkt['reg'][i])
    buys_on = reg not in (1, 2) and bool(mkt['jr_on'][i])
    P = book['positions']
    free_paper = max(0, O.MAXPOS - len(P))
    rows = []
    for c in sorted(today, key=lambda x: -(x['r252'] if np.isfinite(x['r252']) else -9)):
        rps = c['buy'] - c['stop']
        top = bool(np.isfinite(c['lpct']) and c['lpct'] >= O.TOP_PCT)
        rk = O.RISK_TOP if top else O.RISK
        t = tick_of(c['sym'])
        entry = up_tick(c['buy'], t)
        rps_live = entry - c['stop']
        qty = int(max(0, min(rk * cap // rps_live, O.MAXW * cap // entry))) if rps_live > 0 else 0
        rows.append(dict(s=c['sym'], nm=names.get(c['sym'], ''), ent=O.TYP[c['typ']], entT=c['typ'], buy=f(c['buy']), entry=f(entry),
                         stop=f(c['stop']), stopPct=f(rps / c['buy'] * 100, 2), t2r=f(c['buy'] + 2 * rps), r1y=f(c['r252'] * 100, 1),
                         lpct=f(c['lpct'] * 100, 1) if np.isfinite(c['lpct']) else None, top5=top, riskPct=rk * 100,
                         C=f(c['C'] * 100, 0) if c.get('C') is not None else None, A=f(c['A'] * 100, 0) if c.get('A') is not None else None,
                         ud=f(c['ratio'], 2), mf=f(c['mf60'] / 1e7, 1), tov=f(c['tov'], 1), base=f(c['rng10'] * 100, 1),
                         e21d=f(c['e21d'] * 100, 1), hi=f(c['hi'] * 100, 0), block=c['block'] or ('stop more than 8% away' if rps / c['buy'] > O.MAXSTOP else ''),
                         held=c['sym'] in P, qty=qty, amount=f(qty * entry, 0), riskAmt=f(qty * rps_live, 0)))
    ok = [r for r in rows if not r['block'] and not r['held']]
    for n, r in enumerate(ok):
        r['rank'] = n + 1
        r['take'] = n < free_paper
    sched = {}
    for a in book['sched']:
        sched.setdefault(a['sym'], []).append(a)
    pos = []
    for s, p in sorted(P.items(), key=lambda kv: kv[1]['fill_date']):
        rps = p['fill'] - p['stop']
        nxt = sched.get(s, [])
        pos.append(dict(s=s, nm=names.get(s, ''), fill=f(p['fill']), stop=f(p['stop']), t2r=f(p['fill'] + 2 * rps), last=f(p['last']),
                        e21=f(p.get('e21')), fillDate=p['fill_date'], qty=f(p['sh'] + p['sha'], 0), riskPct=p['risk_pct'] * 100,
                        rNow=f((p['last'] - p['fill']) / rps, 2) if rps > 0 else None, touched=p['touched'], kept=p['kept'],
                        halfSold=p['half_sold'], added=p['added'], value=f((p['sh'] + p['sha']) * p['last'], 0),
                        pnl=f((p['sh'] + p['sha']) * p['last'] + p['proc'] - p['cost'], 0),
                        next=[dict(act=a['act'], why=a['why']) for a in nxt], lastDate=p.get('last_date'),
                        stale=(pd.Timestamp(cal[i]) - pd.Timestamp(p.get('last_date') or p['fill_date'])).days > 7))
    closed = book['closed'][-300:][::-1]
    Rs = np.array([x['R'] for x in book['closed']]) if book['closed'] else np.array([])
    eq0 = book['curve'][0][1] if book['curve'] else cap
    meta = dict(asof=str(pd.Timestamp(cal[i]).date()), generated=dt.datetime.now().isoformat(timespec='seconds'),
                phase=O.PH[reg], breadth=f(mkt['breadth'][i] * 100, 1), niftyAbove200=int(mkt['nifty'][i]),
                jrClose=f(mkt['jr_c'][i]), jrSma50=f(mkt['jr_m'][i]), jrOn=bool(mkt['jr_on'][i]), buysOn=buys_on,
                capital=cap, maxPos=O.MAXPOS, risk=O.RISK * 100, riskTop=O.RISK_TOP * 100, cashYield=O.CASH_YIELD * 100,
                candidates=len(rows), passing=len(ok), freePaper=free_paper,
                paper=dict(start=book['start'], equity=f(book['eq'], 0), cash=f(book['cash'], 0), startEq=f(eq0, 0),
                           ret=f((book['eq'] / eq0 - 1) * 100, 2) if eq0 else None, open=len(P), pending=len(book['pending']),
                           trades=int(len(Rs)), win=f((Rs > 0).mean() * 100, 1) if len(Rs) else None,
                           avgR=f(Rs.mean(), 2) if len(Rs) else None,
                           maxDD=f(_maxdd([x[1] for x in book['curve']]) * 100, 1) if book['curve'] else None))
    save(OUT, dict(meta=meta, candidates=rows, positions=pos, closed=closed, curve=book['curve'][-800:],
                   log=book['fills'][-120:][::-1]))
    O.log('wrote', OUT, 'candidates', len(rows), 'passing', len(ok), 'paper open', len(P))


def _maxdd(e):
    e = np.array(e, float)
    return float(-(e / np.maximum.accumulate(e) - 1).min()) if len(e) else 0.0


if __name__ == '__main__':
    main()
