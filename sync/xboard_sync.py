#!/usr/bin/env python3
"""Synchronize XBoard AnyTLS nodes/users to a sing-box AnyReality server."""
import hashlib, json, os, re, shutil, subprocess, sys, time
from pathlib import Path
import requests
sys.path.insert(0, str(Path(__file__).resolve().parent))
from reality_scanner import choose_reality_domain

ENV_PATH = "/opt/sing-box-sync/.env"
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
    for item in raw.split(","):
        if ":" not in item: raise RuntimeError(f"NODES 格式错误: {item}")
        node_id, node_type = (x.strip() for x in item.split(":", 1)); node_type = normalize_node_type(node_type)
        if not node_id or node_type != "anytls": raise RuntimeError(f"仅支持 AnyTLS/AnyReality 节点: {item}")
        nodes.append((node_id, node_type))
    return nodes

def redact(text): return re.sub(r"([?&]token=)[^&\s]+", r"\1***", str(text))

def request_json(url):
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=25); data = r.json()
            if r.status_code >= 400: raise RuntimeError(f"HTTP {r.status_code}: {data}")
            return data.get("data", data) if isinstance(data, dict) else data
        except Exception as exc:
            last = exc
            if attempt < 2: time.sleep(2)
    raise RuntimeError(f"XBoard API 请求失败: {redact(last)}")

def fetch_node(env, node_id, node_type):
    base, token = env["PANEL_URL"].rstrip("/"), env["PANEL_TOKEN"]
    q = f"node_id={node_id}&node_type={node_type}&token={token}"
    return request_json(f"{base}/api/v1/server/UniProxy/config?{q}"), request_json(f"{base}/api/v1/server/UniProxy/user?{q}")

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
    return []

def build_users(resp, node_id):
    result = []
    for user in unwrap_users(resp):
        if not isinstance(user, dict): continue
        uid = get_path(user, "id", "user_id", "uid")
        password = get_path(user, "password", "uuid", "token")
        if uid not in (None, "") and password not in (None, ""):
            result.append({"name": f"{node_id}:{uid}", "password": str(password)})
    return result

def normalize_short_ids(value):
    value = parse_json(value, value); values = value if isinstance(value, list) else str(value or "").split(",")
    return [str(x).strip().lower() for x in values if re.fullmatch(r"(?:[0-9a-fA-F]{2}){1,8}", str(x).strip())]

def env_node(env, key, node_id, default=None): return env.get(f"{key}_{node_id}", env.get(key, default))

def generate_keypair(container):
    p = subprocess.run(["docker", "exec", container, "sing-box", "generate", "reality-keypair"], text=True, capture_output=True, check=True)
    private = public = None
    for line in p.stdout.splitlines():
        if re.search(r"Private\s*[Kk]ey:", line): private = line.split(":", 1)[1].strip()
        if re.search(r"Public\s*[Kk]ey:", line): public = line.split(":", 1)[1].strip()
    if not private or not public: raise RuntimeError(f"无法解析 Reality 密钥: {p.stdout}")
    return private, public

def load_state(path):
    try: return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception: return {}

def save_state(path, state):
    target = Path(path); target.parent.mkdir(parents=True, exist_ok=True); tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); os.chmod(tmp, 0o600); tmp.replace(target)

def resolve_reality(env, server, node_id, container, state):
    protocol = parse_json(server.get("protocol_settings"), {}) or {}
    reality = get_path(protocol, "reality_settings", "tls.reality", default={}) or {}
    saved = state.get(str(node_id), {})
    private = env_node(env, "REALITY_PRIVATE_KEY", node_id, get_path(reality, "private_key", "privateKey")) or saved.get("private_key")
    public = env_node(env, "REALITY_PUBLIC_KEY", node_id, get_path(reality, "public_key", "publicKey")) or saved.get("public_key")
    if not private:
        private, generated_public = generate_keypair(container); public = public or generated_public
    # Reality is a node-local layer. Never reuse or modify XBoard's ordinary
    # AnyTLS TLS/SNI setting unless the operator explicitly overrides it here.
    server_name = env_node(env, "REALITY_SERVER_NAME", node_id) or saved.get("server_name")
    if not server_name:
        extra = [x.strip() for x in env.get("REALITY_CANDIDATES", "").split(",") if x.strip()]
        server_name, scan_report = choose_reality_domain(extra, int(env.get("REALITY_SCAN_TIMEOUT", "5")))
        scan_report["node_id"] = str(node_id)
        scan_dir = Path(env.get("REALITY_SCAN_DIR", "/opt/sing-box-sync/reality-scans")); scan_dir.mkdir(parents=True, exist_ok=True)
        (scan_dir / f"node-{node_id}.json").write_text(json.dumps(scan_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[sync] 节点 {node_id}: 根据 VPS {scan_report['vps']['ip']} ({scan_report['vps']['country_code']}) 选择 Reality 域名 {server_name}，延迟 {scan_report['selected']['latency_ms']}ms", flush=True)
    port = int(get_path(reality, "handshake.server_port", "server_port", "serverPort", default=443))
    short_ids = normalize_short_ids(env_node(env, "REALITY_SHORT_ID", node_id, get_path(reality, "short_id", "shortId", "short_ids", "shortIds")))
    if not short_ids: short_ids = [hashlib.sha256(f"{node_id}:{private}".encode()).hexdigest()[:16]]
    state[str(node_id)] = {"private_key": private, "public_key": public, "server_name": server_name, "short_id": short_ids[0]}
    return {"enabled": True, "handshake": {"server": server_name, "server_port": port}, "private_key": private, "short_id": short_ids}, server_name

def build_inbound(env, config_resp, users_resp, node_id, container, state):
    server = unwrap_server(config_resp); protocol = parse_json(server.get("protocol_settings"), {}) or {}
    reality, sni = resolve_reality(env, server, node_id, container, state)
    padding = get_path(protocol, "padding_scheme", default=get_path(server, "padding_scheme", default=DEFAULT_PADDING)); padding = parse_json(padding, padding)
    if not isinstance(padding, list) or not all(isinstance(x, str) for x in padding): padding = DEFAULT_PADDING
    return {"type": "anytls", "tag": f"anytls-{node_id}", "listen": str(get_path(server, "listen_ip", "listen", default="::")), "listen_port": int(get_path(server, "server_port", "port", "listen_port", default=443)), "users": build_users(users_resp, node_id), "padding_scheme": padding, "tls": {"enabled": True, "server_name": sni, "reality": reality}}

def build_config(inbounds):
    tags = [x["tag"] for x in inbounds]; users = [u["name"] for x in inbounds for u in x["users"]]
    return {"log": {"level": "info", "output": "/var/log/sing-box/access.log", "timestamp": True}, "inbounds": inbounds, "outbounds": [{"type": "direct", "tag": "direct"}], "route": {"auto_detect_interface": True, "final": "direct"}, "experimental": {"v2ray_api": {"listen": "127.0.0.1:8080", "stats": {"enabled": True, "inbounds": tags, "outbounds": ["direct"], "users": users}}}}

def validate_config(config):
    ports = set()
    for inbound in config["inbounds"]:
        port = inbound["listen_port"]
        if not 1 <= port <= 65535: raise RuntimeError(f"无效端口: {port}")
        if port in ports: raise RuntimeError(f"节点端口重复: {port}")
        ports.add(port)
        if not inbound["users"]: print(f"[sync] WARNING: {inbound['tag']} 没有有效用户", flush=True)

def test_config(container, path, text):
    target = Path(path).with_name("config.test.json"); target.write_text(text, encoding="utf-8")
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
    for node_id, node_type in nodes:
        cfg, users = fetch_node(env, node_id, node_type); inbound = build_inbound(env, cfg, users, node_id, container, state); inbounds.append(inbound)
        print(f"[sync] 节点 {node_id}: {len(inbound['users'])} 个用户", flush=True)
    config = build_config(inbounds); validate_config(config); text = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    if parse_bool(env.get("SING_BOX_PRESTART_TEST", "true"), True): test_config(container, config_path, text)
    old = config_path.read_text(encoding="utf-8") if config_path.exists() else ""; save_state(state_path, state)
    if old == text: print("[sync] 配置无变化", flush=True); return False
    rotate_backups(config_path, int(env.get("SING_BOX_CONFIG_BACKUPS", "3")))
    tmp = config_path.with_suffix(".tmp"); tmp.write_text(text, encoding="utf-8"); tmp.replace(config_path)
    try: subprocess.run(["docker", "restart", container], check=True)
    except Exception:
        backups = sorted(config_path.parent.glob(f"{config_path.name}.*.bak"), reverse=True)
        if backups: shutil.copy2(backups[0], config_path); subprocess.run(["docker", "restart", container], check=False)
        raise
    print("[sync] sing-box 配置已更新并重启", flush=True); return True

def main():
    once = len(sys.argv) > 1 and sys.argv[1] == "once"
    while True:
        try: sync_once()
        except Exception as exc:
            print(f"[sync] ERROR: {redact(exc)}", file=sys.stderr, flush=True)
            if once: raise
        if once: return
        time.sleep(max(10, int(load_env().get("SYNC_INTERVAL", "60"))))

if __name__ == "__main__":
    try: main()
    except Exception: sys.exit(1)
