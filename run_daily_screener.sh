#!/bin/bash
# Daily quant screener run with retry-on-failure.
# screen_gpt.py writes latest_recommendations.json and, at the end of its own
# run, fire-and-forgets ai_rank_candidates.py (the Gemini AI ranking pass,
# which writes latest_ai_picks.json) — so one successful run here covers both.
cd /root/trade-execution-webhook || exit 1
LOG=/root/trade-execution-webhook/screener_scan.log
MAX_ATTEMPTS=3
RETRY_DELAY=120  # seconds between retries

for attempt in $(seq 1 $MAX_ATTEMPTS); do
    echo "=== [$(date '+%Y-%m-%d %H:%M:%S')] Daily screener run — attempt $attempt/$MAX_ATTEMPTS ===" >> "$LOG"
    ./venv/bin/python3 screen_gpt.py >> "$LOG" 2>&1
    STATUS=$?
    if [ $STATUS -eq 0 ]; then
        echo "=== [$(date '+%Y-%m-%d %H:%M:%S')] Screener run succeeded (attempt $attempt) ===" >> "$LOG"
        exit 0
    fi
    echo "=== [$(date '+%Y-%m-%d %H:%M:%S')] Screener run FAILED (exit $STATUS), attempt $attempt/$MAX_ATTEMPTS ===" >> "$LOG"
    if [ $attempt -lt $MAX_ATTEMPTS ]; then
        sleep $RETRY_DELAY
    fi
done
echo "=== [$(date '+%Y-%m-%d %H:%M:%S')] Daily screener run FAILED after $MAX_ATTEMPTS attempts — giving up ===" >> "$LOG"
exit 1
