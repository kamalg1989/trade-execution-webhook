"""Invariant tests for portfolio_engine.

These do NOT assert on returns. A test that pins CAGR to a number is a
regression detector for the DATA, not a correctness check for the ENGINE, and it
goes red every time the OHLCV table is extended. What is checked here is the set
of properties that must hold for the reported numbers to mean anything at all —
the accounting identities. Every bug this project has actually hit (the
config-hash cache serving results from a different config, gross_pnl silently
never written, sector caps active by default) produced plausible numbers and no
error, so the only defence is asserting the invariants directly.

The engine is instrumented via a debug hook rather than being re-implemented in
the test, because a test that re-implements the logic only proves the two copies
agree.

Run:  python3 -m pytest backtest/test_portfolio_engine.py -v
      (or:  python3 -m backtest.test_portfolio_engine   for a no-pytest run)
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date

sys.path.insert(0, "/root/trade-execution-webhook")

import pytest

from .portfolio_engine import DEFAULTS, _leg_cost, _metrics, run_portfolio

SHORT = dict(start=date(2018, 1, 1), end=date(2019, 12, 31), capital=400000.0)


# --------------------------------------------------------------- pure helpers

def test_leg_cost_buy_has_stamp_duty_not_dp():
    """Stamp duty is buy-side only; the DP charge is sell-side only. Getting
    these the wrong way round changes cost by ~0.015% per leg, which is small
    enough to hide and large enough to matter over 500 trades."""
    v = 100000.0
    buy = _leg_cost(v, is_sell=False)
    sell = _leg_cost(v, is_sell=True)
    assert buy == pytest.approx(v * 0.100 / 100 + v * 0.0030 / 100 + v * 0.015 / 100)
    assert sell == pytest.approx(v * 0.100 / 100 + v * 0.0030 / 100 + 14.75)


def test_leg_cost_is_never_negative_or_free():
    for v in (1000.0, 50000.0, 1_000_000.0):
        assert _leg_cost(v, True) > 0
        assert _leg_cost(v, False) > 0


def test_metrics_cagr_and_drawdown_on_a_known_curve():
    """A curve that doubles over exactly one trading year must report 100% CAGR,
    and a curve that halves then recovers must report a 50% drawdown."""
    cap = 100.0
    curve = [(date(2020, 1, 1), 100.0)] * 1 + [
        (date(2020, 1, 1), 100.0 + 100.0 * i / 251) for i in range(1, 252)]
    m = _metrics(curve, cap, 0, 0, 0, 0)
    assert m["cagrPct"] == pytest.approx(100.0, abs=2.0)

    halve = [(date(2020, 1, 1), v) for v in (100.0, 50.0, 100.0)]
    m2 = _metrics(halve, cap, 0, 0, 0, 0)
    assert m2["maxDDPct"] == pytest.approx(50.0)


def test_metrics_defaults_are_inert():
    """Every risk control must default to OFF. A control that is on by default
    cannot be measured against a baseline — this is the exact bug that made the
    first sector-cap run byte-identical to the run it was supposed to differ
    from, and it read as the clean finding 'sector caps don't matter'."""
    assert DEFAULTS["vol_mode"] == "none"
    assert DEFAULTS["dd_throttle_at"] == 0
    assert DEFAULTS["max_per_sector_pct"] >= 100
    assert DEFAULTS["max_stocks_per_sector"] >= 20
    assert DEFAULTS["max_per_stock_pct"] >= 100


# ------------------------------------------------------- engine-level, live DB

def _pool():
    from app.db import create_pool
    return create_pool()


@pytest.mark.asyncio
async def test_equity_identity_holds_every_single_day():
    """cash + marked holdings == reported equity, on EVERY session.

    This is the accounting identity the whole report rests on. If it drifts, the
    equity curve — and therefore CAGR, maxDD and ulcer — is fiction."""
    pool = await _pool()
    try:
        breaches = []
        m = await run_portfolio(pool, **SHORT, _audit=breaches.append)
    finally:
        await pool.close()
    assert m, "engine returned nothing"
    bad = [b for b in breaches if abs(b["equity"] - (b["cash"] + b["held"])) > 0.01]
    assert not bad, f"{len(bad)} days where cash+holdings != equity, first: {bad[:1]}"


@pytest.mark.asyncio
async def test_cash_never_goes_negative():
    """No leverage is intended. A negative cash balance means the engine bought
    with money it did not have, which silently inflates returns."""
    pool = await _pool()
    try:
        audit = []
        await run_portfolio(pool, **SHORT, _audit=audit.append)
    finally:
        await pool.close()
    neg = [a for a in audit if a["cash"] < -0.01]
    assert not neg, f"negative cash on {len(neg)} days, first: {neg[:1]}"


@pytest.mark.asyncio
async def test_position_size_tracks_current_equity_not_initial_capital():
    """Sizing must compound: every slot is CURRENT equity / top_n, recomputed at
    each rebalance. If slots were sized off the initial Rs.4L the book would stop
    growing, and the 'continuous portfolio' framing would be a lie — it would
    behave like the annual resets it was built to replace.

    This asserts the sizing rule DIRECTLY, at each rebalance.

    An earlier version of this test instead doubled the starting capital and
    checked the final equity roughly doubled. That proves nothing: an engine
    that wrongly sized every slot from the INITIAL capital scales just as
    linearly and passes. Linear scaling is a consequence of BOTH the correct and
    the incorrect rule, so it cannot distinguish them. Only comparing the slot
    against the equity at that same moment can."""
    pool = await _pool()
    try:
        rebals = []
        await run_portfolio(pool, **SHORT, _audit_rebal=rebals.append)
    finally:
        await pool.close()
    assert len(rebals) >= 4, f"only {len(rebals)} rebalances to check"

    for r in rebals:
        expected = r["equity"] / r["top_n"]
        assert r["slot"] == pytest.approx(expected, rel=1e-9), (
            f"slot {r['slot']:.2f} != equity/top_n {expected:.2f} on {r['day']}")

    # ...and the equity it is sized from must actually MOVE, otherwise the
    # assertion above is trivially satisfied by a book that never changed value
    # and the test would still pass against a hard-coded initial-capital rule.
    equities = [r["equity"] for r in rebals]
    cap = SHORT["capital"]
    assert max(equities) != min(equities), "equity never changed across rebalances"
    assert any(abs(e - cap) / cap > 0.02 for e in equities), (
        "equity never moved >2% from the initial capital, so 'sized off current "
        "equity' and 'sized off initial capital' are indistinguishable here")


@pytest.mark.asyncio
async def test_slot_size_respects_the_per_stock_cap_when_active():
    """With the cap active the slot is min(equity/top_n, equity*cap%), so the
    cap must bind exactly when it is the smaller of the two and never otherwise."""
    pool = await _pool()
    try:
        rebals = []
        await run_portfolio(pool, **SHORT, top_n=10, max_per_stock_pct=5.0,
                            _audit_rebal=rebals.append)
    finally:
        await pool.close()
    assert rebals
    for r in rebals:
        # top_n=10 implies a 10% slot, so a 5% cap must bind on every rebalance.
        assert r["slot"] == pytest.approx(r["equity"] * 0.05, rel=1e-9)


@pytest.mark.asyncio
async def test_positions_survive_year_end():
    """The defect that motivated this whole engine: the old harness closed every
    position on 31 Dec. A continuous run must hold across the boundary, so at
    least one trade must span a year end."""
    pool = await _pool()
    try:
        spans = []
        await run_portfolio(pool, **SHORT, _audit_trade=spans.append)
    finally:
        await pool.close()
    crossers = [t for t in spans
                if t["exit"] and t["entry"].year != t["exit"].year]
    assert crossers, "no position was held across a year boundary"


@pytest.mark.asyncio
async def test_costs_applied_once_per_leg_and_in_order():
    """Slippage is applied to the PRICE, charges to the resulting value, once
    each. Double-charging is invisible in the output — it just looks like a
    slightly worse strategy."""
    pool = await _pool()
    try:
        trades = []
        await run_portfolio(pool, **SHORT, _audit_trade=trades.append)
    finally:
        await pool.close()
    assert trades
    for t in trades[:50]:
        # entry price must be raw open marked UP by exactly the slippage
        assert t["entry_px"] == pytest.approx(t["raw_open"] * 1.001, rel=1e-6)
        # outlay must be gross + exactly one buy-side leg cost
        gross = t["entry_px"] * t["qty"]
        assert t["cost"] == pytest.approx(gross + _leg_cost(gross, False), rel=1e-6)


@pytest.mark.asyncio
async def test_stop_fires_at_or_below_threshold_never_above():
    """A stop that fires early flatters drawdown; one that fires late flatters
    nothing but is equally wrong.

    The assertion is an INEQUALITY, deliberately. The engine exits at the close
    that revealed the breach, not at the stop level, because only daily bars
    exist and a fill at exactly the stop price would assume the resting order
    was filled intraday at its limit — which is exactly what fails on the
    gap-downs that cause the worst losses. So the realised exit must be at or
    BELOW the threshold, never above it. An earlier version of this test
    asserted equality, and the engine was briefly changed to match it — which
    is backwards, and made the model less conservative."""
    pool = await _pool()
    try:
        trades = []
        await run_portfolio(pool, **SHORT, sl_pct=15.0, _audit_trade=trades.append)
    finally:
        await pool.close()
    stopped = [t for t in trades if t.get("reason") == "STOP"]
    assert stopped, "no stop ever fired in a two-year window with a 15% stop"
    for t in stopped:
        assert t["exit_px"] <= t["entry_px"] * 0.85 + 1e-6


@pytest.mark.asyncio
async def test_pnl_decomposition_reconciles_to_final_equity():
    """final_equity - capital == realized(closed) + unrealized(open, NET of the
    buy-side charges paid at entry).

    This is the identity the UI's "Total" column depends on. It failed in the
    display layer, not the engine: the generic SQL formula computes unrealized as
    (last_close - entry_fill_price) * qty, which omits the entry charges already
    taken out of cash — overstating total P&L by Rs.1,860 across 16 open
    positions on the continuous run.

    The algebra, which is why the entry cost belongs in unrealized:
        cash  = capital - SUM(all outlays) + SUM(closed proceeds)
        final = cash + SUM(open marked value)
              = capital + SUM_closed(proceeds - outlay)
                        + SUM_open(marked - outlay)
    so the open-position term must be marked value minus OUTLAY (gross + entry
    charges), not minus gross alone."""
    pool = await _pool()
    try:
        trades = []
        m = await run_portfolio(pool, **SHORT, _audit_trade=trades.append)
    finally:
        await pool.close()
    assert m and m["_open"], "need open positions at the end to test this"

    realized = sum(t["proceeds"] - t["cost"] for t in trades)
    unrealized = sum(h["last"] * h["qty"] - h["cost"] for h in m["_open"])
    assert m["final"] - SHORT["capital"] == pytest.approx(realized + unrealized,
                                                          abs=1.0)

    # And confirm the NAIVE formula (ignoring entry charges) is measurably
    # WRONG, so this test cannot pass against the bug it was written for.
    naive = sum((h["last"] - h["entry"]) * h["qty"] for h in m["_open"])
    assert naive > unrealized, "entry charges are not being deducted at all"


@pytest.mark.asyncio
async def test_missing_sector_does_not_exclude_a_stock():
    """Sector data covers only ~55% of traded names. A stock with NO sector must
    be unconstrained, not silently dropped — otherwise the cap quietly becomes a
    universe filter and reintroduces the survivorship problem that makes the
    strict-sector results untrustworthy."""
    pool = await _pool()
    try:
        capped = await run_portfolio(pool, **SHORT, max_stocks_per_sector=2,
                                     max_per_sector_pct=25.0)
        strict = await run_portfolio(pool, **SHORT, max_stocks_per_sector=2,
                                     max_per_sector_pct=25.0, require_sector=True)
    finally:
        await pool.close()
    # The permissive run must trade names the strict run cannot see at all.
    assert capped["trades"] > strict["trades"], (
        "capped run did not trade more names than the sector-restricted run, "
        "which means unknown-sector stocks are being excluded by the cap")


async def _main():
    """No-pytest runner, for the VPS where pytest-asyncio may not be installed."""
    import inspect
    mod = sys.modules[__name__]
    passed = failed = 0
    for name, fn in sorted(vars(mod).items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            if inspect.iscoroutinefunction(fn):
                await fn()
            else:
                fn()
            print(f"PASS {name}")
            passed += 1
        except Exception as e:
            print(f"FAIL {name}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(_main()) else 0)
