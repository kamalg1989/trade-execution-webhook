#!/usr/bin/env python3
"""Replay the OHM-PRO paper engine over 2011-2026 with fractional shares and compare it with the research backtest
(/root/research/ohm_improve/results13.pkl, cell CLEAN). Read-only; writes nothing but its own log."""
import pickle, sys
import numpy as np, pandas as pd, psycopg2
sys.path.insert(0, '/root/ohm_pro_web')
import ohmpro as O

END = '2026-11-20'
conn = psycopg2.connect(**O.DB)
cal = O.complete_dates(conn)
end = int(np.searchsorted(cal, np.datetime64(END)))
mkt, cands, bars = O.scan(conn, cal, 0, end)
O.log('candidates', len(cands), 'stocks with bars', len(bars))
O.earnings(conn, cands, cal)
byday = {}
for c in cands:
    c['block'] = O.gates(c, mkt)
    byday.setdefault(c['i'], []).append(c)
O.log('passing', sum(1 for c in cands if not c['block']))
B = O.Bars(bars)
book = O.new_book(400000.0)
prev = None
i0 = int(np.searchsorted(cal, np.datetime64('2011-04-05')))   # backtest's first trading day (cash yield starts after it)
for i in range(end + 1):
    d = str(pd.Timestamp(cal[i]).date())
    O.step(book, i, d, B, byday.get(i, []), mkt, prev, whole=False)
    prev = d if i >= i0 else None
R = pickle.load(open('/root/research/ohm_improve/results13.pkl', 'rb'))
cal13, eq13, res13 = R['cal'], R['out']['CLEAN']['eq'], R['out']['CLEAN']['res']
k = int(np.searchsorted(cal13, np.datetime64(END)))
mine = dict(book['curve'])
for dd in ('2013-12-31', '2016-12-30', '2019-12-31', '2022-12-30', '2024-12-31', END):
    kk = int(np.searchsorted(cal13, np.datetime64(dd)))
    d2 = str(pd.Timestamp(cal13[kk]).date())
    print(f'{d2}: backtest {eq13[kk]:,.0f}  paper engine {mine.get(d2, float("nan")):,.0f}')
bt = {(x[0], str(pd.Timestamp(cal13[x[2]]).date())): (str(pd.Timestamp(cal13[x[3]]).date()), x[4]) for x in res13}
pe = {(x['sym'], x['fill_date']): (x['exit_date'], x['R']) for x in book['closed']}
common = set(bt) & set(pe)
same_exit = sum(bt[k][0] == pe[k][0] for k in common)
same_R = sum(abs(bt[k][1] - pe[k][1]) < 0.01 for k in common)
print(f'trades: backtest {len(bt)}, paper engine {len(pe)}, same (stock, fill date) {len(common)}; '
      f'same exit date {same_exit}; same R (±0.01) {same_R}')
print('only in backtest (first 8):', sorted(set(bt) - set(pe))[:8])
print('only in engine (first 8):', sorted(set(pe) - set(bt))[:8])
import json
json.dump(dict(closed=book['closed']), open('/root/ohm_pro_web/validate_book.json', 'w'))
bt_list = sorted(((str(pd.Timestamp(cal13[x[2]]).date()), x[0]) for x in res13))
pe_list = sorted(((x['fill_date'], x['sym']) for x in book['closed']))
sb, sp = set(bt_list), set(pe_list)
first = min([d for d, s in bt_list if (d, s) not in sp] + [d for d, s in pe_list if (d, s) not in sb])
print('first divergence fill date', first)
for d, s in bt_list:
    if d <= first and (d, s) not in sp: print(' backtest only', d, s)
for d, s in pe_list:
    if d <= first and (d, s) not in sb: print(' engine only', d, s)
ci = int(np.searchsorted(cal, np.datetime64(first)))
for c in byday.get(ci - 1, []) + byday.get(ci - 2, []):
    print(' cand', str(pd.Timestamp(cal[c['i']]).date()), c['sym'], 'block=', c['block'], 'lpct', round(c['lpct'], 3) if np.isfinite(c['lpct']) else None, 'C', c.get('C'), 'A', c.get('A'), 'buy', c['buy'], 'stop', c['stop'])
