"""
Mr. Mani's Sensex ITM-spread strategy — formalized backtest.

Discretionary source strategy (video), translated into explicit rules.
ALL numeric thresholds below are my own quantified proxies for what the
video describes visually/qualitatively — flagged inline. Nothing here
should be read as "his exact rule," only a testable interpretation.

Structure:
  Bias from first 30-min candle of the underlying (spot, proxied from the
  options data's embedded `spot` field — not true index OHLC).
  - bullish: 30m close > open AND close > prior day's last spot
  - bearish: 30m close < open AND close < prior day's last spot
  - skip (Doji): |close-open| < 30% of (high-low) range  [MY threshold]

  Entry: breakout of the opening 30-min range in the bias direction.
  High-momentum classification [MY proxy]: entry bar's move from OR open
  exceeds 0.5% of spot -> "breakout/momentum" -> use DEBIT spread.
  Otherwise -> normal decay play -> use CREDIT spread.

  Bullish credit: SELL PUT ATM+N, BUY PUT ATM+N-W  (put credit spread)
  Bullish debit:  BUY  CALL ATM-N, SELL CALL ATM-N+W (call debit spread)
  Bearish credit: SELL CALL ATM-N, BUY CALL ATM-N+W (call credit spread)
  Bearish debit:  BUY  PUT ATM+N, SELL PUT ATM+N-W  (put debit spread)

  SL: spot closes beyond the opposite side of the opening range.
  Exit: hold to 15:20 IST (expiry square-off) if SL not hit.
  Sizing: base 1 lot (20 units); optional 1 scale-in lot on pullback to
  the breakout level without SL trigger [tested as a variant].
  Capital: Rs 5,00,000 [assumption — spreads need more margin than 1L].
"""
import asyncio, asyncpg, os, sys, json, logging
from datetime import time as dtime, date, timedelta
import numpy as np
import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, "/root/trade-execution-webhook")
load_dotenv("/root/trade-execution-webhook/.env")

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s')
logger = logging.getLogger(__name__)

DB_HOST=os.getenv('DB_HOST','localhost'); DB_PORT=int(os.getenv('DB_PORT',5432))
DB_USER=os.getenv('DB_USER','market_data_user'); DB_PASSWORD=os.getenv('DB_PASSWORD','secure_market_data_pass_2026')
DB_NAME=os.getenv('DB_NAME','market_data')

SESSION_START = dtime(3, 45)   # 09:15 IST
OR_END = dtime(4, 15)          # 09:45 IST (end of opening 30-min range)
SQUAREOFF = dtime(9, 50)       # 15:20 IST
def lot_size_for(d: date) -> int:
    """Sensex lot size: 10 before 2025-01-07, 20 from then on (SEBI revision)."""
    return 10 if d < date(2025, 1, 7) else 20
CAPITAL = 500_000.0
DOJI_BODY_RATIO = 0.30         # ASSUMPTION
MOMENTUM_THRESHOLD_PCT = 0.005 # ASSUMPTION: 0.5% move on entry bar => "breakout"
FLAT_COST_PER_TRADE = 100.0    # ASSUMPTION: brokerage+statutory approx per spread trade
NOTIONAL_COST_PCT = 0.0005     # ASSUMPTION: ~0.05% of notional


def expiry_weekday_for(d: date) -> int:
    """Sensex weekly expiry: Tuesday (1) before 2025-09-04, Thursday (3) after."""
    return 1 if d < date(2025, 9, 4) else 3


async def load_strike(pool, label, opt_type):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT time, strike_price, spot, open, high, low, close, oi, iv "
            "FROM sensex_options_ohlcv WHERE strike_label=$1 AND option_type=$2 ORDER BY time",
            label, opt_type
        )
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=['time','strike_price','spot','open','high','low','close','oi','iv'])
    df['time'] = pd.to_datetime(df['time'])
    df = df.set_index('time')
    for c in ['strike_price','spot','open','high','low','close']:
        df[c] = df[c].astype(float)
    return df


def find_expiry_days(spot_df):
    """Empirically pick, for each ISO week, the trading day closest to (but not after)
    the theoretical expiry weekday that actually has data (handles holidays)."""
    dates = sorted(set(spot_df.index.date))
    by_week = {}
    for d in dates:
        wk = (d.isocalendar().year, d.isocalendar().week)
        by_week.setdefault(wk, []).append(d)
    expiry_days = []
    for wk, ds in by_week.items():
        ds = sorted(ds)
        target_wd = expiry_weekday_for(ds[0])
        candidates = [d for d in ds if d.weekday() <= target_wd]
        if not candidates:
            continue
        expiry_days.append(max(candidates))
    return sorted(expiry_days)


def build_day_bias(spot_df, d, prior_last_spot):
    day = spot_df[spot_df.index.date == d]
    or_window = day[day.index.time <= OR_END]
    if or_window.empty or len(or_window) < 3:
        return None
    or_open = or_window['spot'].iloc[0]
    or_close = or_window['spot'].iloc[-1]
    or_high = or_window['spot'].max()
    or_low = or_window['spot'].min()
    rng = or_high - or_low
    body = abs(or_close - or_open)
    if rng == 0 or body < DOJI_BODY_RATIO * rng:
        return None  # Doji / choppy -> skip
    bullish = or_close > or_open and (prior_last_spot is None or or_close > prior_last_spot)
    bearish = or_close < or_open and (prior_last_spot is None or or_close < prior_last_spot)
    if not (bullish or bearish):
        return None
    return {
        'bias': 'bullish' if bullish else 'bearish',
        'or_open': or_open, 'or_close': or_close, 'or_high': or_high, 'or_low': or_low
    }


def find_entry(spot_df, d, bias_info):
    day = spot_df[(spot_df.index.date == d) & (spot_df.index.time > OR_END) & (spot_df.index.time <= SQUAREOFF)]
    if day.empty:
        return None
    if bias_info['bias'] == 'bullish':
        trig = day[day['spot'] > bias_info['or_high']]
    else:
        trig = day[day['spot'] < bias_info['or_low']]
    if trig.empty:
        return None
    t0 = trig.index[0]
    row = trig.iloc[0]
    move_pct = abs(row['spot'] - bias_info['or_open']) / bias_info['or_open']
    is_momentum = move_pct > MOMENTUM_THRESHOLD_PCT
    return {'entry_time': t0, 'entry_spot': row['spot'], 'is_momentum': is_momentum}


def leg_price_at(strike_df, t, col='close'):
    if strike_df is None or t not in strike_df.index:
        return None
    return strike_df.loc[t, col]


def simulate_trade(strikes, d, bias_info, entry_info, itm_depth, spread_width, scale_in):
    """strikes: dict[(offset, 'CE'/'PE')] -> df for that day already sliced (full-day df, indexed by time)"""
    bias = bias_info['bias']
    momentum = entry_info['is_momentum']
    entry_t = entry_info['entry_time']

    if bias == 'bullish' and not momentum:
        struct, opt = 'credit', 'PE'
        short_off, long_off = itm_depth, itm_depth - spread_width
    elif bias == 'bullish' and momentum:
        struct, opt = 'debit', 'CE'
        long_off, short_off = -itm_depth, -itm_depth + spread_width  # buy ITM call, sell further OTM call
    elif bias == 'bearish' and not momentum:
        struct, opt = 'credit', 'CE'
        short_off, long_off = -itm_depth, -itm_depth + spread_width
    else:
        struct, opt = 'debit', 'PE'
        long_off, short_off = itm_depth, itm_depth - spread_width

    short_df = strikes.get((short_off, opt))
    long_df = strikes.get((long_off, opt))
    if short_df is None or long_df is None:
        return None

    short_entry = leg_price_at(short_df, entry_t)
    long_entry = leg_price_at(long_df, entry_t)
    if short_entry is None or long_entry is None:
        return None

    qty_lots = 1
    if scale_in:
        # ASSUMPTION: pullback = spot retraces to touch the breakout level again
        # without SL, within 6 bars (30 min) after entry, without hitting SL first.
        pass  # handled below via loop

    after = short_df[(short_df.index.time > entry_t.time()) & (short_df.index.date == d)
                      & (short_df.index.time <= SQUAREOFF)]
    long_after = long_df[(long_df.index.time > entry_t.time()) & (long_df.index.date == d)
                          & (long_df.index.time <= SQUAREOFF)]

    spot_after = strikes['spot_series']
    spot_after = spot_after[(spot_after.index > entry_t) & (spot_after.index.time <= SQUAREOFF)]

    exit_t, exit_reason = None, 'eod'
    scaled = False
    lots_added_at = None

    for t in after.index:
        if t not in spot_after.index:
            continue
        spot_now = spot_after.loc[t, 'spot'] if t in spot_after.index else None
        if spot_now is None:
            continue
        if bias == 'bullish' and spot_now < bias_info['or_low']:
            exit_t, exit_reason = t, 'sl'
            break
        if bias == 'bearish' and spot_now > bias_info['or_high']:
            exit_t, exit_reason = t, 'sl'
            break
        if scale_in and not scaled:
            near_breakout = (abs(spot_now - (bias_info['or_high'] if bias=='bullish' else bias_info['or_low']))
                              / bias_info['or_open']) < 0.001
            if near_breakout:
                scaled = True
                lots_added_at = t

    if exit_t is None:
        valid_times = after.index[after.index.time <= SQUAREOFF]
        exit_t = valid_times[-1] if len(valid_times) else entry_t

    short_exit = leg_price_at(short_df, exit_t)
    long_exit = leg_price_at(long_df, exit_t)
    if short_exit is None or long_exit is None:
        return None

    total_lots = 2 if scaled else 1
    qty = total_lots * lot_size_for(d)

    if struct == 'credit':
        entry_val = short_entry - long_entry     # credit received
        exit_val = short_exit - long_exit         # debit to close
        pnl_gross = (entry_val - exit_val) * qty
        notional = (short_entry + long_entry) * qty
    else:
        entry_val = long_entry - short_entry       # debit paid
        exit_val = long_exit - short_exit           # credit on close
        pnl_gross = (exit_val - entry_val) * qty
        notional = (long_entry + short_entry) * qty

    n_orders = 4 if not scaled else 6
    cost = FLAT_COST_PER_TRADE * (n_orders / 4) + notional * NOTIONAL_COST_PCT
    pnl_net = pnl_gross - cost

    return {
        'date': str(d), 'bias': bias, 'struct': struct, 'opt_type': opt,
        'short_off': short_off, 'long_off': long_off,
        'entry_time': str(entry_t), 'exit_time': str(exit_t), 'reason': exit_reason,
        'entry_val': round(entry_val,2), 'exit_val': round(exit_val,2),
        'lots': total_lots, 'pnl_net': round(pnl_net,2)
    }


async def main(itm_depth=3, spread_width=4, scale_in=False):
    pool = await asyncpg.create_pool(host=DB_HOST, port=DB_PORT, user=DB_USER,
                                      password=DB_PASSWORD, database=DB_NAME, min_size=1, max_size=5)
    strikes = {}
    for off in range(-6, 7):
        for opt in ['CE', 'PE']:
            label = "ATM" if off == 0 else f"ATM{'+' if off>0 else ''}{off}"
            df = await load_strike(pool, label, opt)
            if df is not None:
                strikes[(off, opt)] = df

    spot_df = strikes.get((0, 'CE'))
    if spot_df is None:
        logger.error("No ATM CE data — cannot proceed")
        return
    strikes['spot_series'] = spot_df[['spot']]

    expiry_days = find_expiry_days(spot_df)
    logger.info(f"Found {len(expiry_days)} expiry days")

    trades = []
    prior_last_spot = None
    for d in expiry_days:
        day_spot = spot_df[spot_df.index.date == d]
        if day_spot.empty:
            continue
        bias_info = build_day_bias(spot_df, d, prior_last_spot)
        prior_last_spot = day_spot['spot'].iloc[-1]
        if bias_info is None:
            continue
        entry_info = find_entry(spot_df, d, bias_info)
        if entry_info is None:
            continue
        day_strikes = {k: (v[v.index.date == d] if isinstance(v, pd.DataFrame) and k != 'spot_series' else v)
                       for k, v in strikes.items()}
        day_strikes['spot_series'] = strikes['spot_series'][strikes['spot_series'].index.date == d]
        trade = simulate_trade(day_strikes, d, bias_info, entry_info, itm_depth, spread_width, scale_in)
        if trade:
            trades.append(trade)

    if not trades:
        logger.info("No trades generated")
        return

    tdf = pd.DataFrame(trades)
    wins = tdf[tdf['pnl_net'] > 0]
    capital = CAPITAL
    equity = [capital]
    for pnl in tdf['pnl_net']:
        capital += pnl
        equity.append(capital)
    eq = pd.Series(equity)
    dd = (eq - eq.cummax()) / eq.cummax() * 100

    result = {
        'itm_depth': itm_depth, 'spread_width': spread_width, 'scale_in': scale_in,
        'trades': len(tdf), 'win_rate_pct': round(len(wins)/len(tdf)*100, 2),
        'avg_win': round(wins['pnl_net'].mean(), 2) if len(wins) else 0,
        'avg_loss': round(tdf[tdf['pnl_net']<=0]['pnl_net'].mean(), 2) if len(tdf[tdf['pnl_net']<=0]) else 0,
        'total_pnl': round(capital - CAPITAL, 2),
        'total_return_pct': round((capital-CAPITAL)/CAPITAL*100, 2),
        'max_drawdown_pct': round(dd.min(), 2),
        'final_capital': round(capital, 2),
    }
    logger.info(json.dumps(result, indent=2))
    tdf.to_csv(f"/root/sensex_trades_d{itm_depth}_w{spread_width}_scale{scale_in}.csv", index=False)
    await pool.close()
    return result


if __name__ == "__main__":
    depth = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    width = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    scale = sys.argv[3].lower() == 'true' if len(sys.argv) > 3 else False
    asyncio.run(main(depth, width, scale))
