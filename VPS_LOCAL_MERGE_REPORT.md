# VPS vs Local Repo — Divergence Report (2026-08-01)

**Bottom line: nothing was actually deleted.** The live VPS and your local Mac repo forked —
each has real, working enhancements the other side never received. This file documents every
difference found by pulling the live files off the VPS and diffing them against
`/Users/kamal/IdeaProjects/trade-execution-webhook` file-by-file. No files were changed as
part of producing this report.

---

## 1. Summary table

| Feature | Local repo | Live VPS | Notes |
|---|---|---|---|
| SL screen table view (sortable columns, card/table toggle) | ✅ has it | ❌ missing | Never deployed |
| Structural-SL history persistence (`structural_sl_history.json`) | ✅ has it | ✅ has it | Deployed earlier today, safe on both sides |
| "Safety −1R" mislabel fix | ✅ has it | ✅ has it | Deployed earlier today, safe on both sides |
| DB-backed structural SL (`trade_journal.py`, `position_db.py`) | ❌ missing | ✅ has it | Built directly on VPS, never pulled down |
| "Exit pending" order tracking (stops re-suggesting Exit) | ❌ missing | ✅ has it | Built directly on VPS |
| R-ladder underwater/negative-R display fix | ❌ missing | ✅ has it | Built directly on VPS |
| Initial-SL default: safety-first vs structural-first | structural-first | **safety-first** | Deliberate VPS-side change, see §3 |
| Push notifications (Settings UI, `push.py`, `utils/pushClient.js`, PWA `public/`) | ❌ missing | ✅ has it | Built directly on VPS |
| `ai_rank_candidates.py` OOM-safety (smaller batches + retries) | ❌ missing | ✅ has it | Fixes a real OOM kill observed 2026-07-24 |
| `trade_journal` capture on buy (`orders.py`) | ❌ missing | ✅ has it | Depends on `trade_journal.py`/`position_db.py` above |

---

## 2. Local-only: SL screen table view

`StopLossTracker.jsx` and `StopLossTrackerMobile.jsx` locally contain a full second view mode
for the SL screen that the VPS does not have at all:

- A `viewMode` toggle (`cards` / `table`) with `LayoutGrid`/`Table2` icon buttons in the header.
- A `PositionsTable` component: sortable columns — Symbol, Qty, Entry, LTP, Struct SL, Safety
  SL, Current SL, R, PnL, PnL%, Status — click a header to sort, click again to reverse.
- This is pure frontend, no backend dependency — safe to layer on top of the VPS version
  without touching any Python.

**This is almost certainly what you noticed as "removed"** — you built/saw this locally but it
was never deployed, so the live site never had it and still doesn't.

---

## 3. VPS-only: DB-backed SL tracking + trade journal

Two new modules exist on the VPS only, at `/root/trade-execution-webhook/trade_journal.py`
and `/root/trade-execution-webhook/position_db.py`. Neither exists in the local repo at all
— not even an older version. They plug into `web_api/routers/sl_engine.py` and
`web_api/routers/orders.py`:

- **`orders.py` `/buy`**: now accepts an optional `recommendation` field (the full stock
  object shown on the recommendation card — target, stopLoss, confidence, reason, regime,
  entryType, IFP/AI fields, etc.) and calls `trade_journal.save_trade_on_buy(...)` to freeze
  it into a `trades` DB row at the moment you click Buy. Also logs sell orders via
  `position_db.log_order(...)`.
- **`sl_engine.py`**: new `_structural_map_with_db()` queries Postgres first —
  `sl_positions` table, then `trades` table (`structural_sl` column, populated by the buy-time
  capture above) — before falling back to the existing Sheet → manual → screener-history chain
  (`_structural_map()` / `_screener_structural_map()`, both untouched and still present).
- **Every SL action** (`place-safety`, `place-at-level`, `structural-exit`, `trail`, `move`,
  `sell-half`) now also calls `position_db.log_order(...)` and `position_db.upsert_snapshot(...)`
  to keep a live position/order audit trail in Postgres.
- **New "pending exit" tracking**: `position_db.pending_order_types(...)` checks whether an
  EXIT or HALF_EXIT order is already resting at the broker for a position, and the
  recommendation becomes `EXIT_PENDING` / `HALF_EXIT_PENDING` (label "⏳ Exit pending — fills
  at open") instead of endlessly re-suggesting "Exit at open" — this was a real bug fix
  (Dhan's forever-orders API doesn't echo back a correlation ID, so without a local log there
  was no way to know an exit was already placed).
- **`get_sl_alerts`** now also calls `position_db.mark_closed_if_absent(...)` at the end to
  auto-close DB rows for positions no longer held.

This is effectively a more complete, working version of the DB-backed approach that was only
*proposed* (never built) in the uncommitted `STRUCTURAL_SL_DB_FIX.md` / `structural_sl_migration.sql`
files sitting in the local repo root — those proposal docs are now superseded by what's
actually running.

**Depends on**: `sl_positions` and `trades` columns/tables already present in the
`trading_platform` Postgres DB (see `CLAUDE.md` §5.2). Whether the `trades` table already has
a `structural_sl` column live, or whether that migration also happened directly on the VPS,
wasn't independently re-verified in this pass — worth confirming before merging.

---

## 4. VPS-only: R-ladder underwater display fix

In `RLadder` (both `StopLossTracker.jsx` and mobile), the local version clamps the progress
bar at 0% for any price below the buy price:

```js
// LOCAL (clamps at Buy)
const toPct = (price) => Math.min(97, Math.max(0, ((price - p.buyPrice) / (rUnit * maxR)) * 100));
```

The VPS version extends the scale below Buy so a losing position shows its true negative-R
position instead of being pinned at the Buy tick, and colors the progress bar red when
underwater:

```js
// VPS (extends below Buy, colors red when underwater)
const minR = Math.min(-1, Math.floor(p.rMultiple ?? 0));
const span = maxR - minR;
const toPct = (price) => Math.min(100, Math.max(0, (((price - p.buyPrice) / rUnit) - minR) / span * 100));
```

This was called out with an inline comment referencing a real symptom: "IKS at -1.41R" was
being visually pinned at the Buy tick, hiding how far underwater it actually was.

---

## 5. VPS-only: initial-SL default flipped (structural → safety)

In `_recommendation()` (`sl_engine.py`), the rule for what SL price to suggest when a position
has **no** stop-loss order yet:

```python
# LOCAL — prefers structural SL if one is known
init = p["structural_sl"] if (p["structural_sl"] and ltp and p["structural_sl"] < ltp) else p["safety_sl"]

# VPS — prefers the −8% safety level
init = p["safety_sl"] if (p["safety_sl"] and ltp and p["safety_sl"] < ltp) else p["structural_sl"]
```

The VPS version has a reasoned comment attached: structural SL is a technical/trailing
reference, but the −8% safety level is what actually protects the position the moment there
are zero resting orders — so it should be the default suggestion for an unprotected position,
not structural.

The related `r_stop` calculation (what "R" is measured against) was also made conditional on
whether a real SL is already placed:

```python
# VPS
r_stop = (structural_sl if structural_sl else safety_sl) if has_sl else (safety_sl if safety_sl else structural_sl)
```

**This is a judgment call, not obviously a bug** — flagging it so you can decide whether to
keep the VPS behavior, revert to the local behavior, or something else, when merging.

---

## 6. VPS-only: push notifications

Entirely new, self-contained feature not in local repo at all:
- `web_api/routers/push.py` (and mirrored at `web-platform/backend/routers/push.py`) —
  subscribe/unsubscribe/test-send endpoints, registered in `main.py`'s router list.
- `web-platform/utils/pushClient.js` — frontend helper (`subscribeToPush`, `unsubscribeFromPush`,
  `getPushStatus`, `sendTestPush`, `isPushSupported`).
- `web-platform/public/` — PWA manifest, service worker (`sw.js`), and icons required for
  push to work as an installable PWA.
- `Settings.jsx` — a full "Push Notifications" card (enable/disable/test buttons, status).
- `ai_rank_candidates.py` — now imports `push_notify` and sends a push with the top-3 picks
  after every AI ranking pass (`_notify_top_picks`), including a fallback push
  ("quant only — AI unavailable") if the AI pass fails entirely.
- Root-level `push_notify.py` and `sl_danger_monitor.py` (backing the `sl-danger-monitor.timer`)
  are also VPS-only — already flagged in `CLAUDE.md` §8.1.

---

## 7. VPS-only: `ai_rank_candidates.py` OOM-safety fix

```python
# LOCAL
BATCH = 25

# VPS
BATCH = 10   # 25-symbol batches measured pushing the :8005 process to 621MB RSS +
             # 1.5GB swap on this 1GB-RAM VPS, which OOM-killed it mid-run on 2026-07-24
```

Also added: 3 retries per batch with a 15s delay (gives a just-OOM-killed uvicorn worker time
to restart via systemd's `Restart=always` before being hit again), and a fallback push
notification with quant-only picks if the AI pass fails outright instead of going silent.

---

## 8. Trivial/cosmetic diffs (no action needed)

- `main.py`: VPS registers the new `push` router in the router list — expected, matches §6.
- `recommendations.py`: `python3` → `sys.executable` for the subprocess call — portability
  fix, functionally identical on this VPS.
- `screen_gpt.py`: `ai_rank.log` path changed from a hardcoded `/tmp/ai_rank.log` to a
  path relative to the script + append mode instead of overwrite — minor logging improvement.

---

## 9. Suggested merge order (not yet executed — awaiting your go-ahead)

1. **Pull VPS-only files down to local first**, so nothing is at risk of being overwritten:
   `trade_journal.py`, `position_db.py`, `push_notify.py`, `sl_danger_monitor.py`,
   `web_api/routers/push.py` → `web-platform/backend/routers/push.py`,
   `web-platform/utils/pushClient.js`, `web-platform/public/*`, and the current (DB-backed)
   `sl_engine.py` / `orders.py` / `Settings.jsx` / `ai_rank_candidates.py` / `screen_gpt.py`.
   Exclude `vapid_private_key.pem` from git (secret).
2. **Re-apply the local-only table view** on top of the pulled-down `StopLossTracker.jsx` /
   `StopLossTrackerMobile.jsx` (the `PositionsTable` component + `viewMode` toggle) — pure
   frontend addition, no backend conflict.
3. **Decide on §5** (initial-SL default) — keep VPS behavior, revert to local, or adjust.
4. Commit the merged state to git, then redeploy backend (`web_api/` — see `CLAUDE.md` §7.1)
   and frontend (build locally, rsync — see `CLAUDE.md` §7.2, never build on the VPS).
5. Verify `sl_positions`/`trades` DB columns referenced by `_structural_map_with_db()` actually
   exist as expected (§3 caveat) before relying on it.

No files were modified to produce this report — say the word and I'll execute whichever parts
of this plan you want.
