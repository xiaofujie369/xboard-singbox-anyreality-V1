# XBoard sing-box AnyReality

生产型 XBoard 独立节点端。它始终从 XBoard 的普通 `AnyTLS` UniProxy 节点读取端口和用户密码，只在 VPS 本地为 sing-box 入站叠加 Reality。不会修改 XBoard 数据库、前端或节点类型，也不依赖 Xboard-Node、V2bX、XrayR。

当前项目版本 `1.1.0`，内核固定为 sing-box `1.14.0`。支持 Debian 12/13、Ubuntu 22.04/24.04，以及 amd64/arm64。

## 功能边界

- 多 AnyTLS 节点与用户统一同步；密码优先读取 `password`，缺失时回退 `uuid`。
- 每个节点独立 Reality 密钥、Short ID、SNI 和握手目标，仅以权限 600 保存在 VPS。
- V2Ray Stats API 每用户上下行统计；流量先原子写入 pending，面板明确确认后才清除。
- AnyTLS 日志包含认证用户和来源 IP；支持 IPv4/IPv6、跨行兼容解析、TTL、数量限制及 logrotate。
- v2 report 优先；只有 404/405 才回退旧 UniProxy API，401/403、429、422、5xx不会盲目回退。
- 所有节点全部获取成功后才统一生成配置；默认拒绝空用户和空入站覆盖生产配置。
- candidate 校验、原子替换、无变化不重启、稳定性检查及失败回滚。
- 固定版本 GHCR 多架构镜像；也可明确选择 VPS 本地构建。
- 自定义 DNS、outbounds、route rules、endpoints；更新不会覆盖。
- systemd 加固、日志轮转、UFW、版本化备份、更新、回滚和分级卸载。

## XBoard 配置

在 XBoard 中建立普通 `AnyTLS` 节点。安装时填写：

```text
379:anytls
379:anytls,380:anytls
```

程序请求 XBoard 时始终使用 `node_type=anytls`。普通 AnyTLS 订阅不一定携带 Reality Public Key、Short ID 和 SNI，客户端可能需要手工补充 `sbr client NODE_ID` 显示的参数。Private Key 永远不能上传面板或填写到客户端。

## 安装

默认拉取固定镜像 `ghcr.io/xiaofujie369/xboard-singbox-anyreality-v1:1.1.0`：

```bash
sudo -i
bash <(curl -fsSL https://raw.githubusercontent.com/xiaofujie369/xboard-singbox-anyreality-V1/main/install.sh)
```

安装器会检查系统、架构、systemd、磁盘、Docker、Compose、DNS、GitHub/GHCR、XBoard API、节点格式和端口冲突。已有安装不会被覆盖。

非交互安装必须明确给出 Reality 域名：

```bash
sudo env \
  PANEL_URL=https://panel.example.com \
  PANEL_TOKEN=REPLACE_ME \
  NODES=379:anytls \
  REALITY_SERVER_NAME=www.microsoft.com \
  bash -c 'bash <(curl -fsSL https://raw.githubusercontent.com/xiaofujie369/xboard-singbox-anyreality-V1/main/install.sh)'
```

若 GHCR 无法访问并且 VPS 内存、磁盘足够，可以显式本地编译：

```bash
INSTALL_MODE=build bash <(curl -fsSL https://raw.githubusercontent.com/xiaofujie369/xboard-singbox-anyreality-V1/main/install.sh)
```

镜像拉取失败不会静默转为本地编译。

## Reality 域名与密钥

交互安装会从 VPS 查询出口 IP/国家，对地区和全球候选域名各测试至少三次，只推荐满足证书验证、TLS 1.3、ALPN `h2`、成功率不低于 80%，且不解析到 VPS 地址的候选；按平均/P95延迟排序。扫描与应用分离，后台不会未经确认切换生产 SNI。

```bash
sbr reality scan --node 379
sbr reality show 379
sbr reality apply 379 www.microsoft.com
sbr reality rotate-key 379
sbr reality rotate-short-id 379
```

非交互轮换必须增加 `--yes`。轮换 Public Key 或 Short ID 后，所有客户端必须同步更新。

状态格式位于 `/opt/sing-box-sync/reality_keys.json`。旧 V1 直接节点映射格式会自动备份并迁移为版本化格式。

## 客户端

```bash
sbr client 379
```

命令只显示 Public Key、Short ID、SNI 和端口，不显示 Private Key 或 XBoard 用户密码。把输出中的 `VPS_IP` 和“从 XBoard 用户订阅取得”替换为实际值。客户端需要支持 AnyTLS + Reality 的 sing-box 内核。

## 自定义配置

自定义文件位于 `/opt/sing-box-sync/custom/`，缺失时保持 direct 默认：

```text
dns.json
outbounds.json
route_rules.json
endpoints.json
```

`dns.json` 是标准 sing-box DNS 对象；其余文件为对应对象数组，也可使用带同名顶级键的对象。可以实现 relay、SendIP、prefer_ipv4、域名/BT/SMTP拦截等标准 sing-box 能力。所有合并结果仍须通过完整 `sing-box check`。

默认 `TCP_ONLY=true`，会拒绝 UDP，防火墙只放行 TCP。需要 UDP 时编辑 `/opt/sing-box-sync/.env` 后改为 `false` 并同步。

## 管理命令

```bash
sbr status
sbr sync
sbr report
sbr logs sync|report|singbox
sbr config
sbr check
sbr users
sbr traffic
sbr online
sbr client 379
sbr reality scan --node 379
sbr reality show 379
sbr reality apply 379 DOMAIN
sbr firewall
sbr version
sbr restart
```

`sbr config` 会隐藏用户密码和 Reality Private Key。`sbr status` 最终输出 `HEALTHY` 或 `UNHEALTHY`，异常时返回非零。

## 更新与回滚

从早期 V1 升级时首次执行：

```bash
curl -fsSL https://raw.githubusercontent.com/xiaofujie369/xboard-singbox-anyreality-V1/main/update.sh -o /tmp/xboard-anyreality-update.sh
sudo bash /tmp/xboard-anyreality-update.sh
```

升级后使用：

```bash
sbr update --check
sbr update --version 1.1.0
sbr rollback --list
sbr rollback BACKUP_TIMESTAMP
```

更新备份包含程序、配置、Compose、systemd、镜像信息，以及单独保存的 `.env`、Reality、pending、online state 和 custom。普通回滚不回滚密钥与流量状态；明确增加 `--include-state` 才会恢复状态。旧版 Reality 密钥会保留并迁移。

## 卸载

```bash
sbr uninstall
```

可选择只移除程序、删除运行配置但单独保留 Reality 密钥，或者输入 `DELETE` 完全删除。

## 文件与权限

```text
/opt/sing-box/config/config.json          600
/opt/sing-box/logs                        750
/opt/sing-box/logs/access.log             640
/opt/sing-box-sync                        700
/opt/sing-box-sync/.env                   600
/opt/sing-box-sync/reality_keys.json      600
/opt/sing-box-sync/report_pending.json    600
/opt/sing-box-sync/report_state.json      600
```

访问日志每日或达到 50MB 时轮转，压缩保留七份。

## 故障排查

```bash
sbr status
sbr check
sbr logs sync
sbr logs report
sbr logs singbox
journalctl -u xboard-sync -u xboard-report -n 150 --no-pager
```

云安全组仍需放行生成的 TCP 节点端口。`sbr firewall` 只处理 VPS 的 UFW，无法修改云厂商安全组。

不要公开 `.env`、`reality_keys.json`、生成后的服务端配置或日志。本项目只用于合法远程访问、隐私保护和技术研究。

## CI 与镜像

GitHub Actions 会执行 Python/单元/ShellCheck、真实 sing-box 配置、容器启动和 Stats API 查询，然后才发布：

```text
ghcr.io/xiaofujie369/xboard-singbox-anyreality-v1:1.1.0
ghcr.io/xiaofujie369/xboard-singbox-anyreality-v1:sing-box-1.14.0
ghcr.io/xiaofujie369/xboard-singbox-anyreality-v1:latest
```

参考：[sing-box AnyTLS](https://sing-box.sagernet.org/configuration/inbound/anytls/)、[sing-box V2Ray API](https://sing-box.sagernet.org/configuration/experimental/v2ray-api/)、[Reality 目标要求](https://github.com/XTLS/Xray-core/discussions/4849)。

License: MIT
