"""funnel_rsi.py — Strategy 3: "Mean Reversion on High-Quality Stocks"
(RSI oversold + bullish reversal candle) signal generation, per the user's
spec (2026-08-14).

Plugs into the EXISTING daily engine (_run() in engine.py) exactly like
funnel_squeeze.py — reuses simulator.py's SimTrade / try_fill / step_exit
UNCHANGED. This module's only job is to produce a
{signal_date: [candidate, ...]} map once, up front.

Spec mapping:
  universe      Nifty 50 / Quality Midcap. No true index-membership history
                in this DB — substituted with the SAME liquidity pre-filter
                as funnel_squeeze.py (turnover_1m_avg_cr >= 25 sustained
                >= 500 days at any point in history; a universe-selection
                convenience, not the per-day trading gate).
  macro filter  Nifty 50 Index > its own 200-day EMA. No literal Nifty 50
                index series in this DB (only ETFs) — NIFTYBEES (the Nifty
                50 ETF, tracks the index to within its ~0.05% expense ratio)
                is used as the proxy. NIFTYBEES only has price history from
                2019-01-01 onward here — for signal days before that, the
                macro filter is treated as PASS (disclosed gap; a 2016-2018
                signal is not silently dropped, it just isn't macro-gated).
  stock trend   Close > EMA200.
  pullback      RSI(14, Wilder-smoothed) < `rsi_entry_threshold` (default
                35) on the PRIOR session's close.
  signal        Today is a bullish candle (Close > Open) immediately
                following that oversold prior session — "first reversal
                candle" per the spec.
  stop          entry x (1 - rsi_stop_pct/100), default 4.5%.
  target        entry x (1 + rsi_target_pct/100), default 5% — this is the
                spec's own "Fixed % target" branch (one of the two exit
                variants it explicitly asks to test: "Fixed % target vs
                Indicator target (RSI>60)"). The indicator-target variant
                (exit on RSI crossing back above 60 OR price reaching the
                20-day EMA) would need a live RSI/EMA20 value threaded into
                every OPEN position's daily bar, the same way macd_trail
                was added to the BREAKOUT strategy — not built here; a
                reasonable v2 if the fixed-target version's numbers warrant
                the extra work.
  exit          Reuses simulator.py's EXISTING fixed_target exit (closes
                fully once the day's High reaches the target) plus the
                always-on structural/close-based stop (candidate.sl) plus
                max_holding_days. Unlike Strategy 2, max_holding_days is
                NOT exempted here — this strategy leaves breakeven and
                half_booking both off, so simulator.py's exemption
                ("skip the time-stop once the position is working") never
                engages, giving a genuinely unconditional time cap that
                matches this strategy's quick-bounce intent. The run's
                exit_config must set fixed_target=true, half_booking=false,
                breakeven=false, trailing=false (frontend preset default,
                not hardcoded here).
"""
from __future__ import annotations

import asyncio
from datetime import date

import numpy as np
import pandas as pd

EMA_TREND_DAYS = 200
RSI_PERIOD = 14
MIN_DAILY_TURNOVER_RS = 15_000_000

UNIVERSE_TURNOVER_FLOOR_CR = 25.0
UNIVERSE_MIN_QUALIFYING_DAYS = 500

DEFAULT_RSI_ENTRY_THRESHOLD = 35.0
DEFAULT_STOP_PCT = 4.5
DEFAULT_TARGET_PCT = 5.0
DEFAULT_RISK_PCT = 1.0
DEFAULT_MAX_CAPITAL_PCT = 15.0

NIFTY_PROXY_SYMBOL = "NIFTYBEES"


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
    min_bars = EMA_TREND_DAYS + 30
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


async def _load_market_regime(pool, upto: date) -> dict[date, bool]:
    """{day: NIFTYBEES close > its own 200-EMA} — see module docstring for
    the proxy disclosure. Days absent from this dict (before the proxy's own
    data starts) are treated by the caller as PASS, not as a rejection."""
    rows = await pool.fetch(
        "SELECT time::date AS d, close FROM ohlcv_data WHERE symbol = $1 AND time::date <= $2 ORDER BY time ASC",
        NIFTY_PROXY_SYMBOL, upto,
    )
    if not rows:
        return {}
    df = pd.DataFrame([dict(r) for r in rows])
    df["close"] = df["close"].astype(float)
    df["ema200"] = df["close"].ewm(span=EMA_TREND_DAYS, adjust=False).mean()
    return {r["d"]: bool(r["close"] > r["ema200"]) for r in df.to_dict("records")}


def _rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return rsi.fillna(100)  # avg_loss==0 (straight up, no down days) -> RSI=100, never falsely "oversold"


def _scan_symbol(df: pd.DataFrame, symbol: str, start_date: date, end_date: date,
                  rsi_entry_threshold: float, stop_pct: float, target_pct: float,
                  market_ok: dict[date, bool]) -> list[dict]:
    close, open_, vol = df["Close"], df["Open"], df["Volume"]
    ema200 = close.ewm(span=EMA_TREND_DAYS, adjust=False).mean()
    rsi = _rsi(close)
    turnover20_prior = (vol * close).rolling(20).mean().shift(1)

    oversold_prior = rsi.shift(1) < rsi_entry_threshold
    bullish_candle = close > open_
    trend_ok = close > ema200

    gate = (oversold_prior & bullish_candle & trend_ok
            & (turnover20_prior >= MIN_DAILY_TURNOVER_RS)).to_numpy()

    idxs = np.nonzero(gate)[0]
    out = []
    close_a = close.to_numpy()
    dates = df.index
    for i in idxs:
        d = dates[i]
        if d < start_date:
            continue
        if d > end_date:
            break
        if not market_ok.get(d, True):  # absent day (pre-2019 proxy data) => no block
            continue
        c = float(close_a[i])
        entry = round(c * 1.003, 2)
        stop = round(entry * (1 - stop_pct / 100), 2)
        target = round(entry * (1 + target_pct / 100), 2)
        risk = round(entry - stop, 2)
        if risk <= 0:
            continue
        out.append({
            "symbol": symbol, "signal_date": d, "entry": entry, "sl": stop,
            "target": target, "risk_per_share": risk,
        })
    return out


async def scan_all(pool, start_date: date, end_date: date, rsi_entry_threshold: float,
                    stop_pct: float, target_pct: float,
                    capital: float, risk_pct: float, max_capital_pct: float) -> dict:
    """Precomputes every RSI_REVERSION signal for the whole eligible
    universe/window ONCE, before the day loop. Returns
    {signal_date: [candidate, ...]}, shaped identically to
    funnel.build_candidates()'s output."""
    symbols = await _eligible_symbols(pool)
    frames = await _load_frames(pool, symbols, end_date)
    market_ok = await _load_market_regime(pool, end_date)
    sem = asyncio.Semaphore(4)

    async def _one(sym: str):
        async with sem:
            return await asyncio.to_thread(
                _scan_symbol, frames[sym], sym, start_date, end_date,
                rsi_entry_threshold, stop_pct, target_pct, market_ok)

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
                "qty": qty, "entry_type": "RSI_REVERSION", "base_stage": 0,
            })
    for d in by_day:
        by_day[d].sort(key=lambda c: c["symbol"])  # deterministic; no strong ranking signal
    return by_day


def _size_qty(capital: float, entry: float, risk_per_share: float,
              risk_pct: float, max_capital_pct: float) -> int:
    if risk_per_share <= 0:
        return 0
    qty_risk = int(capital * (risk_pct / 100) / risk_per_share)
    qty_cap = int(capital * (max_capital_pct / 100) / entry)
    return max(0, min(qty_risk, qty_cap))
