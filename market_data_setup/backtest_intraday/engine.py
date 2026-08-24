"""
Intraday backtest engine: 6 strategies x 4 exit variants, single-position
(1 concurrent trade), Rs 1,00,000 capital, NIFTY500 universe.

Session (IST 9:15-15:30) stored as UTC in DB: 03:45 - 10:00 UTC.
Forced square-off at 15:20 IST = 09:50 UTC.

Run:
    venv/bin/python3 -m market_data_setup.backtest_intraday.engine
"""
import asyncio
import asyncpg
import os
import sys
import json
import logging
from datetime import time as dtime, date
import numpy as np
import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, "/root/trade-execution-webhook")
load_dotenv("/root/trade-execution-webhook/.env")

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s')
logger = logging.getLogger(__name__)

DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = int(os.getenv('DB_PORT', 5432))
DB_USER = os.getenv('DB_USER', 'market_data_user')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'secure_market_data_pass_2026')
DB_NAME = os.getenv('DB_NAME', 'market_data')

SESSION_START = dtime(3, 45)
SESSION_END = dtime(10, 0)
SQUAREOFF = dtime(9, 50)     # 15:20 IST hard exit
ORB_END = dtime(4, 0)        # 09:30 IST — end of opening range

CAPITAL = 100_000.0
ROUND_TRIP_COST_PCT = 0.0008  # ~0.08% notional (brokerage+STT+stamp+exch+GST), conservative retail estimate


# ------------------------------------------------------------------
# Data loading
# ------------------------------------------------------------------

async def get_universe(pool, limit=None):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT symbol FROM index_membership WHERE index_name='NIFTY500' ORDER BY symbol"
        )
    syms = [r['symbol'] for r in rows]
    return syms[:limit] if limit else syms


async def load_intraday(pool, symbol, timeframe):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT time, open, high, low, close, volume FROM intraday_ohlcv "
            "WHERE symbol=$1 AND timeframe=$2 ORDER BY time", symbol, timeframe
        )
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
    df['time'] = pd.to_datetime(df['time'])
    df = df.set_index('time')
    for c in ['open', 'high', 'low', 'close']:
        df[c] = df[c].astype(float)
    df['volume'] = df['volume'].astype(float)
    return df


async def load_daily(pool, symbol):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT time, open, high, low, close FROM ohlcv_data WHERE symbol=$1 ORDER BY time", symbol
        )
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=['time', 'open', 'high', 'low', 'close'])
    df['date'] = pd.to_datetime(df['time']).dt.date
    return df.set_index('date')[['open', 'high', 'low', 'close']].astype(float)


# ------------------------------------------------------------------
# Indicators
# ------------------------------------------------------------------

def add_indicators(df):
    df = df.copy()
    df['date'] = df.index.date
    df['tod'] = df.index.time

    grp = df.groupby('date')
    tp = (df['high'] + df['low'] + df['close']) / 3.0
    df['_vp'] = tp * df['volume']
    df['cum_vol'] = grp['volume'].cumsum()
    df['cum_vp'] = df.groupby('date')['_vp'].cumsum()
    df['vwap'] = df['cum_vp'] / df['cum_vol'].replace(0, np.nan)

    delta = df['close'].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df['rsi'] = 100 - 100 / (1 + rs)

    df['ma5'] = df['close'].rolling(5).mean()
    df['ma10'] = df['close'].rolling(10).mean()
    df['ma20'] = df['close'].rolling(20).mean()
    df['vol_avg5'] = df['volume'].rolling(5).mean().shift(1)

    prev_close = df['close'].shift(1)
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - prev_close).abs(),
        (df['low'] - prev_close).abs()
    ], axis=1).max(axis=1)
    df['atr14'] = tr.rolling(14).mean()

    df.drop(columns=['_vp'], inplace=True)
    return df


def opening_range(df):
    """Per-day OR high/low from bars within the first 15 minutes (<= ORB_END)."""
    orb = df[df['tod'] <= ORB_END].groupby('date').agg(or_high=('high', 'max'), or_low=('low', 'min'))
    return orb


# ------------------------------------------------------------------
# Strategy signal generators
# Each returns a DataFrame: index=entry_time, columns=[symbol,date,entry_price,direction,init_stop]
# ------------------------------------------------------------------

def sig_orb(df5, symbol):
    orb = opening_range(df5)
    df = df5.join(orb, on='date')
    df = df[df['tod'] > ORB_END]
    long_break = (df['close'] > df['or_high']) & (df['volume'] > 1.5 * df['vol_avg5'])
    first_long = df[long_break].groupby('date').head(1)
    out = first_long.copy()
    out['direction'] = 1
    out['entry_price'] = out['close']
    out['init_stop'] = out['or_low']
    out['symbol'] = symbol
    return out[['symbol', 'date', 'entry_price', 'direction', 'init_stop']]


def sig_vwap(df5, symbol):
    df = df5.copy()
    df['prev_close'] = df.groupby('date')['close'].shift(1)
    df['prev_vwap'] = df.groupby('date')['vwap'].shift(1)
    cross_up = (df['prev_close'] <= df['prev_vwap']) & (df['close'] > df['vwap'])
    vol_ok = df['volume'] > 1.5 * df['vol_avg5']
    sig = cross_up & vol_ok
    first_sig = df[sig].groupby('date').head(1)
    out = first_sig.copy()
    out['direction'] = 1
    out['entry_price'] = out['close']
    out['init_stop'] = np.minimum(out['low'], out['vwap'])
    out['symbol'] = symbol
    return out[['symbol', 'date', 'entry_price', 'direction', 'init_stop']]


def sig_momentum(df5, symbol):
    df = df5.copy()
    df['prev_rsi'] = df.groupby('date')['rsi'].shift(1)
    cross = (df['prev_rsi'] <= 60) & (df['rsi'] > 60) & (df['close'] > df['ma20'])
    first_sig = df[cross].groupby('date').head(1)
    out = first_sig.copy()
    out['direction'] = 1
    out['entry_price'] = out['close']
    out['init_stop'] = out['ma20']
    out['symbol'] = symbol
    return out[['symbol', 'date', 'entry_price', 'direction', 'init_stop']]


def sig_gap(df5, symbol, daily):
    if daily is None:
        return pd.DataFrame(columns=['symbol', 'date', 'entry_price', 'direction', 'init_stop'])
    df = df5.copy()
    first_bar = df.groupby('date').head(1).set_index('date')
    dates = list(first_bar.index)
    prior_close = {}
    daily_dates = list(daily.index)
    for d in dates:
        prior = [x for x in daily_dates if x < d]
        prior_close[d] = daily.loc[prior[-1], 'close'] if prior else np.nan
    first_bar['prior_close'] = [prior_close[d] for d in first_bar.index]
    first_bar['gap_pct'] = (first_bar['open'] - first_bar['prior_close']) / first_bar['prior_close']
    gap_days = first_bar[first_bar['gap_pct'] > 0.005].index

    df2 = df[df['date'].isin(gap_days)]
    trigger_high = df2.groupby('date').head(1).set_index('date')['high']
    df2 = df2.join(trigger_high.rename('trig_high'), on='date')
    later = df2[df2['tod'] > df2.groupby('date')['tod'].transform('min')]
    breakout = later[later['close'] > later['trig_high']]
    first_sig = breakout.groupby('date').head(1)
    out = first_sig.copy()
    out['direction'] = 1
    out['entry_price'] = out['close']
    out['init_stop'] = out['low']
    out['symbol'] = symbol
    return out[['symbol', 'date', 'entry_price', 'direction', 'init_stop']]


def sig_sr_bounce(df5, symbol, daily):
    if daily is None:
        return pd.DataFrame(columns=['symbol', 'date', 'entry_price', 'direction', 'init_stop'])
    d3low = daily['low'].rolling(3).min().shift(1)
    df = df5.copy()
    dates = df['date'].unique()
    support = {}
    for d in dates:
        prior = [x for x in d3low.index if x < d]
        support[d] = d3low.loc[prior[-1]] if prior else np.nan
    df['support'] = df['date'].map(support)
    touch = df['low'] <= df['support'] * 1.003
    bounce = df['close'] > df['support'] * 1.003
    sig = touch & bounce
    first_sig = df[sig].groupby('date').head(1)
    out = first_sig.copy()
    out['direction'] = 1
    out['entry_price'] = out['close']
    out['init_stop'] = out['support'] * 0.995
    out['symbol'] = symbol
    return out[['symbol', 'date', 'entry_price', 'direction', 'init_stop']]


def sig_ma_cross(df5, df15, symbol):
    df = df5.copy()
    df['prev_ma5'] = df.groupby('date')['ma5'].shift(1)
    df['prev_ma10'] = df.groupby('date')['ma10'].shift(1)
    cross = (df['prev_ma5'] <= df['prev_ma10']) & (df['ma5'] > df['ma10'])

    trend15 = (df15['close'] > df15['ma20']).astype(int)
    trend15 = trend15.reindex(df.index, method='ffill')
    sig = cross & (trend15 == 1)

    first_sig = df[sig].groupby('date').head(1)
    out = first_sig.copy()
    out['direction'] = 1
    out['entry_price'] = out['close']
    out['init_stop'] = out['ma10']
    out['symbol'] = symbol
    return out[['symbol', 'date', 'entry_price', 'direction', 'init_stop']]


STRATEGIES = ['orb', 'vwap', 'momentum', 'gap', 'sr_bounce', 'ma_cross']


def generate_signals(strategy, df5, df15, daily, symbol):
    if strategy == 'orb':
        return sig_orb(df5, symbol)
    if strategy == 'vwap':
        return sig_vwap(df5, symbol)
    if strategy == 'momentum':
        return sig_momentum(df5, symbol)
    if strategy == 'gap':
        return sig_gap(df5, symbol, daily)
    if strategy == 'sr_bounce':
        return sig_sr_bounce(df5, symbol, daily)
    if strategy == 'ma_cross':
        return sig_ma_cross(df5, df15, symbol)
    raise ValueError(strategy)


# ------------------------------------------------------------------
# Exit simulation (per signal, walk forward on that symbol's 5m bars)
# ------------------------------------------------------------------

EXIT_TYPES = ['fixed_1_2', 'atr_trail', 'time_only', 'hybrid_be_trail']


def simulate_exit(bars_after, entry_price, init_stop, atr_at_entry, exit_type):
    """bars_after: df of 5m bars strictly after entry, same day, index=time."""
    if bars_after.empty:
        return entry_price, None, 'no_data'

    if exit_type == 'time_only':
        last = bars_after.iloc[-1]
        return last['close'], bars_after.index[-1], 'eod'

    if exit_type == 'fixed_1_2':
        risk = entry_price - init_stop
        if risk <= 0:
            risk = entry_price * 0.005
        target = entry_price + 2 * risk
        stop = entry_price - risk
        for t, row in bars_after.iterrows():
            if row['low'] <= stop:
                return stop, t, 'stop'
            if row['high'] >= target:
                return target, t, 'target'
        last = bars_after.iloc[-1]
        return last['close'], bars_after.index[-1], 'eod'

    if exit_type == 'atr_trail':
        atr = atr_at_entry if atr_at_entry and atr_at_entry > 0 else entry_price * 0.005
        stop = entry_price - 1.5 * atr
        highest = entry_price
        for t, row in bars_after.iterrows():
            highest = max(highest, row['high'])
            trail = highest - 1.5 * atr
            stop = max(stop, trail)
            if row['low'] <= stop:
                return stop, t, 'trail_stop'
        last = bars_after.iloc[-1]
        return last['close'], bars_after.index[-1], 'eod'

    if exit_type == 'hybrid_be_trail':
        risk = entry_price - init_stop
        if risk <= 0:
            risk = entry_price * 0.005
        stop = entry_price - risk
        target1r = entry_price + risk
        moved_to_be = False
        highest = entry_price
        atr = atr_at_entry if atr_at_entry and atr_at_entry > 0 else entry_price * 0.005
        for t, row in bars_after.iterrows():
            if row['low'] <= stop:
                return stop, t, 'stop'
            highest = max(highest, row['high'])
            if not moved_to_be and row['high'] >= target1r:
                moved_to_be = True
                stop = max(stop, entry_price)
            if moved_to_be:
                trail = highest - 1.0 * atr
                stop = max(stop, trail)
        last = bars_after.iloc[-1]
        return last['close'], bars_after.index[-1], 'eod'

    raise ValueError(exit_type)


# ------------------------------------------------------------------
# Portfolio-level single-position simulator
# ------------------------------------------------------------------

def _prep_day_index(symbol_bars: dict):
    """Precompute {symbol: {date: day_df}} once, avoids O(n) date filtering per signal."""
    idx = {}
    for symbol, bars in symbol_bars.items():
        idx[symbol] = {d: g for d, g in bars.groupby('date')}
    return idx


def run_matrix(all_signals: dict, symbol_bars: dict, exit_types=EXIT_TYPES):
    """all_signals: {strategy: concatenated signals df across symbols}
       symbol_bars: {symbol: df5 with indicators}
       Returns: {(strategy, exit_type): metrics dict}
    """
    results = {}
    day_index = _prep_day_index(symbol_bars)
    for strategy, sigdf in all_signals.items():
        if sigdf.empty:
            continue
        sigdf = sigdf.sort_index()
        for exit_type in exit_types:
            capital = CAPITAL
            equity_curve = []
            trades = []
            busy_until = None  # timestamp when current position frees up

            for entry_time, row in sigdf.iterrows():
                if busy_until is not None and entry_time < busy_until:
                    continue  # single concurrent position — skip overlapping signals

                symbol = row['symbol']
                d = row['date']
                day_bars = day_index.get(symbol, {}).get(d)
                if day_bars is None:
                    continue
                after = day_bars[(day_bars.index > entry_time) & (day_bars['tod'] <= SQUAREOFF)]
                atr_val = None
                if entry_time in day_bars.index:
                    atr_val = day_bars.loc[entry_time, 'atr14']
                    if isinstance(atr_val, pd.Series):
                        atr_val = atr_val.iloc[0]

                exit_price, exit_time, reason = simulate_exit(
                    after, row['entry_price'], row['init_stop'], atr_val, exit_type
                )
                qty = int(capital // row['entry_price'])
                if qty <= 0:
                    continue
                pnl_gross = (exit_price - row['entry_price']) * qty
                notional = (row['entry_price'] + exit_price) * qty
                cost = notional * ROUND_TRIP_COST_PCT
                pnl_net = pnl_gross - cost

                capital += pnl_net
                trades.append({
                    'symbol': symbol, 'date': str(d), 'entry_time': str(entry_time),
                    'exit_time': str(exit_time), 'entry_price': row['entry_price'],
                    'exit_price': exit_price, 'qty': qty, 'pnl_net': pnl_net, 'reason': reason
                })
                equity_curve.append(capital)
                busy_until = exit_time if exit_time is not None else entry_time

            if not trades:
                continue

            tdf = pd.DataFrame(trades)
            wins = tdf[tdf['pnl_net'] > 0]
            losses = tdf[tdf['pnl_net'] <= 0]
            win_rate = len(wins) / len(tdf) * 100
            eq = pd.Series([CAPITAL] + equity_curve)
            running_max = eq.cummax()
            dd = (eq - running_max) / running_max * 100
            max_dd = dd.min()
            total_return_pct = (capital - CAPITAL) / CAPITAL * 100

            n_days = max((pd.to_datetime(tdf['date']).max() - pd.to_datetime(tdf['date']).min()).days, 1)
            cagr = ((capital / CAPITAL) ** (365.0 / n_days) - 1) * 100 if capital > 0 else -100

            results[(strategy, exit_type)] = {
                'trades': len(tdf), 'win_rate_pct': round(win_rate, 2),
                'avg_win': round(wins['pnl_net'].mean(), 2) if len(wins) else 0,
                'avg_loss': round(losses['pnl_net'].mean(), 2) if len(losses) else 0,
                'total_pnl': round(capital - CAPITAL, 2),
                'total_return_pct': round(total_return_pct, 2),
                'max_drawdown_pct': round(max_dd, 2),
                'cagr_pct': round(cagr, 2),
                'final_capital': round(capital, 2),
            }
            tdf.to_csv(f"/root/backtest_trades_{strategy}_{exit_type}.csv", index=False)
    return results


async def main(limit=None):
    pool = await asyncpg.create_pool(host=DB_HOST, port=DB_PORT, user=DB_USER,
                                      password=DB_PASSWORD, database=DB_NAME,
                                      min_size=1, max_size=5)
    universe = await get_universe(pool, limit=limit)
    logger.info(f"Universe: {len(universe)} symbols")

    all_sig = {s: [] for s in STRATEGIES}
    symbol_bars = {}
    n_ok = 0

    for i, symbol in enumerate(universe, 1):
        df5 = await load_intraday(pool, symbol, '5m')
        df15 = await load_intraday(pool, symbol, '15m')
        if df5 is None or df15 is None or len(df5) < 100:
            continue
        daily = await load_daily(pool, symbol)

        df5 = add_indicators(df5)
        df15 = add_indicators(df15)
        symbol_bars[symbol] = df5
        n_ok += 1

        for strat in STRATEGIES:
            try:
                sdf = generate_signals(strat, df5, df15, daily, symbol)
                if not sdf.empty:
                    all_sig[strat].append(sdf)
            except Exception as e:
                logger.warning(f"{symbol} {strat}: {e}")

        if i % 50 == 0:
            logger.info(f"Processed {i}/{len(universe)} ({n_ok} usable)")

    logger.info(f"Signal generation done. {n_ok} symbols with data.")
    combined = {}
    for strat in STRATEGIES:
        if all_sig[strat]:
            combined[strat] = pd.concat(all_sig[strat]).sort_index()
            logger.info(f"{strat}: {len(combined[strat])} raw signals")
        else:
            combined[strat] = pd.DataFrame()

    results = run_matrix(combined, symbol_bars)

    out = {f"{k[0]}|{k[1]}": v for k, v in results.items()}
    with open("/root/intraday_backtest_results.json", "w") as f:
        json.dump(out, f, indent=2)

    logger.info("=== RESULTS ===")
    for k, v in sorted(out.items(), key=lambda x: -x[1]['total_return_pct']):
        logger.info(f"{k:35s} trades={v['trades']:5d} win%={v['win_rate_pct']:6.2f} "
                    f"CAGR%={v['cagr_pct']:8.2f} maxDD%={v['max_drawdown_pct']:7.2f} "
                    f"totalRet%={v['total_return_pct']:8.2f}")

    await pool.close()


if __name__ == "__main__":
    import sys as _sys
    lim = int(_sys.argv[1]) if len(_sys.argv) > 1 else None
    asyncio.run(main(limit=lim))
