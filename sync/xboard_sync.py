#!/usr/bin/env python3
"""Synchronize XBoard AnyTLS nodes/users to a sing-box AnyReality server."""
import base64, hashlib, json, os, random, re, shutil, socket, subprocess, sys, tempfile, time
from pathlib import Path
import requests
sys.path.insert(0, str(Path(__file__).resolve().parent))
from reality_scanner import choose_reality_domain

ENV_PATH = os.environ.get("XBOARD_ENV_PATH", "/opt/sing-box-sync/.env")
DEFAULT_PADDING = ["stop=8", "0=30-30", "1=100-400", "2=400-500,c,500-1000,c,500-1000,c,500-1000,c,500-1000", "3=9-9,500-1000", "4=500-1000", "5=500-1000", "6=500-1000", "7=500-1000"]

def load_env(path=ENV_PATH):
    env = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1); env[k.strip()] = v.strip().strip('"').strip("'")
    return env

def parse_bool(v, default=False):
    return default if v in (None, "") else str(v).strip().lower() in ("1", "true", "yes", "on", "enabled")

def parse_json(v, default=None):
    if isinstance(v, (dict, list)): return v
    if isinstance(v, str) and v.strip():
        try: return json.loads(v)
        except json.JSONDecodeError: pass
    return default

def get_path(data, *paths, default=None):
    for path in paths:
        cur = data
        for part in path.split("."):
            cur = parse_json(cur, cur)
            if not isinstance(cur, dict) or part not in cur: cur = None; break
            cur = cur[part]
        if cur not in (None, "", [], {}): return cur
    return default

def normalize_node_type(v):
    v = str(v).strip().lower().replace("-", "")
    return "anytls" if v in ("anytls", "anyreality") else v

def get_nodes(env):
    raw = env.get("NODES") or f'{env.get("NODE_ID", "")}:{env.get("NODE_TYPE", "anytls")}'
    nodes = []
    seen = set()
    for item in raw.split(","):
        if ":" not in item: raise RuntimeError(f"NODES 格式错误: {item}")
        node_id, node_type = (x.strip() for x in item.split(":", 1)); node_type = normalize_node_type(node_type)
        if not node_id or node_type != "anytls": raise RuntimeError(f"仅支持 AnyTLS/AnyReality 节点: {item}")
        if not node_id.isdigit() or node_id in seen: raise RuntimeError(f"节点 ID 无效或重复: {node_id}")
        seen.add(node_id)
        nodes.append((node_id, node_type))
    return nodes

def redact(text):
    text = re.sub(r"([?&]token=)[^&\s]+", r"\1***", str(text))
    text = re.sub(r'(?i)(private[_ ]?key|password|uuid)(["\s:=]+)[^,}\s]+', r'\1\2***', text)
    return text

def request_json(url, env=None):
    env = env or {}; timeout = float(env.get("HTTP_TIMEOUT", "25")); retries = max(0, int(env.get("HTTP_RETRIES", "2")))
    last = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code in (401, 403): raise PermissionError(f"XBoard 凭据错误 HTTP {r.status_code}")
            if r.status_code == 429:
                if attempt < retries: time.sleep(min(float(r.headers.get("Retry-After", "2")), 60)); continue
                raise RuntimeError("XBoard HTTP 429 请求过多")
            if r.status_code >= 400: raise RuntimeError(f"XBoard HTTP {r.status_code}")
            try: data = r.json()
            except Exception: raise RuntimeError(f"XBoard 返回非 JSON (HTTP {r.status_code})")
            return data.get("data", data) if isinstance(data, dict) else data
        except PermissionError:
            raise
        except Exception as exc:
            last = exc
            if attempt < retries: time.sleep(min(2 ** attempt, 10))
    raise RuntimeError(f"XBoard API 请求失败: {redact(last)}")

def fetch_node(env, node_id, node_type):
    base, token = env["PANEL_URL"].rstrip("/"), env["PANEL_TOKEN"]
    q = f"node_id={node_id}&node_type={node_type}&token={token}"
    return request_json(f"{base}/api/v1/server/UniProxy/config?{q}", env), request_json(f"{base}/api/v1/server/UniProxy/user?{q}", env)

def unwrap_server(resp):
    if not isinstance(resp, dict): raise RuntimeError("节点配置不是 JSON 对象")
    for key in ("server", "config", "node"):
        if isinstance(resp.get(key), dict):
            result = dict(resp); result.update(resp[key]); return result
    return resp

def unwrap_users(resp):
    if isinstance(resp, list): return resp
    if isinstance(resp, dict):
        for key in ("users", "data"):
            value = resp.get(key)
            if isinstance(value, list): return value
            if isinstance(value, dict) and isinstance(value.get("users"), list): return value["users"]
    if isinstance(resp, dict): raise RuntimeError("用户 API JSON 中未找到 users 数组")
    raise RuntimeError("用户 API 返回类型错误")

def build_users(resp, node_id):
    result = []
    seen = set()
    for user in unwrap_users(resp):
        if not isinstance(user, dict): continue
        uid = get_path(user, "id", "user_id", "uid")
        password = get_path(user, "password", "uuid", "token")
        if uid in (None, "") or password in (None, ""): raise RuntimeError(f"节点 {node_id} 存在缺少 ID 或 password/uuid 的用户")
        name = f"{node_id}:{uid}"
        if name in seen: raise RuntimeError(f"节点 {node_id} 存在重复用户 {uid}")
        seen.add(name); result.append({"name": name, "password": str(password)})
    return result

def normalize_short_ids(value):
    value = parse_json(value, value); values = value if isinstance(value, list) else str(value or "").split(",")
    return [str(x).strip().lower() for x in values if re.fullmatch(r"(?:[0-9a-fA-F]{2}){1,8}", str(x).strip())]

def env_node(env, key, node_id, default=None): return env.get(f"{key}_{node_id}", env.get(key, default))
def node_env(env, key, node_id): return env.get(f"{key}_{node_id}")

def valid_sni(value):
    return isinstance(value, str) and len(value) <= 253 and re.fullmatch(r"(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}", value) is not None

def valid_x25519_key(value):
    if not isinstance(value, str): return False
    try: return len(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))) == 32
    except Exception: return False

def resolve_addresses(host):
    if not host: return []
    try: return sorted({item[4][0] for item in socket.getaddrinfo(str(host), None)})
    except Exception: return []

def public_key_from_private(private_key):
    raw = base64.urlsafe_b64decode(private_key + "=" * (-len(private_key) % 4))
    prefix = bytes.fromhex("302e020100300506032b656e04220420")
    with tempfile.TemporaryDirectory() as tmp:
        private_path = Path(tmp) / "private.der"; public_path = Path(tmp) / "public.der"
        private_path.write_bytes(prefix + raw)
        subprocess.run(["openssl", "pkey", "-inform", "DER", "-in", str(private_path), "-pubout", "-outform", "DER", "-out", str(public_path)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        public_raw = public_path.read_bytes()[-32:]
    return base64.urlsafe_b64encode(public_raw).decode().rstrip("=")

def generate_keypair(container):
    p = subprocess.run(["docker", "exec", container, "sing-box", "generate", "reality-keypair"], text=True, capture_output=True, check=True)
    private = public = None
    for line in p.stdout.splitlines():
        if re.search(r"Private\s*[Kk]ey:", line): private = line.split(":", 1)[1].strip()
        if re.search(r"Public\s*[Kk]ey:", line): public = line.split(":", 1)[1].strip()
    if not private or not public: raise RuntimeError(f"无法解析 Reality 密钥: {p.stdout}")
    return private, public

def load_state(path):
    target = Path(path)
    if not target.exists(): return {"version": 1, "nodes": {}}
    try: raw = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        damaged = target.with_name(f"{target.name}.corrupt-{int(time.time())}"); shutil.copy2(target, damaged)
        raise RuntimeError(f"Reality 状态损坏，已备份为 {damaged}: {exc}")
    if raw.get("version") == 1 and isinstance(raw.get("nodes"), dict): return raw
    # V1 legacy format was a direct node-id mapping.
    legacy = {str(k): v for k, v in raw.items() if isinstance(v, dict)}
    backup = target.with_name(f"{target.name}.legacy-{int(time.time())}.bak"); shutil.copy2(target, backup)
    return {"version": 1, "nodes": legacy}

def save_state(path, state):
    target = Path(path); target.parent.mkdir(parents=True, exist_ok=True); tmp = target.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2); handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
    os.chmod(tmp, 0o600); os.replace(tmp, target); os.chmod(target, 0o600)

def resolve_reality(env, server, node_id, container, state):
    protocol = parse_json(server.get("protocol_settings"), {}) or {}
    reality = get_path(protocol, "reality_settings", "tls.reality", default={}) or {}
    nodes_state = state.setdefault("nodes", {}); saved = nodes_state.get(str(node_id), {})
    private = node_env(env, "REALITY_PRIVATE_KEY", node_id) or saved.get("private_key") or get_path(reality, "private_key", "privateKey") or env.get("REALITY_PRIVATE_KEY")
    public = node_env(env, "REALITY_PUBLIC_KEY", node_id) or saved.get("public_key") or get_path(reality, "public_key", "publicKey") or env.get("REALITY_PUBLIC_KEY")
    if not private:
        private, generated_public = generate_keypair(container); public = public or generated_public
    if not valid_x25519_key(private) or (public and not valid_x25519_key(public)): raise RuntimeError(f"节点 {node_id} Reality 密钥格式无效")
    derived_public = public_key_from_private(private)
    if public and public != derived_public: raise RuntimeError(f"节点 {node_id} Reality 私钥与公钥不匹配")
    public = derived_public
    # Reality is a node-local layer. Never reuse or modify XBoard's ordinary
    # AnyTLS TLS/SNI setting unless the operator explicitly overrides it here.
    panel_name = get_path(reality, "server_name", "serverName", "handshake.server")
    server_name = node_env(env, "REALITY_SERVER_NAME", node_id) or saved.get("server_name") or panel_name or env.get("REALITY_SERVER_NAME")
    if not server_name:
        extra = [x.strip() for x in env.get("REALITY_CANDIDATES", "").split(",") if x.strip()]
        node_host = get_path(server, "host", "address", "server")
        recommendation, scan_report = choose_reality_domain(extra, int(env.get("REALITY_SCAN_TIMEOUT", "5")), int(env.get("REALITY_SCAN_ATTEMPTS", "3")), env.get("REALITY_SCAN_FAMILY", "prefer_ipv4"), resolve_addresses(node_host))
        scan_report["node_id"] = str(node_id)
        scan_dir = Path(env.get("REALITY_SCAN_DIR", "/opt/sing-box-sync/reality-scans")); scan_dir.mkdir(parents=True, exist_ok=True)
        (scan_dir / f"node-{node_id}.json").write_text(json.dumps(scan_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[sync] 节点 {node_id}: 推荐 Reality 域名 {recommendation}，报告已保存", flush=True)
        if not parse_bool(env.get("REALITY_AUTO_APPLY", "false")):
            raise RuntimeError(f"节点 {node_id} 尚未配置 Reality 域名，请执行 sbr reality apply {node_id} {recommendation}")
        server_name = recommendation
    if not valid_sni(server_name): raise RuntimeError(f"节点 {node_id} Reality SNI 不合法")
    port = int(get_path(reality, "handshake.server_port", "server_port", "serverPort", default=443))
    short_ids = normalize_short_ids(node_env(env, "REALITY_SHORT_ID", node_id) or saved.get("short_ids") or saved.get("short_id") or get_path(reality, "short_id", "shortId", "short_ids", "shortIds") or env.get("REALITY_SHORT_ID"))
    if not short_ids: short_ids = [hashlib.sha256(f"{node_id}:{private}".encode()).hexdigest()[:16]]
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    nodes_state[str(node_id)] = {"private_key": private, "public_key": public, "short_ids": short_ids, "server_name": server_name, "handshake_server": server_name, "handshake_port": port, "created_at": saved.get("created_at", now), "updated_at": now}
    return {"enabled": True, "handshake": {"server": server_name, "server_port": port}, "private_key": private, "short_id": short_ids}, server_name

def build_inbound(env, config_resp, users_resp, node_id, container, state):
    server = unwrap_server(config_resp); protocol = parse_json(server.get("protocol_settings"), {}) or {}
    returned_id = get_path(server, "id", "node_id", "nodeId")
    if returned_id not in (None, "") and str(returned_id) != str(node_id): raise RuntimeError(f"节点 ID 不匹配: 请求 {node_id} 返回 {returned_id}")
    returned_type = get_path(server, "type", "node_type", "nodeType")
    if returned_type not in (None, "") and normalize_node_type(returned_type) != "anytls": raise RuntimeError(f"节点 {node_id} 返回了非 AnyTLS 类型")
    raw_port = get_path(server, "server_port", "port", "listen_port")
    if raw_port in (None, ""): raise RuntimeError(f"节点 {node_id} 缺少端口")
    reality, sni = resolve_reality(env, server, node_id, container, state)
    padding = get_path(protocol, "padding_scheme", default=get_path(server, "padding_scheme", default=DEFAULT_PADDING)); padding = parse_json(padding, padding)
    if not isinstance(padding, list) or not all(isinstance(x, str) for x in padding): padding = DEFAULT_PADDING
    return {"type": "anytls", "tag": f"anytls-{node_id}", "listen": str(get_path(server, "listen_ip", "listen", default="::")), "listen_port": int(raw_port), "users": build_users(users_resp, node_id), "padding_scheme": padding, "tls": {"enabled": True, "server_name": sni, "reality": reality}}

def load_custom_fragments(custom_dir):
    base = Path(custom_dir); result = {}
    mapping = {"dns": ("dns.json", dict), "outbounds": ("outbounds.json", list), "route_rules": ("route_rules.json", list), "endpoints": ("endpoints.json", list)}
    for key, (filename, expected) in mapping.items():
        path = base / filename
        if not path.exists(): continue
        try: value = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc: raise RuntimeError(f"自定义片段 {path} 不是有效 JSON: {exc}")
        if isinstance(value, dict) and key in value: value = value[key]
        if not isinstance(value, expected): raise RuntimeError(f"自定义片段 {path} 类型错误")
        result[key] = value
    return result

def build_config(inbounds, custom=None, tcp_only=False):
    custom = custom or {}
    tags = [x["tag"] for x in inbounds]; users = [u["name"] for x in inbounds for u in x["users"]]
    outbounds = [{"type": "direct", "tag": "direct"}] + custom.get("outbounds", [])
    route_rules = ([{"network": "udp", "action": "reject"}] if tcp_only else []) + custom.get("route_rules", [])
    config = {"log": {"level": "info", "output": "/var/log/sing-box/access.log", "timestamp": True}, "inbounds": inbounds, "outbounds": outbounds, "route": {"rules": route_rules, "auto_detect_interface": True, "final": "direct"}, "experimental": {"v2ray_api": {"listen": "127.0.0.1:8080", "stats": {"enabled": True, "inbounds": tags, "outbounds": [x.get("tag") for x in outbounds if x.get("tag")], "users": users}}}}
    if "dns" in custom: config["dns"] = custom["dns"]
    if "endpoints" in custom: config["endpoints"] = custom["endpoints"]
    return config

def validate_config(config):
    if not isinstance(config.get("inbounds"), list): raise RuntimeError("inbounds 必须为数组")
    ports = set()
    users = set()
    for inbound in config["inbounds"]:
        port = inbound["listen_port"]
        if not 1 <= port <= 65535: raise RuntimeError(f"无效端口: {port}")
        if port in ports: raise RuntimeError(f"节点端口重复: {port}")
        ports.add(port)
        for user in inbound.get("users", []):
            if user.get("name") in users: raise RuntimeError(f"重复用户统计名: {user.get('name')}")
            users.add(user.get("name"))

def test_config(container, path, text):
    target = Path(path).with_name("config.candidate.json")
    with target.open("w", encoding="utf-8") as handle: handle.write(text); handle.flush(); os.fsync(handle.fileno())
    os.chmod(target, 0o600)
    try:
        p = subprocess.run(["docker", "exec", container, "sing-box", "check", "-c", "/etc/sing-box/config.test.json"], text=True, capture_output=True)
        if p.returncode: raise RuntimeError(p.stderr.strip() or p.stdout.strip())
    finally: target.unlink(missing_ok=True)

def ensure_container(container):
    p = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", container], text=True, capture_output=True)
    if p.returncode or p.stdout.strip() != "true": raise RuntimeError(f"容器 {container} 未运行")

def rotate_backups(config_path, keep):
    if config_path.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(config_path, config_path.with_name(f"{config_path.name}.{stamp}.bak"))
    backups = sorted(config_path.parent.glob(f"{config_path.name}.*.bak"), reverse=True)
    for old in backups[max(0, keep):]: old.unlink()

def sync_once():
    env = load_env(); nodes = get_nodes(env); container = env.get("SING_BOX_CONTAINER", "sing-box")
    config_path = Path(env.get("SING_BOX_CONFIG", "/opt/sing-box/config/config.json")); state_path = env.get("REALITY_KEY_STATE", "/opt/sing-box-sync/reality_keys.json")
    ensure_container(container); state = load_state(state_path); inbounds = []
    fetched = []
    for node_id, node_type in nodes:
        cfg, users = fetch_node(env, node_id, node_type); fetched.append((node_id, node_type, cfg, users))
    for node_id, node_type, cfg, users in fetched:
        inbound = build_inbound(env, cfg, users, node_id, container, state); inbounds.append(inbound)
        if not inbound["users"] and not parse_bool(env.get("ALLOW_EMPTY_USERS", "false")): raise RuntimeError(f"节点 {node_id} 用户列表为空，拒绝覆盖生产配置")
        print(f"[sync] 节点 {node_id}: {len(inbound['users'])} 个用户", flush=True)
    if not inbounds and not parse_bool(env.get("ALLOW_EMPTY_INBOUNDS", "false")): raise RuntimeError("入站列表为空，拒绝覆盖生产配置")
    custom = load_custom_fragments(env.get("CUSTOM_CONFIG_DIR", "/opt/sing-box-sync/custom"))
    config = build_config(inbounds, custom, parse_bool(env.get("TCP_ONLY", "true"), True)); validate_config(config); text = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    if parse_bool(env.get("SING_BOX_PRESTART_TEST", "true"), True): test_config(container, config_path, text)
    old = config_path.read_text(encoding="utf-8") if config_path.exists() else ""; save_state(state_path, state)
    if old == text: print("[sync] 配置无变化", flush=True); return False
    rotate_backups(config_path, int(env.get("SING_BOX_CONFIG_BACKUPS", "3")))
    tmp = config_path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as handle: handle.write(text); handle.flush(); os.fsync(handle.fileno())
    os.chmod(tmp, 0o600); os.replace(tmp, config_path); os.chmod(config_path, 0o600)
    try:
        subprocess.run(["docker", "restart", container], check=True)
        time.sleep(float(env.get("RESTART_STABLE_SECONDS", "3")))
        ensure_container(container)
        test_config(container, config_path, text)
    except Exception:
        backups = sorted(config_path.parent.glob(f"{config_path.name}.*.bak"), reverse=True)
        if backups: shutil.copy2(backups[0], config_path); subprocess.run(["docker", "restart", container], check=False)
        raise
    print("[sync] sing-box 配置已更新并重启", flush=True); return True

def main():
    once = len(sys.argv) > 1 and sys.argv[1] == "once"
    failures = 0
    while True:
        try: sync_once()
        except Exception as exc:
            print(f"[sync] ERROR: {redact(exc)}", file=sys.stderr, flush=True)
            if once: raise
            failures += 1
        else: failures = 0
        if once: return
        env = load_env(); base = max(10, int(env.get("SYNC_INTERVAL", "60"))); maximum = max(base, int(env.get("MAX_BACKOFF", "600")))
        delay = min(maximum, base * (2 ** max(0, failures - 1))) + random.uniform(0, max(0, int(env.get("SYNC_JITTER", "10"))))
        time.sleep(delay)

if __name__ == "__main__":
    try: main()
    except Exception: sys.exit(1)
