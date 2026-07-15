#!/bin/bash
# Daily market-data pipeline — replaces the old split cron(18:00)+systemd-timer(18:30)
# setup, which raced: compute assumed the OHLCV gap-fill would always finish within
# 30 minutes, and had no way to retry a day whose data simply wasn't published yet.
#
# Runs sequentially, in order, so each step only starts once the previous one has
# actually finished (no more fixed-time-buffer guessing):
#   1. update_ohlcv.py    - historical gap-fill, full ~2700-symbol universe.
#                            (Dhan's EOD historical API publishes a trading day's
#                            candle the NEXT morning, so this step fills in
#                            YESTERDAY and earlier, never "today".)
#   2. update_today.py     - NIFTY-500 same-day candle via intraday aggregation.
#                            Was previously dashboard-button-only / never scheduled,
#                            which is the only way "today" gets any data same-day.
#   3. compute_stock_indicators.py - self-healing trailing-window indicator +
#                            market_snapshot compute (see that script's docstring).
#                            Runs last so it sees whatever steps 1-2 just wrote.
#
# A failure in one step is logged but does not stop the rest of the chain — e.g. if
# Dhan's intraday API is briefly down, step 3 still runs and will complete
# yesterday's data; step 3's own self-healing window will pick up anything step 2
# missed on a later run once data is available.
set -uo pipefail

cd /root/trade-execution-webhook
LOG_DIR="market_data_setup/scripts"
PY="./venv/bin/python"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

log "=== daily_pipeline.sh starting ==="

log "--- Step 1/3: update_ohlcv.py (historical gap-fill) ---"
if $PY market_data_setup/scripts/update_ohlcv.py >> "$LOG_DIR/update.log" 2>&1; then
    log "Step 1 OK"
else
    log "⚠️  Step 1 (update_ohlcv.py) FAILED - check $LOG_DIR/update.log"
fi

log "--- Step 2/3: update_today.py (NIFTY-500 same-day candle) ---"
if $PY market_data_setup/scripts/update_today.py >> "$LOG_DIR/update.log" 2>&1; then
    log "Step 2 OK"
else
    log "⚠️  Step 2 (update_today.py) FAILED - check $LOG_DIR/update.log"
fi

log "--- Step 3/3: compute_stock_indicators.py (self-healing indicators + snapshot) ---"
cd custom-screener/backend
if /root/trade-execution-webhook/venv/bin/python -m compute.compute_stock_indicators >> "/root/trade-execution-webhook/$LOG_DIR/compute.log" 2>&1; then
    log "Step 3 OK"
else
    log "⚠️  Step 3 (compute_stock_indicators) FAILED - check $LOG_DIR/compute.log"
fi

log "=== daily_pipeline.sh finished ==="
