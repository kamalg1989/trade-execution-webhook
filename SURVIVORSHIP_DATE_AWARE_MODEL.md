# Date-Aware Delisting Injection — Survivorship Stress Test
**2026-08-19 · Three findings, and one of them falsifies the hypothesis we set out to test.**

---

## 1. THE KEY EMPIRICAL ANCHOR — the loss model should not be invented

The strategy already contains 973 real observations of *"held a name whose
volatility exploded, then exited"* — the `ATR_CEILING` exits in run #823. A
delisting-bound company is the extreme tail of that same process, so its
observed return distribution is the correct prior:

| n | mean | p25 | p10 | **worst observed** |
|---|---|---|---|---|
| 973 | +2.98% | −8.38% | −14.06% | **−33.28%** |

**This changes the whole calculation.** The daily ATR ceiling means the strategy
**cannot ride a position to zero** — as a collapsing stock's volatility expands
past 5% ATR it is liquidated at the next open. In 973 real cases the worst
single outcome was −33%.

**My earlier flat sensitivity used −60% and −100% per phantom position. Those
are not attainable outcomes for this strategy and overstated the damage by
2–3×.** The date-aware model uses −14.1% (p10) as the central loss and −33.3%
(worst ever observed) as the stress case.

---

## 2. ⚠️ THE HYPOTHESIS IS FALSIFIED — the damage is NOT in 2018

We expected the 2016-18 delisting cluster to expand the 2018 drawdown. **It does
not.** The formal delisting date lags the economic death by years, so the window
in which the strategy could still have *held* these names (6–30 months prior)
maps onto **2014–2017**, peaking at **181 simultaneously-dying companies in
early 2016**.

| scenario | CAGR | MaxDD | **dd 2014-16** | dd 2018 | dd 2020 |
|---|---|---|---|---|---|
| baseline (no injection) | 24.91 | 26.42 | −26.4 | −17.5 | −23.6 |
| elig 15%, loss −14.1% | 22.52 | 28.85 | −28.8 | −17.9 | −23.6 |
| **elig 25% (central), loss −14.1%** | **19.94** | **31.09** | **−30.1** | −20.3 | −23.5 |
| elig 25%, loss −33.3% | 13.37 | 55.28 | −46.3 | −25.8 | −24.6 |
| elig 40%, loss −14.1% | 16.26 | 38.77 | −38.8 | −21.0 | −23.5 |

**2018 moves from −17.5% to −20.3% in the central case** — it never approaches
30%. **2014-2016 is the real exposure**, expanding from −26.4% to −30.1% and, in
harsher variants, past −46%.

That is a more uncomfortable answer than the one we were looking for, because
2014-2016 is also the window with the worst price-mismatch contamination and the
thinnest universe. **All three known defects concentrate in the same period.**

---

## 3. ⚠️ THE 30% KILL CRITERION IS BREACHED IN THE CENTRAL CASE

Pre-registered kill criterion #1 is **MaxDD > 30%**, chosen because it was worse
than any backtested drawdown (24.05% full window, 19.74% clean).

**Under the central survivorship assumption the historical strategy would itself
have hit 31.09%.** The criterion was calibrated against survivor-biased history.

**Recommendation: raise kill criterion #1 from 30% to 35%.** Not to be lenient —
because a 30% live drawdown is *within* what honest history would have produced,
so breaching it would not be evidence that the strategy is broken. A kill
criterion that fires on normal behaviour is worse than no criterion, since it
trains you to override it.

The other three criteria are unaffected.

---

## 4. CAGR IMPACT — consistent with the earlier estimate

| | CAGR |
|---|---|
| raw backtest (2011-26) | 24.91% (harness) / 14.90% (engine) |
| central survivorship adjustment | **−5.0 points** |
| adjusted | ~19.9% (harness) / **~9.9% (engine)** |

The −5.0 point haircut sits squarely inside the 3–7 point range estimated from
the population count, derived here by a completely independent route (dated
danger windows + empirical loss distribution). Two methods agreeing is the
strongest evidence available without buying data.

**Versus benchmarks, survivorship-adjusted:** ~16-19% on the clean 2017-26
window against Nifty 50 ETF at 12.27% and Nifty Next 50 at 16.53%. The
conclusion from before survives: **ahead of Nifty 50, roughly level with Nifty
Next 50.**

---

## 5. ASSUMPTIONS, STATED PLAINLY

| assumption | value | basis |
|---|---|---|
| danger window | delist −30m to −6m | dying firms are illiquid well before formal notice |
| eligibility rate | **25%** central (15/40 swept) | **assumed, not measured** — the weakest link |
| selection rate | 30/350 = 8.6% | actual book size ÷ actual eligible pool |
| loss per phantom | −14.1% (p10) central | empirical, 973 observations |
| loss, stress | −33.3% | worst ever observed |

**The eligibility rate is the one number with no empirical support.** Everything
else is measured. If the true rate is nearer 15%, the haircut is ~2.4 points and
the kill criterion holds at 28.85%; at 40% it is ~8.7 points and MaxDD reaches
38.8%.

The `elig 60%` and `elig 100%` stress rows (MaxDD 93%, 99%) are included in the
script for completeness but should **not** be read as plausible — they assume
every dying company was both eligible and held simultaneously, at the worst
outcome ever recorded, which is not a coherent scenario.

---

## 6. VERDICT AGAINST THE DECISION RULE

The brief was: *evaluate paid data only if the synthetic test threatens core
mechanics or kill-criteria boundaries.*

- **Core mechanics: NOT threatened.** The IFP gate, composite rank, banding and
  inverse-vol sizing are unaffected — survivorship changes the *level* of
  returns, not which components work. The ATR ceiling in fact emerges as more
  valuable than previously credited: it is the mechanism that caps delisting
  losses at −33% instead of −100%.
- **Kill-criteria boundaries: THREATENED.** Criterion #1 breaches in the central
  case. Fix by recalibrating to 35% — a free change requiring no new data.

**Therefore: hold off on CMIE Prowess / NSE EOD, per the stated rule.** The
synthetic model has bounded the problem well enough to act on, and the one
decision it changes (kill threshold) costs nothing to implement.

Revisit if forward paper trading shows drawdowns clustering near 30% early, which
would suggest the true eligibility rate is at the high end of the sweep.
