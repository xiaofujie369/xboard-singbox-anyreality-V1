#!/usr/bin/env bash
set -euo pipefail
IMAGE="${1:-xboard-sing-box:test}"
docker run --rm --entrypoint sh "$IMAGE" -c 'sing-box version && grpcurl -version && test -s /usr/local/share/sing-box/stats.proto'
KEYS="$(docker run --rm "$IMAGE" generate reality-keypair)"
PRIVATE="$(printf '%s\n' "$KEYS" | awk -F': ' '/PrivateKey/{print $2}')"
PUBLIC="$(printf '%s\n' "$KEYS" | awk -F': ' '/PublicKey/{print $2}')"
mkdir -p .integration/config .integration/logs
chmod 750 .integration/logs
sed "s|PRIVATE_KEY|$PRIVATE|" tests/integration-config.json > .integration/config/config.json
docker run --rm -v "$(pwd)/.integration/config:/etc/sing-box" "$IMAGE" check -c /etc/sing-box/config.json
docker run -d --name sing-box-integration -v "$(pwd)/.integration/config:/etc/sing-box" -v "$(pwd)/.integration/logs:/var/log/sing-box" "$IMAGE" -C /etc/sing-box run
MOCK_REALITY_PRIVATE="$PRIVATE" MOCK_REALITY_PUBLIC="$PUBLIC" python tests/mock_xboard.py .integration/received.jsonl &
MOCK_PID=$!
cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "=== sing-box container logs ==="
    docker logs sing-box-integration 2>&1 || true
    cat .integration/logs/access.log 2>&1 || true
    echo "=== loaded integration config ==="
    docker exec sing-box-integration cat /etc/sing-box/config.json 2>&1 || true
  fi
  kill "$MOCK_PID" >/dev/null 2>&1 || true
  docker rm -f sing-box-integration >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT
sleep 3
test "$(docker inspect -f '{{.State.Running}}' sing-box-integration)" = true
docker exec sing-box-integration grpcurl -plaintext -import-path /usr/local/share/sing-box -proto stats.proto -d '{"pattern":"user>>>","reset":false}' 127.0.0.1:18080 v2ray.core.app.stats.command.StatsService/QueryStats
cat > .integration/test.env <<EOF
PANEL_URL=http://127.0.0.1:19090
PANEL_TOKEN=integration-token
NODES=1:anytls,2:vless
SING_BOX_CONFIG=$(pwd)/.integration/config/config.json
SING_BOX_CONTAINER=sing-box-integration
REALITY_KEY_STATE=$(pwd)/.integration/reality_keys.json
REALITY_SERVER_NAME=www.microsoft.com
CUSTOM_CONFIG_DIR=$(pwd)/.integration/custom
SING_BOX_ACCESS_LOG=$(pwd)/.integration/logs/access.log
REPORT_STATE=$(pwd)/.integration/report_state.json
REPORT_PENDING=$(pwd)/.integration/report_pending.json
ALLOW_EMPTY_USERS=false
ALLOW_EMPTY_INBOUNDS=false
TCP_ONLY=true
EOF
mkdir -p .integration/custom
XBOARD_ENV_PATH="$(pwd)/.integration/test.env" python sync/xboard_sync.py once
jq -e '.inbounds|length==2 and .[0].type=="anytls" and .[0].users[0].name=="1:1001" and .[0].tls.reality.enabled==true and .[1].type=="vless" and .[1].users[0].name=="2:1002" and .[1].users[0].flow=="xtls-rprx-vision" and .[1].tls.reality.private_key=="'"$PRIVATE"'"' .integration/config/config.json
XBOARD_ENV_PATH="$(pwd)/.integration/test.env" python sync/xboard_report.py
jq -se 'any(.[]; .path=="/api/v2/server/report" and .payload.node_id==1 and .payload.node_type=="anytls")' .integration/received.jsonl
jq -se 'any(.[]; .path=="/api/v2/server/report" and .payload.node_id==2 and .payload.node_type=="vless")' .integration/received.jsonl
