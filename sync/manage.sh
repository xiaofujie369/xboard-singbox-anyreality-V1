#!/usr/bin/env bash
set -euo pipefail
APP=/opt/sing-box; SYNC=/opt/sing-box-sync; ENV_FILE="$SYNC/.env"; KEYS="$SYNC/reality_keys.json"; CONFIG="$APP/config/config.json"
need_root(){ [ "$(id -u)" = 0 ] || { echo "请使用 sudo sbr" >&2; exit 1; }; }
env_value(){ sed -n "s/^$1=//p" "$ENV_FILE" | tail -1; }
nodes(){ jq -r '.nodes|keys[]?' "$KEYS" 2>/dev/null; }
inbound_type(){ jq -r --arg id "$1" '.inbounds[]|select(.tag|endswith("-"+$id))|.type' "$CONFIG"; }
require_local_reality(){ [ "$(inbound_type "$1")" != vless ] || { echo "VLESS Reality 参数由 XBoard 管理，请在面板修改后执行 sbr sync。" >&2; return 1; }; }
show_reality(){
  local id="${1:-}"
  if [ -n "$id" ]; then jq --arg id "$id" '.nodes[$id]|del(.private_key)' "$KEYS"; else jq '.nodes|with_entries(.value|=del(.private_key))' "$KEYS"; fi
}
client(){
  local id="${1:-$(nodes | head -1)}" host port inbound_type
  [ -n "$id" ] || { echo "没有节点" >&2; return 1; }
  inbound_type="$(inbound_type "$id")"
  if [ "$inbound_type" = vless ]; then echo "VLESS 节点由 XBoard 订阅直接下发；请在 FlClash/Mihomo 中更新订阅。"; show_reality "$id"; return; fi
  host="$(jq -r --arg id "$id" '.nodes[$id].server_name' "$KEYS")"; port="$(jq -r --arg tag "anytls-$id" '.inbounds[]|select(.tag==$tag)|.listen_port' "$CONFIG")"
  jq -n --arg server "VPS_IP" --argjson port "$port" --arg sni "$host" --arg pbk "$(jq -r --arg id "$id" '.nodes[$id].public_key' "$KEYS")" --arg sid "$(jq -r --arg id "$id" '.nodes[$id].short_ids[0]' "$KEYS")" '{type:"anytls",server:$server,server_port:$port,password:"从 XBoard 用户订阅取得",tls:{enabled:true,server_name:$sni,utls:{enabled:true,fingerprint:"chrome"},reality:{enabled:true,public_key:$pbk,short_id:$sid}}}'
}
scan_reality(){
  local id=""; [ "${1:-}" = "--node" ] && id="${2:-}"
  mkdir -p "$SYNC/reality-scans"; local stamp output="$SYNC/reality-scans/recommendation${id:+-$id}.json"; stamp="$(date +%s)"
  local attempts timeout family candidates
  attempts="$(env_value REALITY_SCAN_ATTEMPTS)"; timeout="$(env_value REALITY_SCAN_TIMEOUT)"; family="$(env_value REALITY_SCAN_FAMILY)"; candidates="$(env_value REALITY_CANDIDATES)"
  IFS=',' read -ra extras <<<"$candidates"
  python3 "$SYNC/reality_scanner.py" --attempts "${attempts:-3}" --timeout "${timeout:-5}" --family "${family:-prefer_ipv4}" "${extras[@]}" > "$output.tmp"
  jq --arg id "$id" --argjson at "$stamp" '.node_id=$id|.saved_at=$at' "$output.tmp" > "$output"; rm -f "$output.tmp"; chmod 600 "$output"
  jq '{vps,selected,valid_candidates:[.valid_candidates[]|{domain,success_rate,average_latency_ms,p95_latency_ms,addresses}]}' "$output"
  echo "仅生成推荐，使用 sbr reality apply NODE_ID DOMAIN 应用。"
}
apply_reality(){
  local id="${1:?缺少 NODE_ID}" domain="${2:?缺少 DOMAIN}" report="$SYNC/reality-scans/recommendation-$1.json"
  require_local_reality "$id"
  [ -f "$report" ] || report="$SYNC/reality-scans/recommendation.json"
  if [ ! -f "$report" ] || ! jq -e --arg d "$domain" '.valid_candidates|any(.domain==$d and .valid==true)' "$report" >/dev/null; then echo "该域名不在最近的有效扫描结果中，请先扫描" >&2; return 1; fi
  jq --arg id "$id" --arg d "$domain" '.nodes[$id].server_name=$d|.nodes[$id].handshake_server=$d|.nodes[$id].updated_at=(now|todate)' "$KEYS" > "$KEYS.tmp"
  chmod 600 "$KEYS.tmp"; mv "$KEYS.tmp" "$KEYS"; python3 "$SYNC/xboard_sync.py" once
}
rotate_key(){
  local id="${1:?缺少 NODE_ID}" yes="${2:-}"
  require_local_reality "$id"
  [ "$yes" = "--yes" ] || { read -rp "轮换后所有客户端必须更新 Public Key。输入 YES 继续: " answer; [ "$answer" = YES ] || return 1; }
  local pair private public; pair="$(docker exec sing-box sing-box generate reality-keypair)"; private="$(awk -F': ' '/PrivateKey/{print $2}' <<<"$pair")"; public="$(awk -F': ' '/PublicKey/{print $2}' <<<"$pair")"
  jq --arg id "$id" --arg private "$private" --arg public "$public" '.nodes[$id].private_key=$private|.nodes[$id].public_key=$public|.nodes[$id].updated_at=(now|todate)' "$KEYS" > "$KEYS.tmp"; chmod 600 "$KEYS.tmp"; mv "$KEYS.tmp" "$KEYS"
  echo "密钥已轮换（Private Key 不显示），请立即更新客户端 Public Key。"; show_reality "$id"
}
rotate_short(){
  local id="${1:?缺少 NODE_ID}" yes="${2:-}" sid
  require_local_reality "$id"
  [ "$yes" = "--yes" ] || { read -rp "轮换后所有客户端必须更新 Short ID。输入 YES 继续: " answer; [ "$answer" = YES ] || return 1; }
  sid="$(openssl rand -hex 8)"; jq --arg id "$id" --arg sid "$sid" '.nodes[$id].short_ids=[$sid]|.nodes[$id].updated_at=(now|todate)' "$KEYS" > "$KEYS.tmp"; chmod 600 "$KEYS.tmp"; mv "$KEYS.tmp" "$KEYS"; show_reality "$id"
}
status(){ "$SYNC/healthcheck.sh"; }
logs(){ case "${1:-}" in sync|report) journalctl -u "xboard-${1}" -n 120 --no-pager;; singbox) docker logs sing-box --tail 120;; *) journalctl -u xboard-sync -u xboard-report -n 80 --no-pager;; esac; }
firewall(){ while read -r p; do ufw allow "$p/tcp"; done < <(jq -r '.inbounds[]?.listen_port' "$CONFIG"); ufw status; }
version(){ echo "Project $(cat "$SYNC/VERSION" 2>/dev/null || echo unknown)"; docker exec sing-box sing-box version | head -1; docker inspect -f '{{index .RepoDigests 0}}' "$(docker inspect -f '{{.Config.Image}}' sing-box)" 2>/dev/null || true; }
usage(){ echo "sbr {status|sync|report|logs|config|check|users|traffic|online|client|reality|firewall|version|update|rollback|restart|uninstall}"; }
dispatch(){
  case "${1:-}" in
    status) status;; sync) python3 "$SYNC/xboard_sync.py" once;; report) python3 "$SYNC/xboard_report.py";; logs) logs "${2:-}";;
    config) jq 'del(.inbounds[].users[].password,.inbounds[].users[].uuid,.inbounds[].tls.reality.private_key)' "$CONFIG";; check) docker exec sing-box sing-box check -c /etc/sing-box/config.json;;
    users) jq '[.inbounds[]|{tag,users:(.users|length)}]' "$CONFIG";; traffic) jq . "$SYNC/report_pending.json" 2>/dev/null || echo '{"version":1,"nodes":{}}';;
    online) jq '.online // {}' "$SYNC/report_state.json" 2>/dev/null || echo '{}';; client) client "${2:-}";;
    reality) case "${2:-}" in show) show_reality "${3:-}";; scan) scan_reality "${3:-}" "${4:-}";; apply) apply_reality "${3:-}" "${4:-}";; rotate-key) rotate_key "${3:-}" "${4:-}";; rotate-short-id) rotate_short "${3:-}" "${4:-}";; *) echo "reality {scan [--node ID]|show [ID]|apply ID DOMAIN|rotate-key ID [--yes]|rotate-short-id ID [--yes]}";; esac;;
    firewall) firewall;; version) version;; update) bash "$SYNC/update.sh" "${@:2}";; rollback) bash "$SYNC/rollback.sh" "${@:2}";; restart) docker restart sing-box; systemctl restart xboard-sync xboard-report;; uninstall) bash "$SYNC/uninstall.sh";; *) usage; return 1;;
  esac
}
menu(){ while true; do echo "1状态 2同步 3上报 4日志 5客户端配置 6Reality 7校验 8重启 0退出"; read -rp "选择: " n; case "$n" in 1) dispatch status;;2) dispatch sync;;3) dispatch report;;4) dispatch logs;;5) read -rp "节点ID: " id; dispatch client "$id";;6) dispatch reality show;;7) dispatch check;;8) dispatch restart;;0) return;;esac; done; }
need_root
if [ $# -eq 0 ]; then menu; else dispatch "$@"; fi
