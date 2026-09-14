#!/usr/bin/env bash
set -u
APP=/opt/sing-box
SYNC=/opt/sing-box-sync
[ "$(id -u)" = 0 ] || { echo "请使用 sudo sbr"; exit 1; }
pause(){ read -rp "按 Enter 返回..." _; }
while true; do
  clear
  echo "XBoard sing-box AnyReality 管理"
  echo "1) 状态  2) 立即同步  3) 重启  4) 日志"
  echo "5) 查看节点  6) 配置校验  7) 防火墙放行  8) 编辑环境配置"
  echo "9) 测试 Reality 候选域名  0) 退出"
  read -rp "选择: " n
  case "$n" in
    1) docker ps -a --filter name=sing-box; systemctl status xboard-sync xboard-report --no-pager ;;
    2) python3 "$SYNC/xboard_sync.py" once ;;
    3) docker restart sing-box; systemctl restart xboard-sync xboard-report ;;
    4) journalctl -u xboard-sync -u xboard-report -n 100 --no-pager; docker logs sing-box --tail 100 ;;
    5) jq '.inbounds[]|{tag,type,listen_port,users:(.users|length),sni:.tls.server_name,reality:.tls.reality.enabled}' "$APP/config/config.json" ;;
    6) docker exec sing-box sing-box check -c /etc/sing-box/config.json; "$SYNC/healthcheck.sh" ;;
    7) while read -r p; do ufw allow "$p/tcp"; done < <(jq -r '.inbounds[]?.listen_port' "$APP/config/config.json"); ufw status ;;
    8) "${EDITOR:-nano}" "$SYNC/.env"; chmod 600 "$SYNC/.env" ;;
    9) python3 "$SYNC/reality_scanner.py" ;;
    0) exit 0 ;;
    *) echo "无效选择" ;;
  esac
  pause
done
