#!/bin/bash
# Install systemd units for paper trading. ADDITIVE: new units only, nothing
# existing is modified. Remove with:
#   systemctl disable --now paper-mark.timer paper-rebalance.timer
#   rm /etc/systemd/system/paper-{mark,rebalance}.{service,timer}
set -e

cat > /etc/systemd/system/paper-mark.service <<'EOF'
[Unit]
Description=Paper trading - daily mark-to-market (POSITIONAL momentum #823)
After=network.target postgresql.service

[Service]
Type=oneshot
User=root
WorkingDirectory=/root/trade-execution-webhook
EnvironmentFile=/root/trade-execution-webhook/.env
ExecStart=/root/trade-execution-webhook/venv/bin/python \
  /root/trade-execution-webhook/paper_trading/paper_positional.py --mark
EOF

cat > /etc/systemd/system/paper-mark.timer <<'EOF'
[Unit]
Description=Daily paper-book mark, after the market-data pipeline has run
[Timer]
OnCalendar=Mon..Fri 19:15 Asia/Kolkata
Persistent=true
[Install]
WantedBy=timers.target
EOF

cat > /etc/systemd/system/paper-rebalance.service <<'EOF'
[Unit]
Description=Paper trading - 21-session rebalance (POSITIONAL momentum #823)
After=network.target postgresql.service

[Service]
Type=oneshot
User=root
WorkingDirectory=/root/trade-execution-webhook
EnvironmentFile=/root/trade-execution-webhook/.env
ExecStart=/root/trade-execution-webhook/venv/bin/python \
  /root/trade-execution-webhook/paper_trading/paper_positional.py --rebalance
EOF

cat > /etc/systemd/system/paper-rebalance.timer <<'EOF'
[Unit]
Description=Paper rebalance check (the script enforces the 21-session cadence itself)
[Timer]
OnCalendar=Mon..Fri 19:20 Asia/Kolkata
Persistent=true
[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now paper-mark.timer paper-rebalance.timer
systemctl list-timers 'paper-*' --no-pager
