# Prompt v3 — Pure Visual Analysis (spec v2 for review — revised per feedback)

Revisions applied: daily chart only (weekly removed) · real COHANCE/TNPETRO
example chart images with dates, cached for reuse · fuller skill instructions ·
input+output tokens cut hard · crisp simplified result format.

---

## 1. Call structure

```
[ CACHED PREFIX — identical every call, billed ~10% after first use ]
  system prompt (methodology + rules)
  tool schema (slimmed)
  user content:
    <COHANCE example chart image — rendered from our DB, our own chart style>
    "STRONG IFP example — COHANCE daily, Jan–Jun 2026: explosive rally
     mid-April 2026 from ~₹260 to ~₹520 on a massive volume spike (tallest
     bars on the chart). May–June: orderly base between ₹400–460, volume
     visibly drying up bar after bar. Shallow pullback — gave back little of
     the April move. Late June: tight coil / inside bar inside the base = the
     entry trigger. volume_pattern=strong, base_structure=strong,
     pullback_depth=strong. Type A. Not extended."
    <TNPETRO example chart image — same window, same renderer>
    "WEAK IFP example — TNPETRO daily, Jan–Jun 2026: no institutional move
     anywhere on the chart. Months of wide, choppy oscillation ~₹84–94 with no
     readable base and no floor. Volume random throughout — spikes with no
     follow-through, no dry-up phase. All three criteria weak. Skipped even
     though it passed the same scanner."
  ── cache breakpoint ──
[ PER-CALL — the only fresh tokens ]
    <candidate daily chart image>
    "Stock: {SYMBOL}. Analyse per your methodology."
```

- Example charts are rendered **once at deploy** from our own OHLCV
  (COHANCE + TNPETRO, Jan 1 – Jun 30 2026, daily, same volume-MA/EMA style as
  every candidate chart) and stored as static assets — visually identical
  format to what the model must judge.
- Anthropic prompt caching covers the whole prefix (system + schema + both
  example images + text). Gemini: prefix resent (input is cheap there;
  implicit caching may also apply).
- **No weekly chart anywhere in v3.** The chart-scope selector is hidden when
  v3 is active.

## 2. System prompt (verbatim, ~600 tokens)

```
You are an expert swing-trading analyst for Indian stocks (NSE/BSE). You rank
breakout candidates by the quality of their Institutional Footprint (IFP) —
the visible evidence that institutions accumulated and are defending a stock.
Institutions can manipulate price but cannot hide volume.

Analyse ONLY the chart image. Read price structure, volume bars versus the
volume-average line, moving averages, and base boundaries yourself. Price
action structure is the primary decision tool.

BASE TYPE (context, not a filter):
A = base after uptrend: strong move up (big candles, volume spike), then a
    consolidation base, now breaking out.
B = accumulation after distribution: downtrend/long sideways, quiet
    accumulation at lows, base at the bottom, now breaking out.

RATE THREE IFP CRITERIA (strong / moderate / weak):
1. volume_pattern (most important): strong = big spike on the initial move,
   volume dries up during the base; weak = choppy/random volume, no
   distinction between move and base.
2. base_structure: strong = tight orderly consolidation respecting clear
   levels; weak = wide messy chop with no readable structure.
3. pullback_depth: strong = shallow pullback, clear floor, institutions
   defending; weak = deep unstructured pullback giving back most of the move.

EXTENSION: extended=true if the stock has already moved far from its base
without consolidating. Extended = lower priority even with good IFP. A stock
just starting to move off a clean, fresh base is the highest priority.

ENTRY & LEVELS (read visually):
- The entry trigger is a coiling-pattern breakout inside the base — inside bar
  is the primary coil; any tight consolidation counts.
- breakout_level = top of the coil / base high you identify on the chart.
- stop_level = below the low of the inside bar (or the tightest recent coil).
  This stop rule is fixed — never suggest alternatives.

VERDICT:
- SETUP_READY: 2–3 criteria strong, near a clean base breakout, not extended.
- EARLY_STAGE: base still forming, or criteria moderate.
- NOT_READY: criteria weak or structure unreadable.
- AVOID: distribution signs (high-volume churn at highs, lower highs/lows,
  double/triple top, head & shoulders) or a broken base.
Be conservative: mixed evidence = EARLY_STAGE or NOT_READY, not SETUP_READY.
Ties between similar stocks: cleaner base wins; less-extended wins.

Do not comment on exits, position sizing, targets, or indicators other than
price, volume and the moving averages shown. Bollinger-band-like overlays, if
any, are visual reference only.

verdict field: max 2 sentences, specific to what you see on THIS chart (like
"Tight 5-week base on dry volume after the March rally; inside bar at the
pivot" — never generic filler).

Report ONLY via the report_chart_analysis tool.
```

## 3. Result schema — slimmed to the decision essentials

```jsonc
{
  "base_type": "A | B",
  "ifp": {
    "volume_pattern": "strong | moderate | weak",
    "base_structure": "strong | moderate | weak",
    "pullback_depth": "strong | moderate | weak"
  },
  "extended": false,
  "buy_point": { "breakout_level": 1438.0, "stop_level": 1372.0 },
  "recommendation": "SETUP_READY | EARLY_STAGE | NOT_READY | AVOID",
  "confidence": 0.84,
  "verdict": "max 2 sentences, chart-specific"
}
```

**Removed from v2** (per "crisp, too much info not required"): market cycle
phase, base_count 0–4+, patterns array, ifp_verdict evidence, base_quality
reasons, weekly_context, thesis, buy-point type/structure enums. `buy_point`
keeps its shape so verification / outcomes / aftermath work unchanged.
`max_tokens` drops 900 → 400 (typical output ~200–250 tokens).

## 4. Simplified result display

Results table: `Symbol | Type | V S P (three colored chips) | Ext ⚠ | BO / Stop | Verdict badge`
— IFP chips: green=strong, amber=moderate, red=weak. One glance = full read.

Popup: chart + the V/S/P chip row + base type + extended badge + BO/stop with
the independent ✓/⚠ cross-check + the 2-sentence verdict + feedback buttons +
Aftermath tab. Nothing else. (Phase/base-count/pattern chips removed.)

Ranking pass (unchanged from previous spec): one text-only call per batch
returns rank order + top 1–2 picks with a 1-line reason; table ordered by it.
Rules: cleaner base wins, less extended wins, max 2 picks.

## 5. Token & cost budget (per stock)

| | v2 (measured) | v3 (estimated) |
|---|---|---|
| Fresh input | 4,424 | **~1,150** (1 candidate image + 40 text) |
| Cached prefix reads | 0–1,375 | ~3,500 @ 10% price |
| Output | 819 | **~250** (cap 400) |
| Haiku cost | ₹0.75 | **~₹0.25** |
| Sonnet cost | ₹1.7–2.1 | **~₹0.75** |
| Gemini (no cache) | ₹0.15 | ~₹0.15 |

Cache note: Anthropic cache TTL is ~5 minutes, refreshed on use — a batch run
keeps it hot for the whole run. First call of a batch pays a one-time cache
write (~25% surcharge on the prefix).

## 6. What stays outside the prompt (unchanged)

- Gate: computed IFP score for pre-AI cost control (soft gate available).
- Verification: computed pivot/stop vs the AI's visual levels — two
  independent estimates; ⚠ = "one is wrong, check the chart".
- Outcomes/Aftermath: unchanged; v3 rows keyed `prompt_version=v3` so the
  performance table shows v2 vs v3 win rates side by side.
- Hybrid flow, budget cap, store-first reuse: unchanged.
- Rollback: `AI_PROMPT_VERSION=v2` (v2 prompt+schema kept in code).

## 7. Caveats

- Example charts show Jan–Jun 2026 only; if COHANCE/TNPETRO price action is
  ever a poor exemplar (e.g. you find better trades), swapping examples =
  re-render two PNGs + edit two text blocks, bump to v3.1.
- Without base_count/phase, the deck's "base 4+ = danger" heuristic now rests
  on the AVOID rule's distribution signs — watch whether late-stage bases slip
  through and we can re-add base_count if outcomes show it.
- Gemini Flash-Lite gets no prompt-cache discount and is the weakest visual
  analyst — judge v3 on Sonnet/Haiku results first.
```
