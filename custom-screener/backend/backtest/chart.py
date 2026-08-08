"""Per-trade annotated chart for backtest review.

Static PNG (same mplfinance approach as ai_analysis/charting/render.py,
including the plt.close(fig) fix — kept as a separate function rather than
extending the AI renderer so backtest-specific annotation logic doesn't leak
into the production AI charting path).

Plots: candles + EMA10/21/50 context, then horizontal lines for entry,
the always-on -8% floor, structural SL, the final trailing SL (if it moved
off structural), 1R/2R/3R, and the fixed target — all static values already
stored on the trade row, no simulator replay needed. Vertical lines mark the
entry-fill day and the exit day (colored green/red by win/loss).
"""
from __future__ import annotations

import io
from datetime import date

import pandas as pd

DAILY_EMAS = ((10, "#378ADD"), (21, "#1D9E75"), (50, "#EF9F27"))


async def load_trade_window(
    pool, symbol: str, anchor_start: date, anchor_end: date,
    bars_before: int = 40, bars_after: int = 10,
) -> pd.DataFrame | None:
    """OHLCV window: up to `bars_before` bars ending at anchor_start, the
    full anchor_start->anchor_end span uncapped (the trade's actual
    lifetime), then up to `bars_after` bars past anchor_end."""
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
    # plain DATE columns) — mplfinance's vlines compares against the index
    # directly and raises on a tz-naive/tz-aware mismatch otherwise.
    df["time"] = pd.to_datetime(df["time"]).dt.tz_localize(None)
    df = df.set_index("time").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    return df


def render_trade_chart(df: pd.DataFrame, trade: dict, symbol: str) -> bytes:
    import matplotlib.pyplot as plt
    import mplfinance as mpf
    from matplotlib.lines import Line2D

    d = df[["open", "high", "low", "close", "volume"]].astype(float).copy()

    apd = []
    for span, color in DAILY_EMAS:
        if len(d) >= span:
            apd.append(mpf.make_addplot(
                d["close"].ewm(span=span, adjust=False).mean(),
                color=color, width=1.0, alpha=0.6,
            ))

    entry = trade.get("entryFillPrice") or trade.get("entryTriggerPrice")
    structural_sl = trade.get("structuralSl")
    trail_sl = trade.get("trailSl")
    risk = trade.get("riskPerShare")
    target = trade.get("targetPrice")

    levels: list[tuple[float, str, str, str]] = []  # (price, color, linestyle, label)
    if entry:
        levels.append((entry, "#3378ff", "-", "Entry"))
        levels.append((round(entry * 0.92, 2), "#dc2626", "--", "-8%"))
    if structural_sl:
        levels.append((float(structural_sl), "#dc2626", "-", "Structural SL"))
    if trail_sl and structural_sl and abs(float(trail_sl) - float(structural_sl)) > 0.01:
        levels.append((float(trail_sl), "#f59e0b", "--", "Trail SL (final)"))
    if entry and risk:
        for n in (1, 2, 3):
            levels.append((round(entry + n * float(risk), 2), "#16a34a", ":", f"{n}R"))
    if target and (not risk or abs(float(target) - (entry + 2 * float(risk))) > 0.5):
        levels.append((float(target), "#a855f7", "-.", "Target"))

    hlines = None
    if levels:
        hlines = dict(
            hlines=[v for v, *_ in levels],
            colors=[c for _, c, _, _ in levels],
            linestyle=[s for _, _, s, _ in levels],
            linewidths=1.1,
        )

    vlines_ts, vlines_colors = [], []
    if trade.get("entryFillDate"):
        vlines_ts.append(pd.Timestamp(trade["entryFillDate"]))
        vlines_colors.append("#3378ff")
    elif trade.get("signalDate"):
        vlines_ts.append(pd.Timestamp(trade["signalDate"]))
        vlines_colors.append("#64748b")
    if trade.get("exitDate"):
        vlines_ts.append(pd.Timestamp(trade["exitDate"]))
        win = (trade.get("realizedPnl") or 0) > 0
        vlines_colors.append("#16a34a" if win else "#dc2626")

    style = mpf.make_mpf_style(base_mpf_style="charles", gridcolor="#dddddd", y_on_right=True)
    title = (f"{symbol} — {trade.get('status')} "
             f"({trade.get('entryType') or 'entry'}"
             f"{', ' + trade['exitReason'] if trade.get('exitReason') else ''})")
    kwargs = dict(
        type="candle", volume=True, addplot=apd or None, style=style,
        figsize=(16, 8), title=title, ylabel="Price", ylabel_lower="Volume",
        returnfig=True,
    )
    if hlines:
        kwargs["hlines"] = hlines
    if vlines_ts:
        kwargs["vlines"] = dict(vlines=vlines_ts, colors=vlines_colors,
                                 linestyle="-.", linewidths=1.4, alpha=0.85)

    fig, axlist = mpf.plot(d, **kwargs)
    if levels:
        handles = [Line2D([0], [0], color=c, lw=1.6, linestyle=s, label=lbl)
                   for _, c, s, lbl in levels]
        axlist[0].legend(handles=handles, loc="upper left", fontsize=7,
                          framealpha=0.75, ncol=3)

    buf = io.BytesIO()
    fig.savefig(buf, dpi=100, pad_inches=0.3)
    buf.seek(0)
    png = buf.getvalue()
    plt.close(fig)  # same leak fix as ai_analysis/charting/render.py — see there for why
    return png
