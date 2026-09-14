#!/usr/bin/env bash
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "请使用 root"; exit 1; }
echo "1) 仅卸载程序，保留全部配置/密钥/状态（默认）"
echo "2) 删除程序和运行配置，保留 Reality 密钥"
echo "3) 完全删除"
echo "4) 取消"
read -rp "选择 [1]: " choice; choice="${choice:-1}"
[ "$choice" != 4 ] || exit 0
if [ "$choice" = 3 ]; then read -rp "完全删除不可恢复，请输入 DELETE: " confirm; [ "$confirm" = DELETE ] || { echo "已取消"; exit 1; }; fi
systemctl disable --now xboard-sync xboard-report 2>/dev/null || true
rm -f /etc/systemd/system/xboard-sync.service /etc/systemd/system/xboard-report.service /etc/logrotate.d/xboard-singbox-anyreality /usr/local/bin/sbr
systemctl daemon-reload; cd /opt/sing-box 2>/dev/null && docker compose down || docker rm -f sing-box 2>/dev/null || true
case "$choice" in
  1) echo "程序服务已卸载；/opt 数据全部保留";;
  2) mkdir -p /opt/xboard-anyreality-key-backup; cp -a /opt/sing-box-sync/reality_keys.json /opt/xboard-anyreality-key-backup/ 2>/dev/null || true; rm -rf /opt/sing-box /opt/sing-box-sync; chmod 700 /opt/xboard-anyreality-key-backup; echo "Reality 密钥保存在 /opt/xboard-anyreality-key-backup";;
  3) rm -rf /opt/sing-box /opt/sing-box-sync /opt/xboard-anyreality-key-backup; echo "已完全删除";;
  *) echo "无效选择"; exit 1;;
esac
