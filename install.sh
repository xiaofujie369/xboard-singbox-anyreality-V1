#!/usr/bin/env bash
set -euo pipefail

REPO="xiaofujie369/xboard-singbox-anyreality-V1"
BRANCH="main"
RAW_BASE="https://raw.githubusercontent.com/${REPO}/${BRANCH}"
APP_DIR="/opt/sing-box"
SYNC_DIR="/opt/sing-box-sync"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[ "$(id -u)" = 0 ] || { echo "请使用 root 运行"; exit 1; }
read -rp "XBoard 面板地址: " PANEL_URL
read -rsp "XBoard 通讯密钥 TOKEN: " PANEL_TOKEN; echo
read -rp "AnyTLS 节点列表（如 100:anytls,101:anytls）: " NODES
[ -n "$PANEL_URL" ] && [ -n "$PANEL_TOKEN" ] && [ -n "$NODES" ] || { echo "三项均不能为空"; exit 1; }

apt-get update
apt-get install -y ca-certificates curl python3 python3-requests jq git openssl iproute2 ufw
command -v docker >/dev/null 2>&1 || curl -fsSL https://get.docker.com | bash
systemctl enable docker --now
docker compose version >/dev/null 2>&1 || apt-get install -y docker-compose-plugin

mkdir -p "$APP_DIR/config" "$APP_DIR/logs" "$SYNC_DIR"
touch "$APP_DIR/logs/access.log"
chmod 777 "$APP_DIR/logs"; chmod 666 "$APP_DIR/logs/access.log"

if [ -f "$SOURCE_DIR/sing-box/docker-compose.yml" ]; then
  cp "$SOURCE_DIR/sing-box/docker-compose.yml" "$APP_DIR/docker-compose.yml"
  cp "$SOURCE_DIR/sing-box/Dockerfile" "$APP_DIR/Dockerfile"
  cp "$SOURCE_DIR/sing-box/stats.proto" "$APP_DIR/stats.proto"
  for f in xboard_sync.py xboard_report.py reality_scanner.py healthcheck.sh manage.sh; do cp "$SOURCE_DIR/sync/$f" "$SYNC_DIR/$f"; done
  cp "$SOURCE_DIR"/systemd/*.service /etc/systemd/system/
else
  curl -fsSL "$RAW_BASE/sing-box/docker-compose.yml" -o "$APP_DIR/docker-compose.yml"
  curl -fsSL "$RAW_BASE/sing-box/Dockerfile" -o "$APP_DIR/Dockerfile"
  curl -fsSL "$RAW_BASE/sing-box/stats.proto" -o "$APP_DIR/stats.proto"
  for f in xboard_sync.py xboard_report.py reality_scanner.py healthcheck.sh manage.sh; do curl -fsSL "$RAW_BASE/sync/$f" -o "$SYNC_DIR/$f"; done
  curl -fsSL "$RAW_BASE/systemd/xboard-sync.service" -o /etc/systemd/system/xboard-sync.service
  curl -fsSL "$RAW_BASE/systemd/xboard-report.service" -o /etc/systemd/system/xboard-report.service
fi

cat > "$APP_DIR/config/config.json" <<'EOF'
{"log":{"level":"info","output":"/var/log/sing-box/access.log","timestamp":true},"inbounds":[],"outbounds":[{"type":"direct","tag":"direct"}]}
EOF
cat > "$SYNC_DIR/.env" <<EOF
PANEL_URL=$PANEL_URL
PANEL_TOKEN=$PANEL_TOKEN
NODES=$NODES
SING_BOX_CONFIG=/opt/sing-box/config/config.json
SING_BOX_CONTAINER=sing-box
SING_BOX_PRESTART_TEST=true
SING_BOX_CONFIG_BACKUPS=3
SING_BOX_ACCESS_LOG=/opt/sing-box/logs/access.log
REALITY_KEY_STATE=/opt/sing-box-sync/reality_keys.json
REALITY_SCAN_DIR=/opt/sing-box-sync/reality-scans
REALITY_SCAN_TIMEOUT=5
# REALITY_SERVER_NAME 留空时按 VPS 出口位置和实测延迟自动选择
REALITY_SERVER_NAME=
# 可选：优先测试的自定义候选域名，逗号分隔
REALITY_CANDIDATES=
SYNC_INTERVAL=60
REPORT_USE_V2_REPORT=true
REPORT_V2_FALLBACK=true
REPORT_KERNEL_STATUS=true
REPORT_ONLINE_TTL=180
EOF
chmod 600 "$SYNC_DIR/.env"
chmod +x "$SYNC_DIR"/*.py "$SYNC_DIR"/*.sh
cp "$SYNC_DIR/manage.sh" /usr/local/bin/sbr
chmod +x /usr/local/bin/sbr

cd "$APP_DIR"
docker compose build --pull
docker compose up -d
systemctl daemon-reload
systemctl enable xboard-sync xboard-report
python3 "$SYNC_DIR/xboard_sync.py" once
systemctl restart xboard-sync xboard-report
echo "安装完成。运行 sbr 管理，或 $SYNC_DIR/healthcheck.sh 检查。"
