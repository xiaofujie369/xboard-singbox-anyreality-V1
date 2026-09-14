# XBoard sing-box AnyReality Docker Sync

使用自行编译的官方 sing-box 内核，在节点端把 XBoard 的普通 AnyTLS 节点叠加为 **AnyTLS + Reality（AnyReality）**，并向面板上报用户流量、在线状态和服务器状态。XBoard 中仍然创建和维护普通 `AnyTLS` 节点，程序不会修改面板数据库或节点信息，也不会请求不存在的 `AnyReality` 节点类型。

## 功能

- sing-box 1.14.0 稳定版，启用 Reality Server 与 V2Ray Stats API
- AnyTLS + Reality，无需 TLS 证书
- XBoard 节点配置与用户定时同步
- 多 AnyTLS 节点支持
- 每用户上下行流量上报、在线缓存、服务器状态上报
- 配置写入前使用 `sing-box check` 校验，失败自动保留旧配置
- Reality 密钥首次自动生成并持久化
- 根据 VPS 出口 IP 所在国家以及 VPS 到目标站点的实测握手延迟自动选择 Reality 域名
- systemd 守护、健康检查、ufw 放行及 `sbr` 管理菜单
- amd64/arm64 等 Docker BuildKit 支持的架构

## XBoard 节点要求

在 XBoard 建立 `AnyTLS` 节点。节点端口直接来自面板；每个用户的 `password`（兼容回退到 `uuid`）作为 AnyTLS 密码。面板若提供 Reality 配置，程序会读取：

- `protocol_settings.reality_settings.private_key`
- `protocol_settings.reality_settings.public_key`
- `protocol_settings.reality_settings.server_name`
- `protocol_settings.reality_settings.short_id`

面板下发的普通 AnyTLS TLS/SNI 不会被拿来修改 Reality 层。Reality 参数完全保存在节点 VPS 本地；若私钥为空，首次同步会自动生成并保存至 `/opt/sing-box-sync/reality_keys.json`。

首次同步会从 VPS 查询出口 IP 和国家，优先测试对应地区候选站点，再测试全球候选站点。只有同时满足以下条件的域名才会入选：

- DNS 可正常解析且证书可验证
- TLS 1.3 握手成功
- ALPN 协商为 HTTP/2 `h2`
- 从当前 VPS 实测握手延迟较低

扫描报告保存在 `/opt/sing-box-sync/reality-scans/node-节点ID.json`。结果会持久化，不会每分钟重新扫描。

```dotenv
REALITY_SERVER_NAME=
# 全局配置，或用 REALITY_PRIVATE_KEY_100 / REALITY_SHORT_ID_100 覆盖节点 100
REALITY_PRIVATE_KEY=
REALITY_PUBLIC_KEY=
REALITY_SHORT_ID=
# 自定义域名会优先参与测试；留空使用内置地区候选列表
REALITY_CANDIDATES=
REALITY_SCAN_TIMEOUT=5
```

自动生成后，可用以下命令查看客户端所需的公钥、SNI 和 short ID：

```bash
jq . /opt/sing-box-sync/reality_keys.json
cat /opt/sing-box-sync/reality-scans/node-100.json | jq .
```

> 公钥可以公开给客户端，`private_key` 和 XBoard TOKEN 必须保密，切勿提交到仓库。

## 安装

一键安装：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/xiaofujie369/xboard-singbox-anyreality-V1/main/install.sh)
```

也可以克隆后安装：

```bash
git clone https://github.com/xiaofujie369/xboard-singbox-anyreality-V1.git
cd xboard-singbox-anyreality-V1
sudo bash install.sh
```

按提示输入：

```text
XBoard 面板地址: https://panel.example.com
XBoard 通讯密钥 TOKEN: ...
AnyTLS 节点列表: 100:anytls,101:anytls
```

安装时会在 VPS 上编译带 `with_v2ray_api` 的 sing-box，首次耗时通常数分钟。目录如下：

```text
/opt/sing-box/config/config.json
/opt/sing-box/logs/access.log
/opt/sing-box/docker-compose.yml
/opt/sing-box-sync/.env
/opt/sing-box-sync/reality_keys.json
```

## 客户端参数

使用支持 AnyTLS + Reality 的 sing-box 系客户端：

```json
{
  "type": "anytls",
  "server": "VPS_IP",
  "server_port": 443,
  "password": "XBoard 用户密码",
  "tls": {
    "enabled": true,
    "server_name": "www.microsoft.com",
    "utls": {"enabled": true, "fingerprint": "chrome"},
    "reality": {
      "enabled": true,
      "public_key": "reality_keys.json 中的 public_key",
      "short_id": "reality_keys.json 中的 short_id"
    }
  }
}
```

服务端使用 AnyTLS 官方默认 padding scheme（与参考文章相同）。

## 运维

```bash
sbr
/opt/sing-box-sync/healthcheck.sh
python3 /opt/sing-box-sync/reality_scanner.py
systemctl status xboard-sync xboard-report --no-pager
journalctl -u xboard-sync -u xboard-report -n 100 --no-pager
```

更新本地代码后在项目目录运行 `sudo bash update.sh`。卸载运行 `sudo bash uninstall.sh`，默认保留 `/opt` 下配置和密钥。

## 防火墙

`sbr` 菜单可以为所有生成的入站端口添加 ufw TCP 规则。云厂商安全组仍需手动放行相同端口。AnyTLS 入站使用 TCP。

## 安全提示

- Reality 伪装域名必须从服务器可正常访问，且建议与 VPS 网络条件匹配。
- 不要公开 `.env`、`reality_keys.json` 或生成后的 `config.json`。
- 本项目仅用于合法的远程访问、隐私保护及技术研究，请遵守所在地法律和服务商条款。

## 参考

- [sing-box AnyTLS 入站文档](https://sing-box.sagernet.org/configuration/inbound/anytls/)
- [sing-box Reality TLS 文档](https://sing-box.sagernet.org/configuration/shared/tls/#reality-fields)
- [XBoard](https://github.com/cedar2025/Xboard)
- [AnyReality 参考文章](https://macin.top/posts/852c0c5c/)

License: MIT
