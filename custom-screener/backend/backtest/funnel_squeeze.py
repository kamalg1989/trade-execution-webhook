"""funnel_squeeze.py — Strategy 2: "Breakout from Volatility Compression"
(NR7 / Bollinger squeeze) signal generation, per the user's spec (2026-08-14).

Plugs into the EXISTING daily engine (_run() in engine.py) as an alternative
candidate source, selected via backtest_runs.strategy == 'SQUEEZE_BREAKOUT'.
Deliberately reuses simulator.py's SimTrade / try_fill / step_exit UNCHANGED
for entry-trigger fills, half-booking-at-2R, breakeven/trailing and the
time-stop — this module's only job is to produce a {signal_date: [candidate,
...]} map once, up front, exactly like weekly_engine.py's Phase A. No new
trade-lifecycle code exists anywhere for this strategy.

Spec mapping:
  universe    Nifty 200 / F&O stocks. No true index-membership history in
              this DB (see weekly_breakout.py's docstring for the identical
              limitation) — substituted with a liquidity PRE-FILTER
              (symbols with sustained turnover_1m_avg_cr >= 25 for >= 500
              days at ANY point in history). This is a computational
              universe-selection convenience only, not the real per-day
              trading gate — the actual point-in-time rolling-turnover check
              happens inside _scan_symbol below and can reject a signal day
              even for an eligible symbol.
  trend       Close > EMA50.
  compression Bollinger Bandwidth(20,2) at/within 5% of its trailing
              126-session (~6 month) low, at any point in the preceding
              COMPRESSION_RECENCY_DAYS sessions (the breakout candle itself
              is rarely still "compressed" — that's what makes it a
              breakout).
  trigger     Close breaks above the prior 20-session High on volume >=
              `volume_multiplier` x the prior 20-session average volume.
              Both windows end the day BEFORE the breakout candle (.shift(1))
              — no look-ahead.
  stop        The prior-20-session swing low, clamped to 2%-15% below entry
              (spec says "typically 3%-5%"; the clamp is an engineering
              safety rail for degenerate/illiquid cases, not a spec rule).
  target      entry + 2 x risk_per_share. Spec says "1:2 Risk-to-Reward (or
              +8% gain)" — 2R is used directly (for a ~4% stop, 2R ≈ 8%,
              matching the spec's own parenthetical), rather than a second,
              independently-drifting +8% rule.
  exit        Half-book 50% at the 2R target, trail the remainder, hard stop
              at candidate.sl, time-stop at max_holding_days — all via
              simulator.py's EXISTING half_booking/trailing/max_holding_days
              exit_config toggles (the caller — the backtest run's
              exit_config — must set half_booking=true, trailing=true,
              breakeven=false, fixed_target=false; the frontend preset does
              this, it is not hardcoded here).
              NOTE: simulator.py's max_holding_days is exempted once a trade
              has half-booked ("the clock must not touch a working
              position" — see that module's docstring). This is a
              deliberate, disclosed reuse of the existing BREAKOUT
              strategy's philosophy, not a literal "always flatten at N
              days" rule — a trade that already locked in its 2R half-book
              is allowed to keep trailing past the cap.
"""
from __future__ import annotations

import asyncio
from datetime import date

import numpy as np
import pandas as pd

EMA_TREND_DAYS = 50
BB_PERIOD = 20
BB_STD = 2.0
BANDWIDTH_LOOKBACK_DAYS = 126
SQUEEZE_TOLERANCE = 1.05           # "at/near" the 126-day bandwidth low
COMPRESSION_RECENCY_DAYS = 10      # squeeze must have occurred within N sessions before the breakout
BREAKOUT_LOOKBACK_DAYS = 20
STOP_LOOKBACK_DAYS = 20
STOP_MIN_PCT = 0.02
STOP_MAX_PCT = 0.15
TARGET_R_MULTIPLE = 2.0
MIN_DAILY_TURNOVER_RS = 15_000_000  # ~Rs 1.5cr/day point-in-time liquidity floor

# Universe pre-filter (see module docstring) — NOT the per-day trading gate.
UNIVERSE_TURNOVER_FLOOR_CR = 25.0
UNIVERSE_MIN_QUALIFYING_DAYS = 500

DEFAULT_VOLUME_MULTIPLIER = 1.5
DEFAULT_RISK_PCT = 1.0
DEFAULT_MAX_CAPITAL_PCT = 15.0


async def _eligible_symbols(pool) -> list[str]:
    rows = await pool.fetch(
        """
        SELECT symbol FROM (
          SELECT symbol, COUNT(*) FILTER (WHERE turnover_1m_avg_cr >= $1) AS n
          FROM stock_indicators GROUP BY symbol
        ) s WHERE n >= $2
        """,
        UNIVERSE_TURNOVER_FLOOR_CR, UNIVERSE_MIN_QUALIFYING_DAYS,
    )
    return [r["symbol"] for r in rows]


async def _load_frames(pool, symbols: list[str], upto: date) -> dict[str, pd.DataFrame]:
    rows = await pool.fetch(
        "SELECT symbol, time::date AS d, open, high, low, close, volume FROM ohlcv_data "
        "WHERE symbol = ANY($1) AND time::date <= $2 ORDER BY symbol, time ASC",
        symbols, upto,
    )
    by_symbol: dict[str, list] = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"], []).append(r)
    frames = {}
    min_bars = BANDWIDTH_LOOKBACK_DAYS + BB_PERIOD + 30
    for sym, rs in by_symbol.items():
        if len(rs) < min_bars:
            continue
        df = pd.DataFrame([dict(r) for r in rs])
        df = df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                                 "close": "Close", "volume": "Volume"})
        df = df[["Open", "High", "Low", "Close", "Volume", "d"]].astype(
            {"Open": float, "High": float, "Low": float, "Close": float, "Volume": float}
        )
        df = df.set_index("d")
        frames[sym] = df
    return frames


def _scan_symbol(df: pd.DataFrame, symbol: str, start_date: date, end_date: date,
                  volume_multiplier: float) -> list[dict]:
    close, high, low, vol = df["Close"], df["High"], df["Low"], df["Volume"]
    ema50 = close.ewm(span=EMA_TREND_DAYS, adjust=False).mean()
    sma20 = close.rolling(BB_PERIOD).mean()
    std20 = close.rolling(BB_PERIOD).std()
    upper = sma20 + BB_STD * std20
    lower = sma20 - BB_STD * std20
    bandwidth = (upper - lower) / sma20
    bw_min_126 = bandwidth.rolling(BANDWIDTH_LOOKBACK_DAYS).min()
    squeeze_day = bandwidth <= bw_min_126 * SQUEEZE_TOLERANCE
    recently_squeezed = squeeze_day.shift(1).rolling(COMPRESSION_RECENCY_DAYS).max().fillna(0) > 0

    high20_prior = high.rolling(BREAKOUT_LOOKBACK_DAYS).max().shift(1)
    vol20_prior = vol.rolling(BREAKOUT_LOOKBACK_DAYS).mean().shift(1)
    low20_prior = low.rolling(STOP_LOOKBACK_DAYS).min().shift(1)
    turnover20_prior = (vol * close).rolling(BREAKOUT_LOOKBACK_DAYS).mean().shift(1)

    gate = (
        (close > high20_prior)
        & (vol >= vol20_prior * volume_multiplier)
        & (close > ema50)
        & recently_squeezed
        & (turnover20_prior >= MIN_DAILY_TURNOVER_RS)
    ).to_numpy()

    idxs = np.nonzero(gate)[0]
    out = []
    close_a = close.to_numpy()
    low20_a = low20_prior.to_numpy()
    vol_a = vol.to_numpy()
    vol20_a = vol20_prior.to_numpy()
    dates = df.index
    for i in idxs:
        d = dates[i]
        if d < start_date:
            continue
        if d > end_date:
            break
        c = float(close_a[i])
        entry = round(c * 1.003, 2)
        swing_low = float(low20_a[i])
        min_stop = entry * (1 - STOP_MAX_PCT)
        max_stop = entry * (1 - STOP_MIN_PCT)
        stop = round(min(max(swing_low, min_stop), max_stop), 2)
        if stop >= entry:
            continue
        risk = round(entry - stop, 2)
        target = round(entry + TARGET_R_MULTIPLE * risk, 2)
        vol_ratio = float(vol_a[i]) / float(vol20_a[i]) if vol20_a[i] else 0.0
        out.append({
            "symbol": symbol, "signal_date": d, "entry": entry, "sl": stop,
            "target": target, "risk_per_share": risk, "vol_ratio": vol_ratio,
        })
    return out


async def scan_all(pool, start_date: date, end_date: date, volume_multiplier: float,
                    capital: float, risk_pct: float, max_capital_pct: float) -> dict:
    """Precomputes every SQUEEZE_BREAKOUT signal for the whole eligible
    universe/window ONCE, before the day loop — same two-phase idea as
    weekly_engine.py's Phase A. Returns {signal_date: [candidate, ...]},
    each candidate already shaped exactly like funnel.build_candidates()'s
    output (symbol/entry/sl/target/risk_per_share/qty/entry_type/base_stage)
    so engine.py's day loop can consume it identically to the other funnels."""
    symbols = await _eligible_symbols(pool)
    frames = await _load_frames(pool, symbols, end_date)
    sem = asyncio.Semaphore(4)

    async def _one(sym: str):
        async with sem:
            return await asyncio.to_thread(
                _scan_symbol, frames[sym], sym, start_date, end_date, volume_multiplier)

    scanned = await asyncio.gather(*(_one(s) for s in frames))
    by_day: dict[date, list[dict]] = {}
    for sigs in scanned:
        for s in sigs:
            qty = _size_qty(capital, s["entry"], s["risk_per_share"], risk_pct, max_capital_pct)
            if qty <= 0:
                continue
            by_day.setdefault(s["signal_date"], []).append({
                "symbol": s["symbol"], "entry": s["entry"], "sl": s["sl"],
                "target": s["target"], "risk_per_share": s["risk_per_share"],
                "qty": qty, "entry_type": "SQUEEZE_BREAKOUT", "base_stage": 0,
                "vol_ratio": s["vol_ratio"],
            })
    for d in by_day:
        by_day[d].sort(key=lambda c: -c["vol_ratio"])  # strongest volume expansion first
    return by_day


def _size_qty(capital: float, entry: float, risk_per_share: float,
              risk_pct: float, max_capital_pct: float) -> int:
    if risk_per_share <= 0:
        return 0
    qty_risk = int(capital * (risk_pct / 100) / risk_per_share)
    qty_cap = int(capital * (max_capital_pct / 100) / entry)
    return max(0, min(qty_risk, qty_cap))
