#!/usr/bin/env bash
set -uo pipefail
CONFIG=/opt/sing-box/config/config.json; SYNC=/opt/sing-box-sync; failures=0
check(){ if "$@" >/dev/null 2>&1; then echo "[OK] $*"; else echo "[FAIL] $*"; failures=$((failures+1)); fi; }
check docker inspect -f '{{.State.Running}}' sing-box
check docker exec sing-box sing-box check -c /etc/sing-box/config.json
check systemctl is-active xboard-sync
check systemctl is-active xboard-report
check test -s "$CONFIG"
echo "Project: $(cat "$SYNC/VERSION" 2>/dev/null || echo unknown)"
docker exec sing-box sing-box version 2>/dev/null | head -1 || true
echo "Image: $(docker inspect -f '{{.Config.Image}}' sing-box 2>/dev/null || echo unknown)"
echo "Nodes/users: $(jq '[.inbounds|length,([.inbounds[].users[]?]|length)]|@tsv' -r "$CONFIG" 2>/dev/null || echo unknown)"
jq '[.inbounds[]|{tag,port:.listen_port,users:(.users|length),reality:(.tls.reality.enabled==true),server_name:.tls.server_name}]' "$CONFIG" 2>/dev/null || true
echo "Pending bytes: $(jq '[.nodes[][]?|add]|add // 0' "$SYNC/report_pending.json" 2>/dev/null || echo 0)"
echo "Access log: $(du -h /opt/sing-box/logs/access.log 2>/dev/null | cut -f1 || echo missing)"
echo "Disk: $(df -h /opt | awk 'NR==2{print $4" free ("$5" used)"}')"
if docker exec sing-box grpcurl -plaintext -proto /usr/local/share/sing-box/stats.proto -d '{"pattern":"user>>>","reset":false}' 127.0.0.1:8080 experimental.v2rayapi.StatsService/QueryStats >/dev/null 2>&1; then echo "[OK] Stats API"; else echo "[FAIL] Stats API"; failures=$((failures+1)); fi
if [ "$failures" -eq 0 ]; then echo "HEALTHY"; exit 0; else echo "UNHEALTHY ($failures checks failed)"; exit 1; fi
