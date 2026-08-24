# Risk Audit & Deployment Sign-off — WB-Composite + INDEX_TF Stack
**Principal Quantitative Risk Officer review · 2026-08-17**

Audit method: claims in `QUANT_RESEARCH_PHASE2.md` were re-verified against the
underlying trade logs and database, not taken at face value. Three new audit
computations were run (blend underwater duration, conditional tail correlation,
open-exposure reconstruction) plus a universe-integrity query. Findings below
include one **critical** defect not surfaced anywhere in the research to date.

---

## 0. CRITICAL FINDING (discovered during audit): survivorship bias in the universe

`symbols_meta` contains **3,262 active symbols and 0 inactive**. The engine's
universe filter (`is_active = true`) is therefore vacuous — but more
importantly, **the database contains no delisted stocks at all**. Every
backtest in this program, including the 15-year baselines, trades only
companies that survived to 2026.

Why this is the single largest number-inflating defect in the stack:

- The strategy is a **small-cap breakout book whose strongest ranking factor
  is illiquidity**. Small illiquid NSE companies delist, get suspended, or go
  to zero at a materially higher rate than large caps. Those names are exactly
  the ones this book would have bought — and they are absent from the data.
- The effect is asymmetric: missing delistings remove *losses*, not wins.
  Academic estimates for survivorship inflation in small-cap momentum-style
  strategies run 1.5–4 CAGR points; for a book deliberately tilted into the
  illiquid tail it is plausibly at the high end.
- No amount of walk-forward or OOS discipline detects this, because every
  slice draws from the same survivor-only universe. It silently taints the
  baseline, the composite ranking validation, the compounding results, and the
  blend frontier alike (INDEX_TF via SYNTH_EQW is *less* exposed — an
  equal-weight index of survivors is biased upward too, but the TF overlay's
  edge is timing, not selection).

**Every CAGR figure in this program should be read as carrying an unknown
upward bias, most plausibly 1–4 points on the WB book.** The *relative*
comparisons (composite vs box_weeks ranking, static vs compounded, blend vs
solo) remain broadly valid because both sides of each comparison share the
bias; the *absolute* levels do not deserve the precision they were reported at.

**Required remediation before live capital:** source delisted-symbol OHLCV
(NSE delisted list + historical bhavcopies), or at minimum re-run the baseline
with a conservative haircut assumption (e.g. assume X% of low-turnover entries
per year go to −1R that were never in the data) to bound the effect.

---

## 1. Liquidity / ADV breach analysis — is the ₹15–20L soft cap sufficient?

**No. A behavioural guideline is not a risk control.** Findings:

a. The report's own numbers show the breach is *systematic*, not incidental:
   20 trades >10% ADV, concentrated exactly where the compounding path puts
   the most capital (7 in 2025). A soft cap "remembered by the operator" will
   be breached by the same mechanism that produced it in the backtest —
   equity grows gradually and no single trade announces itself as the breach.

b. **The capacity analysis measured entries only.** Exits are the worse
   problem: this book's exits are stop-driven (STRUCTURAL_SL / MACD trail
   breach), i.e. they cluster on red days in falling names — precisely when
   the bid side of an illiquid book evaporates. A position that was 8% of ADV
   to enter calmly can be 25%+ of that day's actual volume to exit under
   stress. Slippage on stressed exits is the number the 0.10% model
   understates most, and it was not measured.

c. There is a real design tension the research noted but did not resolve: the
   edge's strongest factor IS low turnover. A blunt `min_turnover` floor
   amputates the factor (and Phase-1 showed hard entry filters cost CAGR).

**Ruling — enforce BOTH, in code, not in documentation:**
- **Absolute per-position notional cap** (recommended: position ≤ 2% of the
  name's ADV, hard-coded in `PositionSizer` with ADV passed in at sizing
  time). This preserves access to illiquid names at bounded size instead of
  excluding them — the correct resolution of the tension in (c). Excess risk
  budget above the cap should spill to the next-ranked candidate.
- **Compounding ceiling in code** (`compounding_max_capital`, e.g. ₹20L on
  the WB book), with overflow directed to INDEX_TF, which is capacity-free at
  this account size. The report's "route incremental capital to INDEX_TF" is
  the right idea; it must be a config field, not an intention.

---

## 2. Multi-strategy structural integrity — is the −0.04 correlation trustworthy?

Verified and decomposed. The average is real but **average correlation is the
wrong statistic for crash risk**, so the audit computed conditional measures:

| Measure | Value | Reading |
|---|---|---|
| Full-sample monthly ρ (n=170) | **−0.048** | as reported |
| ITF mean return in WB's worst-decile months | **+8.36%** | genuinely convex where it matters |
| Conditional ρ *within* that worst decile | **+0.364** | positive — they are not independent inside stress |
| Months with both books < −5% | **0 / 170** | no joint crash month *in sample* |

The structural story (ITF flat during sustained downtrends) is supported. But
three hidden regime risks remain, and the sample cannot exonerate them:

a. **No 2008 in the sample.** Data begins 2011. The only true crash is COVID
   (Mar-2020), and both books show implausibly mild −2.6%/−3.2% months there —
   an artifact of realized-only accounting (see §3). In a gap crash, **both
   books are long the same market simultaneously**: WB holds open positions
   with weekly exits; ITF stays long until its MA crossover confirms, a lag of
   ~2–6 weeks. A −30% fortnight (Oct-2008 shape) hits both at full exposure.
   The −0.04 correlation says nothing about this because the sample contains
   no such fortnight.

b. **The diversifier is built from the book's own assets.** SYNTH_EQW is an
   equal-weight composite of the very universe WB trades. The two books are
   different *signals* on the *same underlying risk factor* (Indian small/mid
   equity beta). This is signal diversification, not asset diversification —
   valuable, but it must not be described or sized as if it were an
   uncorrelated asset class. A true macro shock (currency crisis, market-wide
   circuit days) is one factor draw, not two.

c. **The blend weights were optimised in-sample.** The frontier searched
   weight × exposure on the same 2012–2026 window it reported. The plateau is
   broad (50/50–80/20 all similar), which mitigates this, but the specific
   "16.56% at 40%" carries selection optimism of perhaps ±0.5pt.

**Ruling:** diversification claim is structurally sound for *slow* bear
markets (the mode that actually occurred in-sample) and **unproven for fast
gap crashes**. Size the combined book so that a simultaneous −30% shock on
gross long exposure is survivable. Do not lever the blend (the 1.13× variant)
until the survivorship and mark-to-market issues below are resolved.

---

## 3. Drawdown duration & behavioural risk — does the 60/40 blend fix it?

**No — and the audit numbers are worse than the report implied.** Computed
directly on the blended curves (the report only showed component durations):

| Book | MaxDD | Longest underwater | Next spells |
|---|---|---|---|
| WB static alone | 14.32% | 38.6 mo | 20.9, 11.3 |
| ITF ma150 comp alone | 20.09% | 46.9 mo | 34.7, 33.8 |
| **60/40 blend (recommended)** | 15.84% | **40.0 mo** | 17.2, 12.0 |
| 30/70 blend (18% variant) | 17.75% | **47.3 mo** | 15.5, 14.8 |

The blend reduces drawdown *depth* but **lengthens the longest underwater
spell** (40.0 vs 38.6 months; the 18% variant is worse still at 47.3). Reason:
both books spent overlapping multi-year stretches below their own peaks
(2018-2020 era), and averaging two underwater curves cannot create a new peak.

Additionally, all durations are computed on **realized-only equity**, which
brings up the second audit-discovered defect:

**Mark-to-market bias.** Every equity curve in this program books P&L at exit
date only. Reconstructed open exposure on the static WB book averages
**₹2.85L (71% of capital) and peaks at 101%**. Any month in which those open
positions fell 10–15% unrealized, the *true* account was deeper underwater
than the curve shows. Reported MaxDD figures are **lower bounds**; the COVID
month reading of −2.65% is not credible as a mark-to-market number. True
underwater durations are ≥ the reported ones.

**Behavioural ruling:** a 40-month underwater spell is beyond the abandonment
threshold of nearly all retail operators (industry evidence suggests most
abandon systems inside 12–18 underwater months). This is the stack's most
likely *actual* failure mode — not a market event, but the operator turning
the system off in month 20 of a 40-month spell. Mitigations that are
consistent with the data: (i) report expected underwater duration on the
dashboard so it is a known design parameter, not a surprise; (ii) the 6%
cash yield on ITF's flat periods is real carry that shortens spells — ensure
live implementation actually parks idle cash in a liquid fund; (iii) do NOT
respond by adding the pause-throttle (A5) — the research correctly showed it
lengthens spells to 66 months.

---

## 4. Final sign-off

### Verdict: **CONDITIONAL — NOT approved for live deployment in current state.**

The research program itself is of genuinely high quality — hypothesis-first,
kill criteria pre-registered and honoured, OOS and walk-forward discipline,
engine-truth over harness numbers, and the negative results (rotation,
half-booking, entry filters, pause) were reported rather than buried. The
composite-ranking discovery and the diversification architecture are sound
*relative* findings and I endorse them as such.

But three defects stand between this and live capital:

| # | Vulnerability | Severity | Status |
|---|---|---|---|
| V1 | **Survivorship bias** — zero delisted symbols in universe | Critical | Unaddressed, newly found |
| V2 | **Realized-only equity curves** — MaxDD/duration are lower bounds; open exposure peaks at 101% of capital | High | Unaddressed, newly found |
| V3 | **Untradeable diversifier history** — the 19.17% ITF figure is on SYNTH_EQW, which cannot be bought. The tradeable proxy (NIFTYBEES) has 6.4yr of validated history at 10.26% CAGR. Live blend numbers will differ from reported | High | Known but under-weighted in report |
| V4 | Stressed-exit liquidity never measured (entry-only ADV analysis) | Medium | §1b |
| V5 | Soft compounding cap; no code-level liquidity guard | Medium | §1 ruling |
| V6 | No fast-crash regime in sample; blend weights optimised in-sample | Medium | §2 |

### Conditions for approval
1. **Fix V1 or bound it**: acquire delisted-stock data, or publish all WB
   figures with an explicit survivorship haircut band (−1 to −4 CAGR pts) and
   re-evaluate the 16.6%/17.8% headline claims against it.
2. **Fix V2**: rebuild equity curves mark-to-market weekly (data exists —
   `ohlcv_weekly` covers every open position). Re-report MaxDD and durations.
3. **Implement §1 code controls**: ADV-relative position cap +
   `compounding_max_capital` with INDEX_TF overflow.
4. **Paper-trade 3–6 months** with live slippage capture on the WB book's
   actual fills vs the 0.10% model, specifically logging stop-exit slippage.
5. **Live ITF must be specified as NIFTYBEES/JUNIORBEES** with its own
   (shorter, humbler) validated record — SYNTH_EQW numbers must not appear in
   any live-sizing decision.
6. **Pre-registered kill criteria** for live operation (e.g. "if realized
   12-month return falls below the backtest's 5th percentile, halt and
   review") so the month-20 abandonment decision is made now, calmly, not
   then.

With conditions 1–3 closed and 4 underway, I would approve a staged deployment
(50% of intended capital, static sizing, 60/40 blend) and full deployment on
clean paper-trade reconciliation.

*Everything in this audit was computed from the engine trade logs and database
on 2026-08-17; audit scripts: `/tmp/qr/audit.py`.*
