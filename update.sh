#!/usr/bin/env bash
set -euo pipefail
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ "$(id -u)" = 0 ] || { echo "请使用 root 运行"; exit 1; }
[ -f "$SOURCE_DIR/sing-box/Dockerfile" ] || { echo "请在新仓库的项目目录运行 update.sh"; exit 1; }
systemctl stop xboard-sync xboard-report 2>/dev/null || true
mkdir -p /opt/sing-box-sync/backup
STAMP="$(date +%Y%m%d-%H%M%S)"
for f in xboard_sync.py xboard_report.py reality_scanner.py healthcheck.sh manage.sh; do cp "/opt/sing-box-sync/$f" "/opt/sing-box-sync/backup/$f.$STAMP" 2>/dev/null || true; done
cp "$SOURCE_DIR/sing-box/Dockerfile" "$SOURCE_DIR/sing-box/docker-compose.yml" "$SOURCE_DIR/sing-box/stats.proto" /opt/sing-box/
for f in xboard_sync.py xboard_report.py reality_scanner.py healthcheck.sh manage.sh; do cp "$SOURCE_DIR/sync/$f" "/opt/sing-box-sync/$f"; done
cp "$SOURCE_DIR"/systemd/*.service /etc/systemd/system/
cp /opt/sing-box-sync/manage.sh /usr/local/bin/sbr
chmod +x /opt/sing-box-sync/*.py /opt/sing-box-sync/*.sh /usr/local/bin/sbr
cd /opt/sing-box
docker compose build --pull
docker compose up -d
systemctl daemon-reload
python3 /opt/sing-box-sync/xboard_sync.py once
systemctl restart xboard-sync xboard-report
echo "更新完成"
