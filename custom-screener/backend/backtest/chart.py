"""Per-trade annotated chart for backtest review.

Static PNG (same mplfinance approach as ai_analysis/charting/render.py,
including the plt.close(fig) fix — kept as a separate function rather than
extending the AI renderer so backtest-specific annotation logic doesn't leak
into the production AI charting path).

Plots: candles + EMA50 context, wide history window for pattern formation,
then horizontal lines for entry, the always-on -8% floor, structural SL, the
final trailing SL (if it moved off structural), 1R/2R/3R, and the fixed
target — all static values already stored on the trade row, no simulator
replay needed. Each line is labeled inline at the left edge (not just in a
legend). Entry/exit are marked with a bold arrow+dot directly above that
day's candle, and the trade's duration is lightly shaded. Styling settled
after a few rounds of visual iteration with the user (see chat).

WEEKLY_BOX_BREAKOUT trades (see weekly_simulator.py) get one more series: the
trail SL didn't move by one EMA/R-ladder step like the daily strategies, it
ratchets on weekly MACD bearish crossovers, so a single "final trail SL"
hline understates the trade's actual risk path — a stock that ratcheted up
three times before finally being stopped out looks identical to one that
never trailed at all. load_weekly_trail_sl_series() replays the exact same
ratchet rule from weekly_simulator.step_exit_weekly (not the whole exit
ladder, just the SL-level bookkeeping) against ohlcv_weekly + recomputed
weekly MACD, entirely at render time — no schema change, and it retroactively
works for trades from runs that predate this feature.
"""
from __future__ import annotations

import io
from datetime import date

import pandas as pd

from . import weekly_breakout as wb


async def load_trade_window(
    pool, symbol: str, anchor_start: date, anchor_end: date,
    bars_before: int = 90, bars_after: int = 12,
) -> pd.DataFrame | None:
    """OHLCV window: up to `bars_before` bars ending at anchor_start (enough
    history to see the base/pattern that led into the signal), the full
    anchor_start->anchor_end span uncapped (the trade's actual lifetime),
    then up to `bars_after` bars past anchor_end."""
    before = await pool.fetch(
        """
        SELECT time, open, high, low, close, volume FROM (
          SELECT time, open, high, low, close, volume,
                 row_number() OVER (ORDER BY time DESC) AS rn
          FROM ohlcv_data WHERE symbol = $1 AND time::date <= $2
        ) t WHERE rn <= $3 ORDER BY time ASC
        """,
        symbol, anchor_start, bars_before,
    )
    middle = await pool.fetch(
        """
        SELECT time, open, high, low, close, volume FROM ohlcv_data
        WHERE symbol = $1 AND time::date > $2 AND time::date <= $3
        ORDER BY time ASC
        """,
        symbol, anchor_start, anchor_end,
    )
    after = await pool.fetch(
        """
        SELECT time, open, high, low, close, volume FROM (
          SELECT time, open, high, low, close, volume,
                 row_number() OVER (ORDER BY time ASC) AS rn
          FROM ohlcv_data WHERE symbol = $1 AND time::date > $2
        ) t WHERE rn <= $3 ORDER BY time ASC
        """,
        symbol, anchor_end, bars_after,
    )
    rows = [dict(r) for r in before] + [dict(r) for r in middle] + [dict(r) for r in after]
    if not rows:
        return None
    df = pd.DataFrame(rows)
    # ohlcv_data.time is tz-aware (timestamptz); drop the tz so this lines up
    # with the tz-naive dates on the trade row (entryFillDate/exitDate are
    # plain DATE columns) — mplfinance's hlines/positional lookups raise on a
    # tz-naive/tz-aware mismatch otherwise.
    df["time"] = pd.to_datetime(df["time"]).dt.tz_localize(None)
    df = df.set_index("time").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    return df


async def load_weekly_trail_sl_series(
    pool, symbol: str, entry_fill_date: date, structural_sl: float, as_of_end: date,
) -> list[tuple[date, float]]:
    """Replays weekly_simulator.step_exit_weekly's SL-ratchet rule (bearish
    MACD crossover -> stop moves up to that crossover week's low, never
    loosens) from entry to as_of_end (exit date, or the run's end_date for a
    still-OPEN trade), returning [(week_end, sl_level), ...] starting with
    (entry_fill_date, structural_sl).

    Fetches weekly bars from ~200 weeks before entry — MACD(12,26,9) is an
    EWM, so its dependence on the exact starting point decays geometrically
    (~0.93^n per week); by 200 weeks the values are numerically
    indistinguishable from computing over the symbol's entire history, which
    is what the actual backtest run did. Avoids re-fetching a decade of bars
    just to draw one trade's chart.
    """
    rows = await pool.fetch(
        """
        SELECT week_end, open, high, low, close, volume FROM ohlcv_weekly
        WHERE symbol = $1 AND week_end <= $2
        ORDER BY week_end DESC LIMIT 260
        """,
        symbol, as_of_end,
    )
    if not rows:
        return [(entry_fill_date, structural_sl)]
    df = pd.DataFrame([dict(r) for r in reversed(rows)])
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                             "close": "Close", "volume": "Volume"})
    df = df[["Open", "High", "Low", "Close", "Volume", "week_end"]].astype(
        {"Open": float, "High": float, "Low": float, "Close": float, "Volume": float}
    )
    df = df.set_index("week_end")
    df = wb.compute_weekly_indicators(df)

    weeks_after_entry = df.index[df.index > entry_fill_date]
    current_sl = float(structural_sl)
    series = [(entry_fill_date, current_sl)]
    prev_macd, prev_signal = None, None
    # Need the crossover week's own PRIOR week too, even if that prior week
    # is before entry_fill_date (e.g. the breakout week itself) — otherwise
    # the very first post-entry week could never be detected as a crossover.
    lookback = df.index[df.index <= entry_fill_date]
    if len(lookback):
        last_row = df.loc[lookback[-1]]
        prev_macd, prev_signal = last_row["macd_line"], last_row["macd_signal"]

    for week_end in weeks_after_entry:
        row = df.loc[week_end]
        macd_line, macd_signal = row["macd_line"], row["macd_signal"]
        if pd.notna(macd_line) and pd.notna(macd_signal) and pd.notna(prev_macd) and pd.notna(prev_signal):
            was_bullish_or_flat = prev_macd >= prev_signal
            now_bearish = macd_line < macd_signal
            if was_bullish_or_flat and now_bearish:
                current_sl = max(current_sl, round(float(row["Low"]), 2))
        series.append((week_end.date() if hasattr(week_end, "date") else week_end, current_sl))
        prev_macd, prev_signal = macd_line, macd_signal

    return series


def _levels_for(trade: dict, has_trail_series: bool = False) -> list[tuple[float, str, str, str]]:
    """(price, color, linestyle, label) for every static reference line —
    entry, -8% floor, structural SL, final trail SL (if it moved), 1R/2R/3R,
    target. All computed once at entry/exit time, nothing here needs a
    simulator replay."""
    entry = trade.get("entryFillPrice") or trade.get("entryTriggerPrice")
    structural_sl = trade.get("structuralSl")
    trail_sl = trade.get("trailSl")
    risk = trade.get("riskPerShare")
    target = trade.get("targetPrice")

    levels: list[tuple[float, str, str, str]] = []
    if entry:
        levels.append((entry, "#3378ff", "-", "Entry"))
        levels.append((round(entry * 0.92, 2), "#dc2626", "--", "-8%"))
    if structural_sl:
        levels.append((float(structural_sl), "#dc2626", "-", "Structural SL"))
    # When a trail_sl_series is being plotted (WEEKLY_BOX_BREAKOUT trades),
    # that step line already shows the trail SL — a flat hline at just the
    # FINAL value would be redundant/misleading (looks like it was there the
    # whole trade) so it's skipped in favor of the evolving line.
    if not has_trail_series and trail_sl and structural_sl and abs(float(trail_sl) - float(structural_sl)) > 0.01:
        levels.append((float(trail_sl), "#f59e0b", "--", "Trail SL"))
    if entry and risk:
        for n in (1, 2, 3):
            levels.append((round(entry + n * float(risk), 2), "#16a34a", ":", f"{n}R"))
    if target and (not risk or abs(float(target) - (entry + 2 * float(risk))) > 0.5):
        levels.append((float(target), "#a855f7", "-.", "Target"))
    return levels


def render_trade_chart(df: pd.DataFrame, trade: dict, symbol: str,
                        trail_sl_series: list[tuple[date, float]] | None = None) -> bytes:
    import matplotlib.pyplot as plt
    import mplfinance as mpf

    d = df[["open", "high", "low", "close", "volume"]].astype(float).copy()
    levels = _levels_for(trade, has_trail_series=bool(trail_sl_series))

    apd = []
    if len(d) >= 50:
        apd.append(mpf.make_addplot(
            d["close"].ewm(span=50, adjust=False).mean(),
            color="#EF9F27", width=1.3, alpha=0.7,
        ))

    hlines = None
    if levels:
        hlines = dict(
            hlines=[v for v, *_ in levels],
            colors=[c for _, c, _, _ in levels],
            linestyle=[s for _, _, s, _ in levels],
            linewidths=1.5,
        )

    style = mpf.make_mpf_style(base_mpf_style="yahoo", gridcolor="#eeeeee",
                                y_on_right=True, rc={"font.size": 11})
    title = (f"{symbol} — {trade.get('status')} "
             f"({trade.get('entryType') or 'entry'}"
             f"{', ' + trade['exitReason'] if trade.get('exitReason') else ''})")

    fig, axlist = mpf.plot(
        d, type="candle", volume=True, addplot=apd or None, style=style,
        figsize=(24, 11), title=title, ylabel="Price", ylabel_lower="Volume",
        returnfig=True, hlines=hlines,
        update_width_config=dict(candle_linewidth=1.4, candle_width=0.75, volume_width=0.75),
    )
    ax = axlist[0]
    idx = d.index
    span_hi = float(d["high"].max())
    span_lo = float(d["low"].min())
    pad = (span_hi - span_lo) * 0.09

    entry_date = trade.get("entryFillDate")
    exit_date = trade.get("exitDate")
    if entry_date and exit_date:
        pos = idx.get_indexer([pd.Timestamp(entry_date), pd.Timestamp(exit_date)], method="nearest")
        if (pos >= 0).all():
            ax.axvspan(pos[0], pos[1], color="#94a3b8", alpha=0.08)

    def mark(date_val, color, label):
        if not date_val:
            return
        pos = idx.get_indexer([pd.Timestamp(date_val)], method="nearest")[0]
        if pos < 0:
            return
        candle_high = float(d["high"].iloc[pos])
        # thick stem + big arrowhead + a filled dot right at the candle tip —
        # a thin line here was easy to miss at a glance, this isn't.
        ax.annotate("", xy=(pos, candle_high), xytext=(pos, candle_high + pad),
                    arrowprops=dict(arrowstyle="-|>,head_width=0.6,head_length=1.0",
                                     color=color, lw=3.2, shrinkA=0, shrinkB=0),
                    annotation_clip=False)
        ax.plot(pos, candle_high, marker="o", markersize=7, color=color,
                markeredgecolor="white", markeredgewidth=1.2, zorder=5)
        ax.annotate(label, xy=(pos, candle_high + pad), xytext=(0, 6),
                    textcoords="offset points", ha="center", va="bottom",
                    fontsize=11, fontweight="bold", color=color, annotation_clip=False)

    mark(entry_date, "#3378ff", "Entry")
    win = (trade.get("realizedPnl") or 0) > 0
    mark(exit_date, "#16a34a" if win else "#dc2626", "Exit")

    if trail_sl_series and len(trail_sl_series) >= 2:
        dates, vals = zip(*trail_sl_series)
        pos = idx.get_indexer([pd.Timestamp(x) for x in dates], method="nearest")
        keep = pos >= 0
        pos, vals = pos[keep], [v for v, k in zip(vals, keep) if k]
        if len(pos) >= 2:
            # steps-post: the SL is flat between weekly re-evaluations, then
            # jumps up the instant a bearish MACD crossover ratchets it — a
            # straight diagonal line between points would misleadingly imply
            # the stop moved gradually all week.
            ax.step(pos, vals, where="post", color="#f59e0b", linewidth=1.8,
                     linestyle="--", zorder=4, label="Trail SL (weekly MACD)")
            ax.annotate(f"Trail SL {vals[-1]:g}", xy=(pos[-1], vals[-1]),
                        xytext=(6, -4), textcoords="offset points", fontsize=9,
                        color="#f59e0b", fontweight="bold", va="top", annotation_clip=False)

    ax.set_ylim(span_lo - pad, span_hi + pad * 2.6)

    # room on the left for the inline line labels sitting just outside the axes
    fig.subplots_adjust(left=0.12)
    for v, c, s, lbl in levels:
        ax.annotate(f"{lbl} {v:g}", xy=(0.0, v), xycoords=("axes fraction", "data"),
                    xytext=(-6, 0), textcoords="offset points", fontsize=9, color=c,
                    va="center", ha="right", fontweight="bold", annotation_clip=False)

    buf = io.BytesIO()
    fig.savefig(buf, dpi=130, pad_inches=0.3)
    buf.seek(0)
    png = buf.getvalue()
    plt.close(fig)  # same leak fix as ai_analysis/charting/render.py — see there for why
    return png
