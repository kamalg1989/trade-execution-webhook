set -e
cd /root/blend_web
mkdir -p data /root/web-app/dist/blend
sed -i "s#OUT = '/root/web-app/dist/blend/data.json'#OUT = '/root/blend_web/data/data.json'#; s#Writes /root/web-app/dist/blend/data.json#Writes /root/blend_web/data/data.json (served by api.py with the dashboard key)#" build.py
rm -f /root/web-app/dist/blend/data.json
cp index.html /root/web-app/dist/blend/index.html

cat > deploy/blend-api.service <<'EOF'
[Unit]
Description=/blend/ dashboard API (A5 + OHM Pro, read-only, port 8012)
After=network.target postgresql.service
[Service]
WorkingDirectory=/root/blend_web
ExecStart=/root/trade-execution-webhook/venv/bin/uvicorn api:app --host 127.0.0.1 --port 8012 --workers 1
Restart=always
RestartSec=5
MemoryMax=150M
[Install]
WantedBy=multi-user.target
EOF
cat > deploy/blend-build.service <<'EOF'
[Unit]
Description=/blend/ data builder (read-only)
[Service]
Type=oneshot
WorkingDirectory=/root/blend_web
ExecStart=/root/trade-execution-webhook/venv/bin/python /root/blend_web/build.py
StandardOutput=append:/root/blend_web/build.log
StandardError=append:/root/blend_web/build.log
EOF
cat > deploy/blend-build.timer <<'EOF'
[Unit]
Description=/blend/ data builder every 10 min, 07:00-23:59
[Timer]
OnCalendar=*-*-* 07..23:00/10:00
Persistent=false
[Install]
WantedBy=timers.target
EOF
cp deploy/blend-*.service deploy/blend-build.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now blend-api.service blend-build.timer
/root/trade-execution-webhook/venv/bin/python build.py

NG=/etc/nginx/sites-enabled/trade-platform
if ! grep -q 'location ^~ /blend/' $NG; then
  cp $NG /root/nginx-trade-platform.bak-blend-$(date +%Y%m%d)
  python3 - <<'EOF'
p='/etc/nginx/sites-enabled/trade-platform'
s=open(p).read()
anchor='    # Telegram confirm webhook'
assert s.count(anchor)==1
blk='''    # ---- /blend/ A5 + OHM Pro dashboard (read-only) - 2026-11-25 -------------
    location ^~ /blend/api/ {
        proxy_pass http://127.0.0.1:8012/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 30s;
    }
    location = /blend { return 301 /blend/; }
    location ^~ /blend/ {
        try_files $uri $uri/ /blend/index.html;
        add_header Cache-Control "no-cache";
    }
'''
open(p,'w').write(s.replace(anchor, blk+anchor))
EOF
fi
nginx -t && systemctl reload nginx
sleep 2
echo "health: $(curl -s https://ohmstockvault.duckdns.org/blend/api/health)"
echo "no key: $(curl -s -o /dev/null -w '%{http_code}' https://ohmstockvault.duckdns.org/blend/api/data)"
K=$(python3 -c "import json;print(json.load(open('/root/trade-execution-webhook/api_key.json'))['api_key'])")
echo "with key: $(curl -s -o /dev/null -w '%{http_code}' -H "X-API-Key: $K" https://ohmstockvault.duckdns.org/blend/api/data)"
echo "page: $(curl -s -o /dev/null -w '%{http_code}' https://ohmstockvault.duckdns.org/blend/)"
echo "old public json: $(curl -s -o /dev/null -w '%{http_code}' https://ohmstockvault.duckdns.org/blend/data.json)"
