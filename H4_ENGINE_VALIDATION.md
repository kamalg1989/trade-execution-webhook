# H4 Engine Validation — Information Discreteness (runs #801–808)
**2026-08-19 · VERDICT: DO NOT ADOPT. The harness result does not replicate.**

The port is built, deployed, and correct. The *finding* it was built to validate
failed. Run #799 (N=30, no ID) remains the production candidate.

---

## 1. VERDICT FIRST

| Run | N | id_w | lookback | CAGR | MtM MaxDD | Calmar |
|---|---|---|---|---|---|---|
| #799 / #801 | 30 | — | — | 14.90 | 24.05 | **0.62 ← still the best** |
| #802 | 30 | 1.00 | 126 | 14.41 | 25.68 | 0.56 |
| #803 | 25 | — | — | 14.59 | 26.71 | 0.55 |
| #804 | 25 | 1.00 | 126 | 15.18 | 25.91 | 0.59 |
| #805 | 20 | — | — | 15.67 | 29.38 | 0.53 |
| #806 | 20 | 1.25 | 126 | 15.18 | 28.37 | 0.54 |
| #807 | 25 | 1.50 | 126 | 15.88 | 26.62 | 0.60 |
| #808 | 25 | 1.00 | 252 | 15.40 | 27.12 | 0.57 |

**No configuration beats the incumbent.** The best new result (#807, Calmar 0.60)
is still below #799's 0.62.

---

## 2. THE PORT IS CORRECT — INERTNESS PROVEN

Run **#801** re-ran #799's exact stored config with the new parameters absent:

```
#799  14.90% CAGR / 24.05% MaxDD / Calmar 0.62
#801  14.90% CAGR / 24.05% MaxDD / Calmar 0.62   ← identical
```

So the refactor (per-factor weight list, LEFT JOIN, 6th factor) changed nothing
by default. The negative result below is a real measurement, not a broken port.
Params also persisted correctly to `backtest_runs` (the failure mode that hid
the compounding bug earlier in this program) — verified in every run row.

---

## 3. WHAT FAILED TO REPLICATE

The harness claim was a **monotone N-interaction**: ID's benefit rises as the
book concentrates. That was the core evidence — an effect largest where the
mechanism predicts it should be largest.

| N | harness ΔCalmar | **engine ΔCalmar** | harness ΔDD | engine ΔDD |
|---|---|---|---|---|
| 30 | +0.01 | **−0.06** | +0.32 | +1.63 |
| 25 | +0.11 | **+0.04** | −2.86 | −0.80 |
| 20 | +0.15 | **+0.01** | −3.63 | −1.01 |

**The monotone pattern is gone.** Engine gains are +0.04 / +0.01 where the
harness promised +0.11 / +0.15 — roughly a 3× overstatement, and non-monotone.

What *did* survive is the mechanism's fingerprint, weakly:
- **Drawdown falls at both concentrated sizes** (N=25: 26.71→25.91; N=20:
  29.38→28.37) — the right direction, ~25% of the promised magnitude.
- **126d beats 252d in-engine too** (0.59 vs 0.57), matching the harness.
- **The sign is right at N≤25** and wrong at N=30.

That is consistent with ID carrying a small amount of genuine information that
is mostly already priced by the existing 5-factor composite — not with the
substantial crash-protection the harness indicated.

---

## 4. WHY THE HARNESS OVERSTATED IT (AGAIN)

This is the fifth consecutive harness→engine port to lose ground, and the cause
is the same each time:

| | Harness | Engine |
|---|---|---|
| Marks | monthly, frictionless | daily, next-open fills |
| Rebalance | instant at month-end close | per-name orders over following sessions |
| Position cost | turnover × cost on the aggregate | real round-trip per name |
| Sizing | equal-weight mean of forward returns | inverse-vol, ADV-capped, compounding |

A ranking tweak that reshuffles marginal names looks cheap under monthly
aggregate marks and is expensive when every substitution is a real fill. **The
standing rule holds: treat harness deltas as upper bounds and divide by ~3.**

Concentration itself also behaves differently: the harness had N=20 *raising*
CAGR to 26.72% from 24.40%; the engine gives 15.67% vs 14.90% but pays 5.3
extra drawdown points for it. Under realistic fills, concentration is a worse
trade than the harness suggested, so the defect H4 was supposed to repair is
larger than H4's repair.

---

## 5. WHAT WAS BUILT (retained, inert)

All strictly additive; nothing in BAU touched; all services verified `active`.

1. **`stock_information_discreteness`** (new table, `market_data`) — 5,571,762
   rows, 3,084 symbols, 2011-01-03 → 2026-08-18, `id_126` complete, `id_252`
   5,143,474. **511 MB** (disk at 38%, 30 GB free). Verified against the
   Mac-side source on 9 spot-checks — exact match.
   *Deliberately a separate table, not new columns on `stock_indicators`, so the
   daily `custom-screener-compute` job is untouched and this is droppable with
   one statement:* `DROP TABLE stock_information_discreteness;`
2. **`positional_engine.py`** — `pos_id_score_w` (0 = inert), `pos_id_lookback`
   (126|252). Scoring refactored to an explicit per-factor **weight list** so
   adding a factor can no longer silently reweight an existing one (the old code
   keyed the base-range weight to hard-coded index 4).
3. **`app/routers/backtest.py`** — both config fields, plus the INSERT column
   list and placeholders.
4. **`backtest_runs`** — `pos_id_score_w real`, `pos_id_lookback integer`,
   both nullable.

The port is worth keeping even though the hypothesis failed: it is inert, and
it makes the ID factor available for free if a future test wants it in
combination with something else.

---

## 6. RECOMMENDATION

1. **Do not adopt H4.** Keep preset #19 / run #799 (N=30, no ID) as the
   production candidate at 14.90% / 24.05% / 0.62.
2. **Do not adopt concentration** (N=20/25) — it costs 2.7–5.3 drawdown points
   for 0.3–0.8 CAGR points in the engine.
3. **H1 and H4 are both now closed.** Of the five hypotheses proposed, two are
   tested and negative. H3 (Barroso with the corrected 126-day *daily*
   estimator) is the strongest remaining candidate and now has the daily-return
   substrate it needs already built.
4. **Standing methodology change worth making:** stop promoting on harness
   Calmar. The harness has now mispriced five consecutive findings in the same
   direction. Screen in the harness for *sign and shape*, then require engine
   validation before any claim is recorded as a result.

## Caveats

- Survivorship bias unchanged; all CAGR figures remain upper bounds.
- ID is computed from unadjusted closes with a ±75%/day sanity filter; a
  corporate action could inject a spurious "jump" and mislabel a name as
  discrete. This weakens H4's measurement slightly but cannot explain a 3×
  shortfall.
- `id_252` coverage is 92% of rows; missing values are neutralised at the
  cross-sectional mean (no look-ahead, mild dilution).
