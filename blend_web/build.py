#!/usr/bin/env python3
"""/blend/ data builder (read-only). Writes /root/blend_web/data/data.json (served by api.py with the dashboard key) for the A5 + OHM Pro dashboard.
A5 numbers come from the read-only A5 MCP helper (same figures as the Supervisor tools);
OHM Pro from /root/ohm_pro_web/data (pro.json, live_book.json, live_orders.jsonl). Never places anything.
Run: venv/bin/python /root/blend_web/build.py   (timer blend-build, every 10 min 07:00-23:59)"""
import datetime as dt, json, os, subprocess

PY = '/root/trade-execution-webhook/venv/bin/python'
HELPER = '/root/a5mcp/helper.py'
OHM = '/root/ohm_pro_web'
OUT = '/root/blend_web/data/data.json'


def helper(cmd, args=None, timeout=120):
    try:
        p = subprocess.run([PY, HELPER, cmd, json.dumps(args or {})], capture_output=True, text=True, timeout=timeout)
        return json.loads(p.stdout) if p.returncode == 0 and p.stdout.strip() else {'error': p.stderr[-400:]}
    except Exception as e:
        return {'error': str(e)}


def jload(path, default=None):
    try:
        return json.load(open(path))
    except Exception:
        return default


def a5_curve():
    import psycopg2
    with psycopg2.connect('postgresql://postgres:postgres@localhost:5432/trading_platform') as c, c.cursor() as cur:
        cur.execute("SELECT d, sum(equity) FROM a5l_equity GROUP BY d HAVING count(*)=3 ORDER BY d")
        return [[str(d), round(float(e))] for d, e in cur.fetchall()]


def main():
    now = dt.datetime.now()
    book = helper('book')
    pos = helper('positions')
    sched = helper('schedule')
    ev = helper('events', {'limit': 12})
    br = helper('breadth')
    curve = a5_curve()
    if isinstance(book, dict) and book.get('total_equity'):
        today = str(dt.date.today())
        if not curve or curve[-1][0] < today:
            curve.append([today, round(book['total_equity'])])
    sch = {t['tranche']: t for t in (sched.get('tranches') or [])} if isinstance(sched, dict) else {}
    tranches = []
    for t in (book.get('tranches') or []):
        s = sch.get(t['tranche'], {})
        tranches.append(dict(n=t['tranche'], eq=t['equity'], cash=t['cash'], start=t['start_capital'], ret=t['return_pct'],
                             nOpen=t['n_open'], since=s.get('sessions_since'), need=s.get('sessions_needed'),
                             toGo=s.get('sessions_to_go'), vm=s.get('vlb21_multiplier')))
    positions = [dict(t=p['tranche'], sym=p['symbol'], qty=p['qty'], avg=p['avg_price'], last=p['last_close'],
                      val=p['value'], pnl=p['pnl_pct'], since=p['entry_date']) for p in (pos if isinstance(pos, list) else [])]
    a5 = dict(total=book.get('total_equity'), peak=book.get('high_water'), dd=book.get('drawdown_pct'),
              kill=book.get('kill_switch_level'), halted=book.get('halted'), haltReason=book.get('halt_reason'),
              tranches=tranches, curve=curve, positions=positions,
              events=[dict(at=e['at'], t=e.get('tranche'), kind=e['kind'], detail=(e.get('detail') or '')[:160])
                      for e in (ev if isinstance(ev, list) else [])],
              breadth=br if isinstance(br, dict) and 'error' not in br else None, signalDate=sched.get('latest_signal_date'))

    pro = jload(OHM + '/data/pro.json', {}) or {}
    m = pro.get('meta', {})
    lb = jload(OHM + '/data/live_book.json', {}) or {}
    acts = []
    try:
        for ln in open(OHM + '/data/live_orders.jsonl').readlines()[-40:]:
            r = json.loads(ln)
            if r.get('kind') in ('placed', 'fill', 'shadow_fill', 'guard_cancel'):
                acts.append(dict(at=r.get('ts'), kind=r['kind'], sym=r.get('sym'), act=r.get('act'), qty=r.get('qty'), px=r.get('px')))
    except FileNotFoundError:
        pass
    plan = lb.get('plan') or {}
    ohm = dict(asof=m.get('asof'), generated=m.get('generated'), phase=m.get('phase'), breadth=m.get('breadth'),
               buysOn=m.get('buysOn'), jrClose=m.get('jrClose'), jrSma50=m.get('jrSma50'), jrOn=m.get('jrOn'),
               passing=m.get('passing'), maxPos=m.get('maxPos', 20),
               mode=lb.get('mode'), eq=lb.get('eq'), cash=lb.get('cash'), peak=lb.get('peak'), start=lb.get('start'),
               startCap=lb.get('start_capital'), liqQty=lb.get('liq_qty', 0), liqLast=lb.get('liq_last'),
               buysBlocked=lb.get('buys_blocked'), curve=lb.get('curve') or [],
               positions=[dict(sym=s, qty=p['qty'], avg=p['fill'], last=p.get('last'), stop=p.get('stop'), since=p.get('fill_date'),
                               val=round(p['qty'] * (p.get('last') or p['fill']), 2),
                               pnl=round(((p.get('last') or p['fill']) / p['fill'] - 1) * 100, 2), half=p.get('half_sold'))
                          for s, p in (lb.get('positions') or {}).items()],
               closed=len(lb.get('closed') or []),
               plan=dict(date=plan.get('date'), items=len(plan.get('items') or []), buyValue=plan.get('buy_value'),
                         fired=plan.get('fired'), envelope=plan.get('envelope_ok')),
               halted=os.path.exists(OHM + '/exec/OHMP_HALT'), acts=acts[-10:])
    out = dict(generated=now.isoformat(timespec='seconds'), a5=a5, ohm=ohm,
               errors={k: v.get('error') for k, v in dict(book=book, schedule=sched, breadth=br).items()
                       if isinstance(v, dict) and v.get('error')})
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + '.tmp'
    json.dump(out, open(tmp, 'w'), default=str)
    os.replace(tmp, OUT)
    print(now.isoformat(timespec='seconds'), 'blend data written', 'errors:', out['errors'] or 'none')


if __name__ == '__main__':
    main()
