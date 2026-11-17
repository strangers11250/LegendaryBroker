#!/usr/bin/env bash
# Run with sudo, passing an already extracted, verified release directory.
set -Eeuo pipefail
candidate=$(realpath "${1:?release directory required}")
case "$candidate" in /opt/legendary-market/releases/*) ;; *) echo 'Invalid release path' >&2; exit 1;; esac
test -f "$candidate/release.json"
test -f "$candidate/web/dist/index.html"
test -s /etc/legendary-market/market.env
site=/etc/nginx/sites-available/my-https
test -f "$site"
exec 9>/run/lock/legendary-market-deploy.lock
flock -n 9 || { echo 'Another deployment is running' >&2; exit 1; }
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup=/opt/legendary-market/backups/$stamp
mkdir -p "$backup"
chmod 0700 "$backup"
previous=
if [ -L /opt/legendary-market/current ]; then previous=$(readlink -f /opt/legendary-market/current); fi
cp -a "$site" "$backup/nginx-site"
managed_files=(/etc/systemd/system/legendary-market-api.service /etc/systemd/system/legendary-market-ingest.service /etc/nginx/conf.d/legendary-market-rate.conf /etc/nginx/legendary-market.conf)
for file in "${managed_files[@]}"; do
    if [ -f "$file" ]; then cp -a "$file" "$backup/$(basename "$file")"; fi
done
bot_before=$(systemctl show discord-bot -p MainPID --value)
cp -a /etc/systemd/system/discord-bot.service "$backup/discord-unit"
probe_pid=
switched=0
rollback() {
    rc=$?
    if [ -n "$probe_pid" ]; then kill "$probe_pid" 2>/dev/null || true; fi
    if [ "$rc" -ne 0 ] && [ "$switched" -eq 1 ]; then
        systemctl stop legendary-market-api legendary-market-ingest || true
        cp -a "$backup/nginx-site" "$site"
        for file in "${managed_files[@]}"; do
            saved="$backup/$(basename "$file")"
            if [ -f "$saved" ]; then cp -a "$saved" "$file"; else rm -f -- "$file"; fi
        done
        systemctl daemon-reload
        if [ -n "$previous" ]; then
            ln -sfn "$previous" /opt/legendary-market/current
            systemctl restart legendary-market-api legendary-market-ingest || true
        else
            systemctl disable --now legendary-market-api legendary-market-ingest || true
            if [ -L /opt/legendary-market/current ]; then unlink /opt/legendary-market/current; fi
        fi
        nginx -t && systemctl reload nginx || true
        echo "Deployment failed; restored previous routing/release. Backup: $backup" >&2
    fi
    exit "$rc"
}
trap rollback EXIT
id legendary-market >/dev/null 2>&1 || useradd --system --home /var/lib/legendary-market --shell /usr/sbin/nologin legendary-market
install -d -o legendary-market -g legendary-market -m 0750 /var/lib/legendary-market
python3 -m venv "$candidate/.venv"
"$candidate/.venv/bin/python" -m pip install --disable-pip-version-check -r "$candidate/requirements-market.lock"
# Verify the candidate with a scratch DB and localhost-only port before touching production.
probe_dir=$(mktemp -d /var/lib/legendary-market/probe.XXXXXX)
chown legendary-market:legendary-market "$probe_dir"
runuser -u legendary-market -- env PYTHONPATH="$candidate/src" BROKER_DB_PATH="$probe_dir/market.sqlite3" BROKER_WEB_DIR="$candidate/web/dist" "$candidate/.venv/bin/python" -m uvicorn legendary_broker.api:app --host 127.0.0.1 --port 18765 >"$backup/probe.log" 2>&1 &
probe_pid=$!
for attempt in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:18765/api/health >"$backup/health.json"; then break; fi
    sleep 1
done
curl -fsS http://127.0.0.1:18765/api/health
curl -fsS http://127.0.0.1:18765/web/ >/dev/null
kill "$probe_pid"
wait "$probe_pid" || true
probe_pid=
if [ -f /var/lib/legendary-market/market.sqlite3 ]; then
    "$candidate/.venv/bin/python" - /var/lib/legendary-market/market.sqlite3 "$backup/market.sqlite3" <<'PY'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as source, sqlite3.connect(sys.argv[2]) as target:
    source.backup(target)
PY
    chmod 0600 "$backup/market.sqlite3"
fi
switched=1
install -m 0644 "$candidate/deploy/legendary-market-api.service" /etc/systemd/system/legendary-market-api.service
install -m 0644 "$candidate/deploy/legendary-market-ingest.service" /etc/systemd/system/legendary-market-ingest.service
install -m 0644 "$candidate/deploy/nginx-rate-limit.conf" /etc/nginx/conf.d/legendary-market-rate.conf
install -m 0644 "$candidate/deploy/nginx-market.conf" /etc/nginx/legendary-market.conf
ln -sfn "$candidate" /opt/legendary-market/current
python3 - "$site" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
directive = '    include /etc/nginx/legendary-market.conf;\n'
if directive not in text:
    position = text.rfind('}')
    if position < 0:
        raise SystemExit('Expected HTTPS server block')
    text = text[:position] + directive + text[position:]
    path.write_text(text)
PY
nginx -t
systemctl daemon-reload
systemctl enable legendary-market-api legendary-market-ingest
systemctl restart legendary-market-api legendary-market-ingest
for attempt in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8765/api/health >/dev/null; then break; fi
    sleep 1
done
curl -fsS http://127.0.0.1:8765/api/health
# A successful live provider fetch is required before publishing this candidate.
for attempt in $(seq 1 60); do
    if curl -fsS http://127.0.0.1:8765/api/status | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("last_success") and not d.get("last_error") and not d.get("running") else 1)'; then break; fi
    sleep 2
done
curl -fsS http://127.0.0.1:8765/api/status | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("last_success") and not d.get("last_error") and not d.get("running"), "Provider fetch did not succeed"'
systemctl reload nginx
curl -fsS https://13.184.199.172/api/health
curl -fsS https://13.184.199.172/web/ >/dev/null
test "$(systemctl show discord-bot -p MainPID --value)" = "$bot_before"
cmp /etc/systemd/system/discord-bot.service "$backup/discord-unit"
echo "Deployed $candidate; rollback snapshot $backup"
