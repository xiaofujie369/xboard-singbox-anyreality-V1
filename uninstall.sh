#!/usr/bin/env bash
set -e
[ "$(id -u)" = 0 ] || { echo "请使用 root 运行"; exit 1; }
read -rp "将移除服务和 sing-box 容器，保留 /opt 配置。继续？[y/N] " answer
[[ "$answer" =~ ^[Yy]$ ]] || exit 0
systemctl disable --now xboard-sync xboard-report 2>/dev/null || true
rm -f /etc/systemd/system/xboard-sync.service /etc/systemd/system/xboard-report.service /usr/local/bin/sbr
systemctl daemon-reload
cd /opt/sing-box 2>/dev/null && docker compose down || docker rm -f sing-box 2>/dev/null || true
echo "已移除服务；配置仍在 /opt/sing-box 和 /opt/sing-box-sync，可手动备份或删除。"
