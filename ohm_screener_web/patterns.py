"""OHM Screener (web) — rule-based chart pattern detection on daily bars.

detect(h, l, c, v) -> list of patterns, newest data last. Each pattern:
  {'t': type, 'piv': pivot, 'dep': depth %, 'wk': length in weeks,
   'ol': [[[bars_ago, price], ...], ...]}   # outline polylines for the chart
Types: vcp is computed in compute.py; here: cup, flat, flag, pennant, htf, asc, dbl.
Rules are simple and NOT backtested; they are a visual shortlist.
Today's bar (index t) is excluded from the base so a breakout today still counts.
"""
import numpy as np


def _r(x):
    return round(float(x), 2)


def _swings(a, k=3, mode='high'):
    out = []
    for i in range(k, len(a) - k):
        w = a[i - k:i + k + 1]
        if (mode == 'high' and a[i] == w.max()) or (mode == 'low' and a[i] == w.min()):
            out.append(i)
    return out


def _fit(y):
    x = np.arange(len(y))
    s, b = np.polyfit(x, y, 1)
    return s, b


def detect(h, l, c, v):
    m = ~(np.isnan(h) | np.isnan(l) | np.isnan(c) | np.isnan(v))
    if m.sum() < 150:
        return []
    # use the contiguous tail without gaps
    idx = np.where(m)[0]
    h, l, c, v = h[idx].astype(float), l[idx].astype(float), c[idx].astype(float), v[idx].astype(float)
    n = len(c); t = n - 1
    vol50 = v[max(0, t - 50):t].mean()
    P = []
    ago = lambda i: int(t - i)

    def add(typ, piv, dep, bars, ol):
        if piv <= 0 or not (0.85 * piv <= c[t] <= 1.06 * piv):
            return
        P.append({'t': typ, 'piv': _r(piv), 'dep': round(float(dep) * 100, 1), 'wk': round(bars / 5, 1),
                  'ol': [[[ago(i), _r(p)] for i, p in line] for line in ol]})

    # ---- flat base: 25-60 bars in a <=15% range after a 20%+ advance ----
    for N in range(60, 24, -5):
        s = t - N
        if s < 80:
            continue
        hi, lo = h[s:t].max(), l[s:t].min()
        if (hi - lo) / hi <= 0.15 and c[s] / c[s - 80:s].min() >= 1.2:
            add('flat', hi, (hi - lo) / hi, N, [[(s, hi), (t - 1, hi), (t - 1, lo), (s, lo), (s, hi)]])
            break

    # ---- cup & handle ----
    for hl_len in range(20, 4, -1):   # longest handle first, so the pivot is the rim high
        s = t - hl_len                     # handle starts at the right rim
        if s - 35 < 1:
            continue
        hh = h[s - 2:t].max(); hlow = l[s:t].min()
        if (hh - hlow) / hh > 0.12 or int(np.argmax(h[s - 2:t])) > 4:
            continue
        a = max(0, s - 300); b = s - 35
        i = a + int(np.argmax(h[a:b])); left = h[i]
        if h[i + 1:s].max() > left * 1.03:
            continue
        right = h[max(i + 1, s - 5):s + 3].max()
        if not (0.90 * left <= right <= 1.05 * left):
            continue
        bi = i + int(np.argmin(l[i:s])); bot = l[bi]
        dep = 1 - bot / left
        if not (0.12 <= dep <= 0.35):
            continue
        pos = (bi - i) / max(1, s - i)
        if not (0.25 <= pos <= 0.75):
            continue
        if hlow < bot + 0.5 * (left - bot):
            continue
        if i > 30 and c[i] / c[max(0, i - 120):i].min() < 1.25:
            continue
        if v[s:t].mean() >= vol50 * 1.1:
            continue
        hli = s + int(np.argmin(l[s:t]))
        add('cup', hh, dep, t - i, [[(i, left), (bi, bot), (s, h[s]), (hli, hlow), (t - 1, h[t - 1])]])
        break

    # ---- high tight flag: +60% in <=40 bars, then <=25% pullback for 3-25 bars ----
    htf = False
    for p in range(t - 3, t - 26, -1):
        if p - 40 < 0:
            break
        if h[p] < h[p - 40:t].max():
            continue
        base = l[p - 40:p].min(); bi = p - 40 + int(np.argmin(l[p - 40:p]))
        if h[p] / base - 1 < 0.60:
            continue
        lo = l[p + 1:t].min()
        dd = (h[p] - lo) / h[p]
        if 0.05 <= dd <= 0.25:
            li = p + 1 + int(np.argmin(l[p + 1:t]))
            add('htf', h[p:t].max(), dd, t - bi, [[(bi, base), (p, h[p])], [(p, h[p]), (t - 1, h[p])], [(li, lo), (t - 1, lo)]])
            htf = True
        break

    # ---- bull flag / pennant: pole +15% in <=10 bars, then 3-15 bar drift giving back <50% ----
    if not htf:
        for b in range(t - 3, max(t - 16, 11), -1):
            if h[b] < h[b - 10:t].max():
                continue
            a = b - 10 + int(np.argmin(l[b - 10:b]))
            if b - a < 2 or h[b] / l[a] - 1 < 0.15:
                continue
            fh, fl = h[b + 1:t], l[b + 1:t]
            if len(fh) < 3:
                continue
            retr = (h[b] - fl.min()) / (h[b] - l[a])
            if retr > 0.5 or v[a:b + 1].mean() <= v[b + 1:t].mean():
                continue
            hs, hb = _fit(fh); ls, lb = _fit(fl)
            k = len(fh) - 1
            piv = min(h[b], hb + hs * (k + 1))
            typ = 'pennant' if (hs < 0 and ls > 0) else 'flag'
            add(typ, max(piv, fh[-1]), (h[b] - fl.min()) / h[b], t - a,
                [[(a, l[a]), (b, h[b])], [(b + 1, hb), (t - 1, hb + hs * k)], [(b + 1, lb), (t - 1, lb + ls * k)]])
            break

    # ---- ascending triangle: flat top (2+ touches within 2%) and rising lows ----
    for N in (60, 45, 30, 20):
        s = t - N
        if s < 0:
            continue
        H, L = h[s:t], l[s:t]
        R = H.max()
        if (R - L.min()) / R > 0.25:
            continue
        pk = [j for j in _swings(H, 3, 'high') if H[j] >= 0.98 * R]
        pk = [j for n_, j in enumerate(pk) if n_ == 0 or j - pk[n_ - 1] >= 5]
        tr = _swings(L, 3, 'low')
        if len(pk) < 2 or len(tr) < 2:
            continue
        lows = [L[j] for j in tr]
        if not all(lows[k + 1] > lows[k] for k in range(len(lows) - 1)) or lows[0] > R * 0.95:
            continue
        add('asc', R, (R - lows[0]) / R, N, [[(s + pk[0], R), (t - 1, R)], [(s + tr[0], lows[0]), (s + tr[-1], lows[-1])]])
        break

    # ---- double bottom (W): two lows within ~5%, middle peak >=10% above ----
    for N in (120, 90, 60, 40):
        s = t - N
        if s < 0:
            continue
        cut = s + int(N * 0.6)
        i1 = s + int(np.argmin(l[s:cut])); L1 = l[i1]
        if i1 + 10 >= t - 2:
            continue
        i2 = i1 + 10 + int(np.argmin(l[i1 + 10:t - 1])); L2 = l[i2]
        if not (0.97 <= L2 / L1 <= 1.05):
            continue
        im = i1 + int(np.argmax(h[i1:i2 + 1])); M = h[im]
        if M / L1 - 1 < 0.10 or c[t] < L2 * 1.03:
            continue
        i0 = s + int(np.argmax(h[s:i1 + 1]))
        if h[i0] < M:
            continue
        add('dbl', M, M / L1 - 1, t - i0, [[(i0, h[i0]), (i1, L1), (im, M), (i2, L2), (t - 1, c[t - 1])]])
        break
    order = {'cup': 0, 'htf': 1, 'flag': 2, 'pennant': 3, 'asc': 4, 'dbl': 5, 'flat': 6}
    P.sort(key=lambda p: order.get(p['t'], 9))
    return P
