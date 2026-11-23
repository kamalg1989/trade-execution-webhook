#!/usr/bin/env python3
"""OHM Screener (web) — quarterly earnings from NSE "Integrated Filing - Financials".

Since the quarter ended Mar-2025 NSE publishes results only under Integrated
Filing, which the old harvest (corporates-financial-results) never sees. This
module fetches those filings from nseindia.in, parses net profit from the XBRL
(same parser as backtest.backfill_fundamentals, imported, not modified) and
stores them in its OWN table `ohm_scr_earnings_if`. It never writes to
earnings_filings / earnings_fundamentals (those feed the A5 combo earn-gate).

Resumable: filings already stored (by seq_id) are skipped.
Usage:
    python earnings_if.py                 # default: last 7 quarters, screener universe
    python earnings_if.py --quarters 2    # nightly top-up (latest 2 quarters)
"""
import argparse, datetime as dt, json, sys, threading, time, queue
from pathlib import Path
import psycopg2

sys.path.insert(0, '/root/canslim')
sys.path.insert(0, '/root/trade-execution-webhook/custom-screener/backend')
from nsein import NSEIN  # noqa: E402  (nseindia.in session)
from backtest.backfill_fundamentals import parse_xbrl, _first_number  # noqa: E402

# Banks / NBFCs (banking taxonomy) label things differently from Ind-AS companies
BANK_PROFIT = ('ProfitLossForThePeriod', 'ProfitLossAfterTaxesMinorityInterestAndShareOfProfitLossOfAssociates',
               'ProfitLossFromOrdinaryActivitiesAfterTax')
BANK_EPS = ('BasicEarningsPerShareAfterExtraordinaryItems', 'BasicEarningsPerShareBeforeExtraordinaryItems')


def parse(path):
    p = parse_xbrl(Path(path))
    if p.get('net_profit') is None:
        txt = Path(path).read_text(errors='ignore')
        p['net_profit'] = _first_number(txt, BANK_PROFIT)
        if p.get('eps_basic') is None:
            p['eps_basic'] = _first_number(txt, BANK_EPS)
        if p.get('revenue') is None:
            p['revenue'] = _first_number(txt, ('Income',))
        p['parse_status'] = 'ok' if p['net_profit'] is not None else 'no_profit'
    return p

UNIVERSE = '/root/ohm_screener_web/data/latest.json'
DL = Path('/root/nsedl/ohmscr')
TYPE = 'Integrated Filing- Financials'
T0 = time.time()


def log(*a):
    print(f'[{time.time() - T0:6.0f}s]', *a, flush=True)


def db():
    return psycopg2.connect(host='localhost', user='postgres', password='postgres', dbname='market_data')


DDL = """
create table if not exists ohm_scr_earnings_if (
  seq_id text primary key,
  symbol text not null,
  period_to date not null,
  period_from date,
  broadcast_date date,
  consolidated text,
  type_sub text,
  audited text,
  xbrl_url text,
  revenue double precision,
  net_profit double precision,
  eps_basic double precision,
  parse_status text,
  fetched_at timestamptz default now()
);
create index if not exists ohm_scr_earnings_if_sym on ohm_scr_earnings_if(symbol, period_to);
"""


def quarter_ends(n):
    """Last n quarter-end dates up to today, newest first."""
    today = dt.date.today()
    qs, y = [], today.year
    while len(qs) < n:
        for m, d in ((12, 31), (9, 30), (6, 30), (3, 31)):
            q = dt.date(y, m, d)
            if q < today and len(qs) < n:
                qs.append(q)
        y -= 1
    return qs


def pdate(s):
    if not s:
        return None
    s = str(s).split(' ')[0]
    for f in ('%d-%b-%Y', '%d-%B-%Y'):
        try:
            return dt.datetime.strptime(s.title(), f).date()
        except ValueError:
            pass
    return None


def list_filings(n, qe):
    out, page = [], 1
    while True:
        url = (f"{n.base_url}/integrated-filing-results?index=equities&period_ended={qe.strftime('%d-%b-%Y')}"
               f"&type={TYPE.replace(' ', '%20')}&page={page}&size=1000")
        for attempt in (1, 2, 3):
            try:
                j = n._req(url).json()
                break
            except Exception as e:
                if attempt == 3:
                    raise
                time.sleep(10)
        data = j.get('data') or []
        out += [x for x in data if x.get('type') == TYPE]
        total = j.get('totalCount') or 0
        if not data or page * 1000 >= total:
            break
        page += 1
        time.sleep(2)
    return out


def worker(wid, q, delay, stats):
    d = DL / f'w{wid}'
    d.mkdir(parents=True, exist_ok=True)
    conn = db(); conn.autocommit = True
    cur = conn.cursor()
    with NSEIN(download_folder=str(d), server=True) as n:
        while True:
            try:
                x = q.get_nowait()
            except queue.Empty:
                break
            path = None
            try:
                path = n.download_document(x['xbrl'])
                p = parse(path)
                stats['ok'] += 1
            except Exception as e:
                p = {'parse_status': f'error:{type(e).__name__}'[:40]}
                stats['bad'] += 1
                time.sleep(15)
            finally:
                if path:
                    Path(path).unlink(missing_ok=True)
            qe = pdate(x.get('qe_Date'))
            pf = None
            if qe:
                m, y = qe.month - 2, qe.year
                if m <= 0:
                    m, y = m + 12, y - 1
                pf = dt.date(y, m, 1)
            cons = (x.get('consolidated') or '').strip()
            cons = 'Non-Consolidated' if cons.lower().startswith('stand') else (cons or None)
            cur.execute("""insert into ohm_scr_earnings_if
                (seq_id, symbol, period_to, period_from, broadcast_date, consolidated, type_sub, audited,
                 xbrl_url, revenue, net_profit, eps_basic, parse_status)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict (seq_id) do update set revenue=excluded.revenue, net_profit=excluded.net_profit, eps_basic=excluded.eps_basic, parse_status=excluded.parse_status, fetched_at=now()""",
                        (x['seq_Id'], x['symbol'].strip().upper(), qe, pf, pdate(x.get('broadcast_Date')), cons,
                         x.get('type_Sub'), x.get('audited'), x['xbrl'], p.get('revenue'), p.get('net_profit'),
                         p.get('eps_basic'), p['parse_status']))
            done = stats['ok'] + stats['bad']
            if done % 200 == 0:
                log(f'progress {done}/{stats["total"]} ok={stats["ok"]} bad={stats["bad"]}')
            time.sleep(delay)
    conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quarters', type=int, default=7)
    ap.add_argument('--workers', type=int, default=3)
    ap.add_argument('--delay', type=float, default=1.5)
    a = ap.parse_args()
    conn = db(); conn.autocommit = True
    cur = conn.cursor(); cur.execute(DDL)
    # done = parsed with a profit, or genuinely empty; failed downloads and old bank rows are retried
    cur.execute("select seq_id from ohm_scr_earnings_if where (parse_status='ok' and net_profit is not null) or parse_status in ('no_data','no_profit')")
    have = {r[0] for r in cur.fetchall()}
    conn.close()
    uni = {r['s'] for r in json.load(open(UNIVERSE))['rows']}
    qs = quarter_ends(a.quarters)
    # C needs the latest quarter and the same quarter a year earlier -> fetch those first
    order = [qs[0]] + ([qs[4]] if len(qs) > 4 else []) + [q for i, q in enumerate(qs) if i not in (0, 4)]
    todo = []
    with NSEIN(download_folder=str(DL), server=True) as n:
        for qe in order:
            rows = list_filings(n, qe)
            new = [x for x in rows if x.get('symbol', '').strip().upper() in uni
                   and x.get('seq_Id') not in have and str(x.get('xbrl', '')).endswith('.xml')]
            log(f'{qe}: {len(rows)} filings, {len(new)} new for screener universe')
            todo += new
            time.sleep(2)
    stats = {'ok': 0, 'bad': 0, 'total': len(todo)}
    q = queue.Queue()
    for x in todo:
        q.put(x)
    ths = [threading.Thread(target=worker, args=(i, q, a.delay, stats)) for i in range(a.workers)]
    for t in ths:
        t.start(); time.sleep(0.7)
    for t in ths:
        t.join()
    log(f'DONE ok={stats["ok"]} bad={stats["bad"]}')


def alert(msg):
    try:
        import urllib.request, urllib.parse
        env = {}
        for ln in open('/root/trade-execution-webhook/.env'):
            if '=' in ln and not ln.startswith('#'):
                k, v = ln.strip().split('=', 1)
                env[k] = v.strip().strip('"').strip("'")
        if env.get('TELEGRAM_TOKEN') and env.get('TELEGRAM_CHAT_ID'):
            data = urllib.parse.urlencode({'chat_id': env['TELEGRAM_CHAT_ID'], 'text': msg}).encode()
            urllib.request.urlopen(f"https://api.telegram.org/bot{env['TELEGRAM_TOKEN']}/sendMessage", data, timeout=15)
    except Exception:
        pass


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        log('FAILED', repr(e))
        alert(f'OHM Screener earnings (Integrated Filing) FAILED: {e!r}')
        raise
