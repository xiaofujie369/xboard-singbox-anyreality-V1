#!/usr/bin/env bash
set -euo pipefail
REPO=xiaofujie369/xboard-singbox-anyreality-V1; RAW="https://raw.githubusercontent.com/$REPO/main"; APP=/opt/sing-box; SYNC=/opt/sing-box-sync
[ "$(id -u)" = 0 ] || { echo "请使用 root"; exit 1; }
remote="$(curl -fsSL "$RAW/VERSION")"; current="$(cat "$SYNC/VERSION" 2>/dev/null || echo 0)"
if [ "${1:-}" = --check ]; then echo "current=$current latest=$remote"; [ "$current" = "$remote" ] && exit 0 || exit 2; fi
if [ "${1:-}" = --version ] && [ "${2:-}" != "$remote" ]; then echo "当前仓库只发布版本 $remote"; exit 1; fi
[ "$current" != "$remote" ] || { echo "已经是 $current"; exit 0; }
stamp="$(date +%Y%m%d-%H%M%S)"; backup="$SYNC/backups/$stamp"; mkdir -p "$backup/program" "$backup/systemd" "$backup/state"
cp -a "$APP/config" "$APP/docker-compose.yml" "$APP/docker-compose.build.yml" "$backup/" 2>/dev/null || true
cp -a "$SYNC"/*.py "$SYNC"/*.sh "$SYNC/VERSION" "$backup/program/" 2>/dev/null || true
cp -a "$SYNC/.env" "$SYNC/reality_keys.json" "$SYNC/report_pending.json" "$SYNC/report_state.json" "$SYNC/custom" "$backup/state/" 2>/dev/null || true
cp -a /etc/systemd/system/xboard-*.service "$backup/systemd/" 2>/dev/null || true
docker inspect -f '{{.Config.Image}} {{index .RepoDigests 0}}' sing-box > "$backup/image.txt" 2>/dev/null || true
systemctl stop xboard-sync xboard-report
restore(){ echo "更新失败，正在恢复程序和配置"; cp -a "$backup/program/." "$SYNC/"; cp -a "$backup/config/." "$APP/config/" 2>/dev/null || true; cp -a "$backup"/docker-compose*.yml "$APP/" 2>/dev/null || true; cp -a "$backup/systemd/." /etc/systemd/system/; systemctl daemon-reload; cd "$APP" && docker compose up -d 2>/dev/null || true; systemctl restart xboard-sync xboard-report 2>/dev/null || true; }
trap restore ERR
for f in docker-compose.yml docker-compose.build.yml Dockerfile stats.proto anytls-source-ip.patch; do curl -fsSL "$RAW/sing-box/$f" -o "$APP/$f"; done
for f in xboard_sync.py xboard_report.py reality_scanner.py healthcheck.sh manage.sh; do curl -fsSL "$RAW/sync/$f" -o "$SYNC/$f"; done
for f in update.sh rollback.sh uninstall.sh VERSION; do curl -fsSL "$RAW/$f" -o "$SYNC/$f"; done
for f in xboard-sync.service xboard-report.service; do curl -fsSL "$RAW/systemd/$f" -o "/etc/systemd/system/$f"; done
chmod 700 "$SYNC"/*.py "$SYNC"/*.sh; install -m 755 "$SYNC/manage.sh" /usr/local/bin/sbr
chmod 700 "$SYNC"; chmod 600 "$SYNC/.env" "$SYNC/reality_keys.json" "$SYNC/report_pending.json" "$SYNC/report_state.json" 2>/dev/null || true; chmod 750 "$APP/logs"; chmod 640 "$APP/logs/access.log" 2>/dev/null || true
cat > /etc/logrotate.d/xboard-singbox-anyreality <<'EOF'
/opt/sing-box/logs/*.log { daily size 50M rotate 7 compress missingok notifempty copytruncate create 0640 root root }
EOF
cd "$APP"; docker compose pull; docker compose up -d
systemctl daemon-reload; python3 "$SYNC/xboard_sync.py" once; systemctl restart xboard-sync xboard-report; "$SYNC/healthcheck.sh"
trap - ERR; echo "更新完成 $current -> $remote；备份 $backup"
