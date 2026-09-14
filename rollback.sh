#!/usr/bin/env bash
set -euo pipefail
SYNC=/opt/sing-box-sync; APP=/opt/sing-box
[ "$(id -u)" = 0 ] || { echo "请使用 root"; exit 1; }
if [ "${1:-}" = --list ]; then find "$SYNC/backups" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -r; exit 0; fi
version="${1:-}"; [ -n "$version" ] || version="$(find "$SYNC/backups" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -r | head -1)"
backup="$SYNC/backups/$version"; [ -d "$backup" ] || { echo "备份不存在: $version"; exit 1; }
systemctl stop xboard-sync xboard-report
cp -a "$backup/program/." "$SYNC/"; cp -a "$backup/config/." "$APP/config/" 2>/dev/null || true; cp -a "$backup"/docker-compose*.yml "$APP/" 2>/dev/null || true; cp -a "$backup/systemd/." /etc/systemd/system/
if [ "${2:-}" = --include-state ]; then cp -a "$backup/state/." "$SYNC/"; echo "已按要求回滚密钥、流量状态和 custom"; fi
chmod 600 "$SYNC/.env" "$SYNC/reality_keys.json" "$SYNC/report_pending.json" "$SYNC/report_state.json" 2>/dev/null || true
systemctl daemon-reload; cd "$APP"; docker compose up -d; systemctl restart xboard-sync xboard-report; "$SYNC/healthcheck.sh"
echo "已回滚到 $version（Reality 密钥和流量状态未回滚）"
