"""OHM /ohm/ — Coffee-can fundamentals feed (course: What to Buy, Part 2).

Source: screener.in public company pages (robots.txt allows /company/*).
Consolidated page first; falls back to standalone when consolidated lacks
3-year history. Stores one row per symbol in market_data.ohm_fundamentals.
Daily via ohm-web-fundamentals.timer. Polite: 1 request at a time, ~1.5 s apart.

Fields (course screen "Coffee can stocks with a twist"):
  sales_g     latest full-year sales growth %
  sales_g3    3-year compounded sales growth %
  profit_g    latest full-year net profit growth %
  profit_g3   3-year compounded profit growth %
  qprofit_yoy latest quarter net profit vs same quarter last year %
  roce, roe   latest %;  roce3y = average of last 3 yearly ROCE %
  mcap        market cap, Rs crore
"""
import json, random, re, sys, time
import psycopg2, requests

DB = dict(host='localhost', dbname='market_data', user='postgres', password='postgres')
UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36'}
UNIVERSE = '/root/ohm_screener_web/data/latest.json'

DDL = """create table if not exists ohm_fundamentals(
 symbol text primary key, mcap double precision, roce double precision, roe double precision,
 roce3y double precision, sales_g double precision, sales_g3 double precision,
 profit_g double precision, profit_g3 double precision, qprofit_yoy double precision,
 basis text, ok boolean, err text, fetched_at timestamptz default now())"""


def num(x):
    if x is None: return None
    x = x.replace(',', '').replace('%', '').replace('&nbsp;', '').strip()
    try: return float(x)
    except ValueError: return None


def table(s, sec):
    i = s.find(f'id="{sec}"')
    if i < 0: return None
    t = s[i:s.find('</table>', i)]
    rows = {}
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', t, re.S):
        cells = [re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', c)).replace('&nbsp;', '').replace('+', '').strip()
                 for c in re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)]
        if cells: rows[cells[0]] = cells[1:]
    hdr = [re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', h)).strip() for h in re.findall(r'<th[^>]*>(.*?)</th>', t, re.S)][1:]
    return hdr, rows


def ranges(s, title):
    i = s.find(title)
    if i < 0: return {}
    t = s[i:s.find('</table>', i)]
    return {k.strip(': '): num(v) for k, v in re.findall(r'<td>([^<]*)</td>\s*<td>([^<]*)</td>', t)}


def growth(a, b):
    if a is None or b is None or b <= 0: return None
    return round((a / b - 1) * 100, 1)


def parse(s):
    top = {k.strip(): v for k, v in re.findall(
        r'<span class="name">\s*(.*?)\s*</span>.*?<span class="number">([^<]*)</span>', s, re.S)}
    out = dict(mcap=num(top.get('Market Cap')), roce=num(top.get('ROCE')), roe=num(top.get('ROE')))
    sg, pg = ranges(s, 'Compounded Sales Growth'), ranges(s, 'Compounded Profit Growth')
    out['sales_g3'], out['profit_g3'] = sg.get('3 Years'), pg.get('3 Years')
    pl = table(s, 'profit-loss')
    if pl:
        hdr, r = pl
        yrs = [i for i, h in enumerate(hdr) if re.match(r'^[A-Z][a-z]{2} \d{4}', h)]
        sales, np_ = r.get('Sales') or r.get('Revenue'), r.get('Net Profit')
        if len(yrs) >= 2 and int(hdr[yrs[-1]][4:8]) - int(hdr[yrs[-2]][4:8]) == 1:
            a, b = yrs[-1], yrs[-2]
            if sales: out['sales_g'] = growth(num(sales[a]), num(sales[b]))
            if np_: out['profit_g'] = growth(num(np_[a]), num(np_[b]))
        yr = {i: int(hdr[i][4:8]) for i in yrs}
        b3 = [i for i in yrs if yrs and yr[i] == yr[yrs[-1]] - 3]
        if b3:  # 3Y CAGR fallback from the annual table (exactly 3 years apart)
            a, b = yrs[-1], b3[0]
            def cagr(row):
                x, y = num(row[a]), num(row[b])
                return round(((x / y) ** (1 / 3) - 1) * 100, 1) if x and y and x > 0 and y > 0 else None
            if out.get('sales_g3') is None and sales: out['sales_g3'] = cagr(sales)
            if out.get('profit_g3') is None and np_: out['profit_g3'] = cagr(np_)
    q = table(s, 'quarters')
    if q:
        npq = q[1].get('Net Profit')
        if npq and len(npq) >= 5: out['qprofit_yoy'] = growth(num(npq[-1]), num(npq[-5]))
    rt = table(s, 'ratios')
    if rt:
        vals = [num(v) for v in (rt[1].get('ROCE %') or []) if num(v) is not None]
        if vals: out['roce3y'] = round(sum(vals[-3:]) / len(vals[-3:]), 1)
    return out


def fetch(sym, sess):
    res = None
    for basis, url in (('consolidated', f'https://www.screener.in/company/{sym}/consolidated/'),
                       ('standalone', f'https://www.screener.in/company/{sym}/')):
        r = None
        for attempt in range(3):
            r = sess.get(url, headers=UA, timeout=25)
            if r.status_code == 429:
                time.sleep(30 * (attempt + 1)); continue
            break
        if r is None or r.status_code != 200:
            continue
        d = parse(r.text); d['basis'] = basis
        if res is None or (res.get('sales_g3') is None and d.get('sales_g3') is not None):
            res = d
        if d.get('sales_g3') is not None and d.get('qprofit_yoy') is not None:
            break
        time.sleep(1.0)
    return res


def main():
    only = sys.argv[1:]
    syms = only or sorted(r['s'] for r in json.load(open(UNIVERSE))['rows'])
    conn = psycopg2.connect(**DB); conn.autocommit = True
    cur = conn.cursor(); cur.execute(DDL)
    sess = requests.Session()
    ok = bad = 0
    t0 = time.time()
    for i, s in enumerate(syms):
        try:
            d = fetch(s, sess); err = None if d else 'not found'
        except Exception as e:
            d, err = None, str(e)[:200]
        d = d or {}
        good = any(d.get(k) is not None for k in ('roce', 'sales_g', 'qprofit_yoy'))
        cur.execute("""insert into ohm_fundamentals(symbol,mcap,roce,roe,roce3y,sales_g,sales_g3,profit_g,profit_g3,qprofit_yoy,basis,ok,err,fetched_at)
            values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
            on conflict(symbol) do update set mcap=excluded.mcap,roce=excluded.roce,roe=excluded.roe,roce3y=excluded.roce3y,
            sales_g=excluded.sales_g,sales_g3=excluded.sales_g3,profit_g=excluded.profit_g,profit_g3=excluded.profit_g3,
            qprofit_yoy=excluded.qprofit_yoy,basis=excluded.basis,ok=excluded.ok,err=excluded.err,fetched_at=now()
            where excluded.ok or not ohm_fundamentals.ok""",
                    (s, d.get('mcap'), d.get('roce'), d.get('roe'), d.get('roce3y'), d.get('sales_g'), d.get('sales_g3'),
                     d.get('profit_g'), d.get('profit_g3'), d.get('qprofit_yoy'), d.get('basis'), good, err))
        ok += good; bad += (not good)
        if i % 50 == 0 or only:
            print(f'[{time.time()-t0:6.0f}s] {i}/{len(syms)} ok={ok} bad={bad} last={s} {d}', flush=True)
        time.sleep(1.2 + random.random() * 0.6)
    print(f'done {len(syms)} ok={ok} bad={bad} in {time.time()-t0:.0f}s', flush=True)
    if len(syms) > 100 and ok < len(syms) * 0.6:
        try:
            sys.path.insert(0, '/root/ohm_screener_web')
            from earnings_if import alert
            alert(f'OHM fundamentals feed: only {ok}/{len(syms)} ok')
        except Exception:
            pass


if __name__ == '__main__':
    main()
