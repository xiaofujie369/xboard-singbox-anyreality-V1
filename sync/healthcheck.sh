#!/usr/bin/env bash
set +e
CONFIG=/opt/sing-box/config/config.json
echo "===== Container ====="
docker ps -a --filter name=sing-box
echo "===== Version / build tags ====="
docker exec sing-box sing-box version
echo "===== Configuration ====="
docker exec sing-box sing-box check -c /etc/sing-box/config.json
jq '.inbounds[]? | {tag,type,listen,listen_port,users:(.users|length),server_name:.tls.server_name,reality:(.tls.reality.enabled==true),short_id:.tls.reality.short_id}' "$CONFIG"
echo "===== Listening ports ====="
PORTS="$(jq -r '.inbounds[]?.listen_port' "$CONFIG" 2>/dev/null | paste -sd'|' -)"
[ -z "$PORTS" ] || ss -lntp | grep -E "(:($PORTS))"
echo "===== Per-user statistics ====="
docker exec sing-box grpcurl -plaintext -proto /usr/local/share/sing-box/stats.proto -d '{"pattern":"user>>>","reset":false}' 127.0.0.1:8080 experimental.v2rayapi.StatsService/QueryStats
echo "===== Services ====="
systemctl status xboard-sync xboard-report --no-pager
echo "===== Recent logs ====="
journalctl -u xboard-sync -u xboard-report -n 40 --no-pager
tail -n 20 /opt/sing-box/logs/access.log
