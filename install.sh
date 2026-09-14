#!/usr/bin/env bash
set -euo pipefail
REPO="xiaofujie369/xboard-singbox-anyreality-V1"; BRANCH="main"; RAW="https://raw.githubusercontent.com/$REPO/$BRANCH"
APP=/opt/sing-box; SYNC=/opt/sing-box-sync; SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; STAGE="preflight"
trap 'echo "[ERROR] 安装阶段 $STAGE 失败（第 $LINENO 行）；未删除已有密钥或状态。" >&2' ERR
[ "$(id -u)" = 0 ] || { echo "请使用 root 运行"; exit 1; }
[ -d /run/systemd/system ] || { echo "需要 systemd"; exit 1; }
[ -r /etc/os-release ] || { echo "无法识别系统"; exit 1; }
# shellcheck disable=SC1091
. /etc/os-release
case "${ID:-}" in debian|ubuntu) :;; *) echo "仅支持 Debian 12/13、Ubuntu 22.04/24.04"; exit 1;; esac
case "$(uname -m)" in x86_64|aarch64|arm64) :;; *) echo "仅支持 amd64/arm64"; exit 1;; esac
[ "$(df -Pm /opt 2>/dev/null | awk 'NR==2{print $4}')" -ge 2048 ] || { echo "/opt 至少需要 2GB 可用空间"; exit 1; }
if [ -e "$SYNC/.env" ]; then echo "检测到已有安装。请使用 sbr update，安装器不会覆盖。"; exit 1; fi

PANEL_URL="${PANEL_URL:-}"; PANEL_TOKEN="${PANEL_TOKEN:-}"; NODES="${NODES:-}"; REALITY_SERVER_NAME="${REALITY_SERVER_NAME:-}"; INSTALL_MODE="${INSTALL_MODE:-image}"
if [ -t 0 ]; then
  [ -n "$PANEL_URL" ] || read -rp "XBoard 面板地址: " PANEL_URL
  [ -n "$PANEL_TOKEN" ] || { read -rsp "XBoard TOKEN: " PANEL_TOKEN; echo; }
  [ -n "$NODES" ] || read -rp "节点（如 100:anytls,101:vless）: " NODES
fi
if [ -z "$PANEL_URL" ] || [ -z "$PANEL_TOKEN" ] || [ -z "$NODES" ]; then
  echo "非交互安装必须提供 PANEL_URL、PANEL_TOKEN、NODES"
  exit 1
fi
[[ "$PANEL_URL" =~ ^https?:// ]] || { echo "PANEL_URL 必须以 http:// 或 https:// 开头"; exit 1; }
[[ "$NODES" =~ ^[0-9]+:(anytls|anyreality|vless)(,[0-9]+:(anytls|anyreality|vless))*$ ]] || { echo "NODES 格式错误"; exit 1; }

STAGE="dependencies"; apt-get update; apt-get install -y ca-certificates curl python3 python3-requests jq git openssl iproute2 ufw logrotate
command -v docker >/dev/null 2>&1 || curl -fsSL https://get.docker.com | bash
systemctl enable docker --now; docker compose version >/dev/null 2>&1 || apt-get install -y docker-compose-plugin
curl -fsS --max-time 10 "$PANEL_URL" >/dev/null || { echo "无法连接 XBoard"; exit 1; }
curl -fsS --max-time 10 https://raw.githubusercontent.com/ >/dev/null; [ "$INSTALL_MODE" = build ] || docker manifest inspect ghcr.io/xiaofujie369/xboard-singbox-anyreality-v1:1.2.0 >/dev/null
STAGE="XBoard validation"; probe_dir="$(mktemp -d)"; chmod 700 "$probe_dir"; trap 'rm -rf "$probe_dir"' EXIT
IFS=',' read -ra node_items <<<"$NODES"
for item in "${node_items[@]}"; do
  node_id="${item%%:*}"; node_type="${item##*:}"; [ "$node_type" = anyreality ] && node_type=anytls
  for endpoint in config user; do
    code="$(curl -sS --max-time 25 -o "$probe_dir/$node_id-$endpoint.json" -w '%{http_code}' --get "$PANEL_URL/api/v1/server/UniProxy/$endpoint" --data-urlencode "node_id=$node_id" --data-urlencode "node_type=$node_type" --data-urlencode "token=$PANEL_TOKEN")"
    [ "$code" = 200 ] || { echo "XBoard $endpoint API 校验失败: node=$node_id HTTP=$code"; exit 1; }
    jq -e . "$probe_dir/$node_id-$endpoint.json" >/dev/null || { echo "XBoard $endpoint API 返回非 JSON: node=$node_id"; exit 1; }
  done
  port="$(jq -r '..|objects|.port? // .server_port? // empty' "$probe_dir/$node_id-config.json" | head -1)"
  if [[ "$port" =~ ^[0-9]+$ ]] && ss -H -ltn "sport = :$port" | grep -q .; then echo "端口 $port 已被占用:"; ss -ltnp "sport = :$port"; exit 1; fi
done
rm -rf "$probe_dir"; trap - EXIT

STAGE="files"; install -d -m 700 "$SYNC" "$SYNC/custom" "$SYNC/backups" "$SYNC/reality-scans"; install -d -m 750 "$APP" "$APP/config" "$APP/logs"; install -m 640 /dev/null "$APP/logs/access.log"
copy_or_download(){ local source="$1" destination="$2"; if [ -f "$SOURCE/$source" ]; then install -m 600 "$SOURCE/$source" "$destination"; else curl -fsSL "$RAW/$source" -o "$destination"; chmod 600 "$destination"; fi; }
for f in docker-compose.yml docker-compose.build.yml Dockerfile stats.proto anytls-source-ip.patch; do copy_or_download "sing-box/$f" "$APP/$f"; done
for f in xboard_sync.py xboard_report.py reality_scanner.py healthcheck.sh manage.sh; do copy_or_download "sync/$f" "$SYNC/$f"; done
for f in update.sh rollback.sh uninstall.sh VERSION; do copy_or_download "$f" "$SYNC/$f"; done
for f in xboard-sync.service xboard-report.service; do copy_or_download "systemd/$f" "/etc/systemd/system/$f"; done
chmod 700 "$SYNC"/*.py "$SYNC"/*.sh; install -m 755 "$SYNC/manage.sh" /usr/local/bin/sbr

if [ -z "$REALITY_SERVER_NAME" ] && [[ ",$NODES," =~ :anytls,|:anyreality, ]]; then
  if [ ! -t 0 ]; then echo "非交互首次安装必须设置 REALITY_SERVER_NAME（可先运行 sync/reality_scanner.py）"; exit 1; fi
  STAGE="reality scan"; python3 "$SYNC/reality_scanner.py" > "$SYNC/reality-scans/install.json"
  chmod 600 "$SYNC/reality-scans/install.json"; recommended="$(jq -r '.selected.domain' "$SYNC/reality-scans/install.json")"
  read -rp "Reality 推荐域名 [$recommended]，回车采用或输入其他域名: " REALITY_SERVER_NAME; REALITY_SERVER_NAME="${REALITY_SERVER_NAME:-$recommended}"
fi

cat > "$APP/config/config.json" <<'EOF'
{"log":{"level":"info","output":"/var/log/sing-box/access.log","timestamp":true},"inbounds":[],"outbounds":[{"type":"direct","tag":"direct"}]}
EOF
chmod 600 "$APP/config/config.json"
cat > "$SYNC/.env" <<EOF
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
REALITY_SCAN_ATTEMPTS=3
REALITY_SCAN_TIMEOUT=5
REALITY_SCAN_FAMILY=prefer_ipv4
REALITY_AUTO_APPLY=false
REALITY_SERVER_NAME=$REALITY_SERVER_NAME
REALITY_CANDIDATES=
CUSTOM_CONFIG_DIR=/opt/sing-box-sync/custom
TCP_ONLY=true
ALLOW_EMPTY_USERS=false
ALLOW_EMPTY_INBOUNDS=false
SYNC_INTERVAL=60
SYNC_JITTER=10
REPORT_INTERVAL=60
REPORT_JITTER=10
MAX_BACKOFF=600
HTTP_TIMEOUT=25
HTTP_RETRIES=2
REPORT_PENDING=/opt/sing-box-sync/report_pending.json
REPORT_USE_V2_REPORT=true
REPORT_V2_FALLBACK=true
REPORT_KERNEL_STATUS=true
REPORT_ONLINE_TTL=180
REPORT_MAX_IPS_PER_USER=10
REPORT_PRIVATE_IP=false
EOF
chmod 600 "$SYNC/.env"
cat > /etc/logrotate.d/xboard-singbox-anyreality <<'EOF'
/opt/sing-box/logs/*.log {
  daily
  size 50M
  rotate 7
  compress
  missingok
  notifempty
  copytruncate
  create 0640 root root
}
EOF

STAGE="container"; cd "$APP"
if [ "$INSTALL_MODE" = build ]; then docker compose -f docker-compose.yml -f docker-compose.build.yml build --pull; docker compose -f docker-compose.yml -f docker-compose.build.yml up -d
else docker compose pull || { echo "GHCR 拉取失败。如确认 VPS 内存足够，可重新执行 INSTALL_MODE=build 安装。"; exit 1; }; docker compose up -d; fi
STAGE="first sync"; python3 "$SYNC/xboard_sync.py" once
STAGE="services"; systemctl daemon-reload; systemctl enable --now xboard-sync xboard-report
"$SYNC/healthcheck.sh"; trap - ERR; echo "安装完成：sbr status"
