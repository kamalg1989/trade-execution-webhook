"""Turn every raw sweep/campaign result into the markdown tables used by
BACKTEST_REPORT.md.

The inputs are the RESULT lines every driver script prints (harvested into
all_results.json) plus the positional sweep JSONs. Keeping this as a script
rather than hand-copying numbers into the report means the report can be
regenerated after any new run, and no figure in it is a transcription.

Usage:  python3 tables.py {campaigns|sweeps|strategies|pos1|pos2|pos3}
"""
import collections
import json
import sys

DATA = '/tmp/btreport'
d = json.load(open(f'{DATA}/all_results.json'))
YRS = ['2016', '2017', '2018', '2019', '2020', '2021', '2022', '2023', '2024',
       '2025', '2026ytd']


def k(n):
    return f"{n/1000:.0f}k" if abs(n) >= 1000 else f"{n:.0f}"


def campaign_table(fname, years=YRS, valkey='total'):
    rows = d[fname]
    by = collections.defaultdict(dict)
    meta = collections.defaultdict(lambda: collections.defaultdict(float))
    for r in rows:
        by[r['config']][r['window']] = r[valkey]
        meta[r['config']]['trades'] += r.get('trades', 0)
        meta[r['config']]['dd'] = max(meta[r['config']]['dd'], r.get('maxDD', 0))
    ys = [y for y in years if any(y in v for v in by.values())]
    out = ['| Config | ' + ' | '.join(y.replace('ytd', '') for y in ys)
           + ' | **Total** | +ve | Trades |',
           '|' + '---|' * (len(ys) + 4)]
    for cfg in sorted(by):
        vals = [by[cfg].get(y) for y in ys]
        tot = sum(v for v in vals if v is not None)
        pos = sum(1 for v in vals if v and v > 0)
        cells = ' | '.join(k(v) if v is not None else '—' for v in vals)
        out.append(f"| {cfg} | {cells} | **{k(tot)}** | {pos}/{len(ys)} "
                   f"| {meta[cfg]['trades']:.0f} |")
    return '\n'.join(out)


def two_window_table(fname):
    rows = d[fname]
    by = collections.defaultdict(dict)
    for r in rows:
        by[r['label']][r['window']] = r
    ws = sorted({r['window'] for r in rows})
    out = ['| Variant | ' + ' | '.join(f'{w} P&L | {w} DD' for w in ws) + ' | Trades |',
           '|' + '---|' * (len(ws) * 2 + 2)]
    for lbl in sorted(by):
        cells, tr = [], 0
        for w in ws:
            r = by[lbl].get(w)
            if r:
                cells += [k(r['totalPnl']), k(r['maxDrawdown'])]
                tr += r['count']
            else:
                cells += ['—', '—']
        out.append(f"| {lbl} | " + ' | '.join(cells) + f" | {tr} |")
    return '\n'.join(out)


def single_window_table(fname):
    rows = d[fname]
    out = ['| Variant | Trades | Win% | Net P&L | avg R | maxDD |',
           '|---|---|---|---|---|---|']
    for r in sorted(rows, key=lambda r: -r['totalPnl']):
        out.append(f"| {r['label']} | {r['count']} | {r['winRate']} "
                   f"| {k(r['totalPnl'])} | {r['avgR']} | {k(r['maxDrawdown'])} |")
    return '\n'.join(out)


def strategy_table(fname):
    rows = d[fname]
    out = ['| Year | Trades | Win% | Realized | Unreal | Total | maxDD | avg hold |',
           '|---|---|---|---|---|---|---|---|']
    tot = 0
    for r in sorted(rows, key=lambda r: r['window']):
        tot += r['total']
        out.append(f"| {r['window']} | {r['trades']} | {r['winRate']} "
                   f"| {k(r['realized'])} | {k(r['unrealized'])} | **{k(r['total'])}** "
                   f"| {k(r.get('maxDD', 0))} | {r.get('avgHold', '—')} |")
    out.append(f"| **ALL** | | | | | **{k(tot)}** | | |")
    return '\n'.join(out)


def pos_sweep_table(path, keyfn, label, top=None):
    rows = json.load(open(path))
    by = collections.defaultdict(list)
    for r in rows:
        by[keyfn(r)].append(r)
    out = [f'| {label} | ' + ' | '.join(y.replace('ytd', '') for y in YRS)
           + ' | **Total** | +ve | maxDD | Trades |',
           '|' + '---|' * (len(YRS) + 5)]
    agg = {}
    for cfg, rs in by.items():
        m = {r['window']: r for r in rs}
        vals = [m[y]['total'] if y in m else None for y in YRS]
        agg[cfg] = (sum(v for v in vals if v is not None), vals,
                    sum(1 for v in vals if v and v > 0),
                    max(r['maxDDpct'] for r in rs), sum(r['trades'] for r in rs))
    for cfg in sorted(agg, key=lambda c: -agg[c][0])[:top]:
        tot, vals, pos, dd, tr = agg[cfg]
        cells = ' | '.join(k(v) if v is not None else '—' for v in vals)
        out.append(f"| {cfg} | {cells} | **{k(tot)}** | {pos}/11 | {dd:.0f}% | {tr} |")
    return '\n'.join(out)


if __name__ == '__main__':
    which = sys.argv[1]
    if which == 'campaigns':
        for f, title in [('campaign.log', 'v1'), ('campaign2.log', 'v2'),
                         ('campaign3.log', 'v3'), ('campaign4.log', 'v4'),
                         ('campaign5.log', 'v5')]:
            print(f'\n### Campaign {title} ({f})\n')
            print(campaign_table(f))
    elif which == 'sweeps':
        for f in ['gate_sweep.log', 'stage2_sweep.log']:
            print(f'\n### {f}\n')
            print(single_window_table(f))
        for f in ['breadth_sweep.log', 'contraction_sweep.log', 'sizing_sweep.log',
                  'trail_sweep.log', 'stage_revalidate.log']:
            print(f'\n### {f}\n')
            print(two_window_table(f))
    elif which == 'strategies':
        for f in ['pos1.log', 'mr1.log', 'mr2.log']:
            print(f'\n### {f}\n')
            print(strategy_table(f))
    elif which == 'pos1':
        print(pos_sweep_table(
            f'{DATA}/possweep.json',
            lambda r: f"{r['momentum'].replace('pct_chg_','')} / {r['rebalance']}d / top{r['top_n']}",
            'momentum / rebalance / top-N', top=15))
    elif which == 'pos2':
        print(pos_sweep_table(
            f'{DATA}/possweep_ma.json',
            lambda r: f"{r['momentum'].replace('pct_chg_','')} / {r['sl_mode']}{r['sl_pct'] or ''}",
            'momentum / stop'))
    elif which == 'pos3':
        print(pos_sweep_table(f'{DATA}/possweep_dw.json',
                              lambda r: r['label'], 'variant'))
