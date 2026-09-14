#!/usr/bin/env python3
import json
import os
import ipaddress
import random
import shutil
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

ENV_PATH = os.environ.get("XBOARD_ENV_PATH", "/opt/sing-box-sync/.env")
STATE_PATH = "/opt/sing-box-sync/report_state.json"
DEFAULT_ACCESS_LOG = "/opt/sing-box/logs/access.log"
PENDING_PATH = "/opt/sing-box-sync/report_pending.json"

def redact(text):
    text = re.sub(r"([?&]token=)[^&\s]+", r"\1***", str(text))
    return re.sub(r'(?i)(private[_ ]?key|password|uuid)(["\s:=]+)[^,}\s]+', r'\1\2***', text)

class ReportHTTPError(RuntimeError):
    def __init__(self, status, endpoint, body=""):
        self.status = status
        super().__init__(f"{endpoint} HTTP {status}")


def load_env():
    env = {}
    p = Path(ENV_PATH)
    if not p.exists():
        raise RuntimeError(f"配置文件不存在: {ENV_PATH}")

    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def normalize_node_type(t):
    t = str(t).strip().lower()
    aliases = {
        "ss": "shadowsocks",
        "shadow": "shadowsocks",
        "shadowsocks2022": "shadowsocks",
        "v2ray": "vmess",
        "anyreality": "anytls",
    }
    return aliases.get(t, t)


def parse_bool(v, default=False):
    if v is None or v == "":
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)

    s = str(v).strip().lower()
    if s in ["1", "true", "yes", "y", "on", "enable", "enabled"]:
        return True
    if s in ["0", "false", "no", "n", "off", "disable", "disabled"]:
        return False
    return default


def parse_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default


def get_nodes(env):
    nodes = []
    seen = set()

    if env.get("NODES"):
        for item in env["NODES"].split(","):
            item = item.strip()
            if not item:
                continue
            if ":" not in item:
                raise RuntimeError(f"NODES format error: {item}; expected node_id:protocol")
            node_id, node_type = item.split(":", 1)
            node_id = node_id.strip()
            node_type = normalize_node_type(node_type.strip())
            if not node_id.isdigit() or node_type != "anytls" or node_id in seen:
                raise RuntimeError(f"NODES format error or duplicate: {item}")
            seen.add(node_id)
            nodes.append((node_id, node_type))
    else:
        nodes.append((env["NODE_ID"], normalize_node_type(env.get("NODE_TYPE", "vless"))))

    if not nodes:
        raise RuntimeError("NODES cannot be empty; expected node_id:protocol")

    return nodes


def run_statsquery(container="sing-box"):
    cmd = [
        "docker",
        "exec",
        container,
        "grpcurl",
        "-plaintext",
        "-import-path",
        "/usr/local/share/sing-box",
        "-proto",
        "stats.proto",
        "-d",
        '{"pattern":"user>>>","reset":true}',
        "127.0.0.1:8080",
        "v2ray.core.app.stats.command.StatsService/QueryStats"
    ]

    p = subprocess.run(cmd, text=True, capture_output=True)

    if p.returncode != 0:
        raise RuntimeError(f"sing-box stats query failed: {p.stderr.strip() or p.stdout.strip()}")

    out = p.stdout.strip()
    if not out:
        return {}

    try:
        return json.loads(out)
    except Exception:
        raise RuntimeError(f"sing-box stats query 返回不是 JSON: {out[:500]}")


def parse_traffic(stats_json):
    """
    Xray 返回：
    {
      "stat": [
        {"name": "user>>>1485>>>traffic>>>uplink", "value": "123"},
        {"name": "user>>>1485>>>traffic>>>downlink", "value": "456"}
      ]
    }

    XBoard 需要：
    {
      "1485": [123, 456]
    }
    """
    stat_list = stats_json.get("stat") or stats_json.get("stats") or []

    traffic = {}

    for item in stat_list:
        name = item.get("name", "")
        value = int(item.get("value", 0) or 0)

        m = re.match(r"^user>>>(.+?)>>>traffic>>>(uplink|downlink)$", name)
        if not m:
            continue

        user_id_raw = m.group(1)
        direction = m.group(2)

        # 用户名由同步脚本设置为 XBoard 用户 id（多节点时为 node_id:user_id）。
        try:
            uid = int(user_id_raw)
        except Exception:
            continue

        if uid not in traffic:
            traffic[uid] = [0, 0]

        if direction == "uplink":
            traffic[uid][0] += value
        elif direction == "downlink":
            traffic[uid][1] += value

    # 删除 0 流量
    traffic = {
        uid: arr for uid, arr in traffic.items()
        if arr[0] > 0 or arr[1] > 0
    }

    return traffic


def split_user_key(user_key):
    if ":" in user_key:
        node_id, user_id_raw = user_key.split(":", 1)
    else:
        node_id = None
        user_id_raw = user_key

    try:
        uid = int(user_id_raw)
    except Exception:
        return None, None

    return node_id, uid


def add_traffic(traffic, uid, direction, value):
    if uid not in traffic:
        traffic[uid] = [0, 0]

    if direction == "uplink":
        traffic[uid][0] += value
    elif direction == "downlink":
        traffic[uid][1] += value


def strip_zero_traffic(traffic):
    return {
        uid: arr for uid, arr in traffic.items()
        if arr[0] > 0 or arr[1] > 0
    }


def parse_traffic_by_node(stats_json):
    stat_list = stats_json.get("stat") or stats_json.get("stats") or []

    scoped = {}
    legacy = {}

    for item in stat_list:
        name = item.get("name", "")
        value = int(item.get("value", 0) or 0)

        m = re.match(r"^user>>>(.+?)>>>traffic>>>(uplink|downlink)$", name)
        if not m:
            continue

        user_key = m.group(1)
        direction = m.group(2)

        node_id, uid = split_user_key(user_key)
        if uid is None:
            continue

        if node_id:
            target = scoped.setdefault(node_id, {})
        else:
            target = legacy

        add_traffic(target, uid, direction, value)

    scoped = {
        node_id: strip_zero_traffic(traffic)
        for node_id, traffic in scoped.items()
    }
    scoped = {node_id: traffic for node_id, traffic in scoped.items() if traffic}

    return scoped, strip_zero_traffic(legacy)


def extract_user_key_from_access_line(line):
    patterns = [
        r"email:\s*([^\s\]]+)",
        # sing-box user name; a lone number is usually the connection ID.
        r"\[([0-9]+:[0-9]+)\]\s+inbound",
    ]
    for pattern in patterns:
        m = re.search(pattern, line)
        if m:
            return m.group(1)
    return None


def normalize_client_ip(value, allow_private=False):
    value = str(value).strip()
    if value.startswith("[") and "]" in value: value = value[1:value.index("]")]
    elif value.count(":") == 1 and "." in value: value = value.rsplit(":", 1)[0]
    if "%" in value: value = value.split("%", 1)[0]
    try: address = ipaddress.ip_address(value)
    except ValueError: return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped: address = address.ipv4_mapped
    if address.is_unspecified or address.is_loopback: return None
    if not allow_private and address.is_private: return None
    return str(address)

def extract_ip_from_access_line(line, allow_private=False):
    match = re.search(r"\bconnection from\s+(\[[0-9a-fA-F:.%]+\](?::\d+)?|[0-9a-fA-F:.%]+(?::\d+)?)", line)
    return normalize_client_ip(match.group(1), allow_private) if match else None


def parse_alive_from_access_lines(lines, allow_private=False):
    scoped = {}
    legacy = {}
    connections = {}

    for line in lines:
        user_key = extract_user_key_from_access_line(line)
        ip = extract_ip_from_access_line(line, allow_private) if " connection from " in line else None
        connection_match = re.search(r"\[([0-9]{2,})\s+[^\]]*\]", line)
        connection_id = connection_match.group(1) if connection_match else None
        if connection_id:
            item = connections.setdefault(connection_id, {})
            if user_key:
                item["user"] = user_key
            if ip:
                item["ip"] = ip
            user_key, ip = item.get("user"), item.get("ip")
        if not user_key or not ip:
            continue

        node_id, uid = split_user_key(user_key)
        if uid is None:
            continue

        if node_id:
            target = scoped.setdefault(node_id, {})
        else:
            target = legacy

        target.setdefault(uid, set()).add(ip)

    def freeze(data):
        return {
            uid: sorted(ips)
            for uid, ips in data.items()
            if ips
        }

    scoped = {
        node_id: freeze(alive)
        for node_id, alive in scoped.items()
    }
    scoped = {node_id: alive for node_id, alive in scoped.items() if alive}

    return scoped, freeze(legacy)


def load_state(path):
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def save_state(path, state):
    atomic_save_json(path, state)

def atomic_save_json(path, data):
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
    os.chmod(tmp, 0o600); os.replace(tmp, p); os.chmod(p, 0o600)

def load_pending(path=PENDING_PATH):
    p = Path(path)
    if not p.exists(): return {"version": 1, "nodes": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not isinstance(data.get("nodes"), dict): raise ValueError("invalid pending schema")
        return data
    except Exception as exc:
        damaged = p.with_name(f"{p.name}.corrupt-{int(time.time())}")
        shutil.copy2(p, damaged)
        raise RuntimeError(f"pending 文件损坏，已保留为 {damaged}: {exc}")

def merge_pending(pending, scoped_traffic):
    for node_id, users in scoped_traffic.items():
        target = pending["nodes"].setdefault(str(node_id), {})
        for uid, values in users.items():
            up, down = max(0, int(values[0])), max(0, int(values[1]))
            current = target.setdefault(str(uid), [0, 0])
            current[0] += up; current[1] += down

def pending_node_traffic(pending, node_id):
    return {int(uid): [int(v[0]), int(v[1])] for uid, v in pending.get("nodes", {}).get(str(node_id), {}).items()}

def clear_pending_node(pending, node_id):
    pending.get("nodes", {}).pop(str(node_id), None)


def read_access_log_since(env):
    access_log = env.get("SING_BOX_ACCESS_LOG", DEFAULT_ACCESS_LOG)
    state_path = env.get("REPORT_STATE", STATE_PATH)
    log_path = Path(access_log)

    if not log_path.exists():
        return {}, {}

    state = load_state(state_path)
    log_state = state.get("access_log", {})

    stat = log_path.stat(); size = stat.st_size; inode = getattr(stat, "st_ino", 0)
    offset = int(log_state.get("offset", 0) or 0)
    if offset < 0 or offset > size or int(log_state.get("inode", inode) or 0) != inode or log_state.get("path") != str(log_path):
        offset = 0

    with log_path.open("r", errors="ignore") as f:
        f.seek(offset)
        lines = f.readlines()
        new_offset = f.tell()

    state["access_log"] = {
        "path": str(log_path),
        "offset": new_offset,
        "size": size,
        "inode": inode,
        "updated_at": int(time.time()),
    }
    save_state(state_path, state)

    return parse_alive_from_access_lines(lines, parse_bool(env.get("REPORT_PRIVATE_IP", "false")))


def report_state_path(env):
    return env.get("REPORT_STATE", STATE_PATH)


def configured_node_ids(nodes):
    return {node_id for node_id, _node_type in nodes}


def refresh_online_cache(env, scoped_alive, nodes, now=None):
    ttl = max(0, parse_int(env.get("REPORT_ONLINE_TTL", "180"), default=180))
    max_ips = max(1, parse_int(env.get("REPORT_MAX_IPS_PER_USER", "10"), default=10))
    now = int(time.time() if now is None else now)
    state_path = report_state_path(env)
    state = load_state(state_path)
    online_state = state.setdefault("online", {})
    node_state = online_state.setdefault("nodes", {})
    active = {}
    configured = configured_node_ids(nodes)

    for node_id in list(node_state.keys()):
        if node_id not in configured:
            del node_state[node_id]

    for node_id, alive in scoped_alive.items():
        users = node_state.setdefault(node_id, {})
        for uid, ips in alive.items():
            users[str(uid)] = {
                "last_seen": now,
                "ips": sorted(set(ips))[:max_ips],
            }

    for node_id in configured:
        users = node_state.get(node_id, {})
        kept = {}
        for uid_raw, item in users.items():
            try:
                last_seen = int(item.get("last_seen", 0) or 0)
                uid = int(uid_raw)
            except Exception:
                continue

            if now - last_seen > ttl:
                continue

            ips = item.get("ips") or []
            if ips:
                limited_ips = sorted(set(ips))[:max_ips]
                kept[str(uid)] = {"last_seen": last_seen, "ips": limited_ips}
                active.setdefault(node_id, {})[uid] = limited_ips

        if kept:
            node_state[node_id] = kept
        else:
            node_state.pop(node_id, None)

    online_state["ttl"] = ttl
    save_state(state_path, state)
    return active


def merge_legacy_map(scoped, legacy, nodes, label):
    if not legacy:
        return

    if len(nodes) != 1:
        print(f"[report] skipped legacy unscoped {label} because multiple NODES are configured")
        return

    node_id = nodes[0][0]
    target = scoped.setdefault(node_id, {})
    for uid, values in legacy.items():
        if uid not in target:
            target[uid] = values
        elif isinstance(values, list) and values and isinstance(values[0], int):
            target[uid][0] += values[0]
            target[uid][1] += values[1]
        else:
            target[uid] = sorted(set(target[uid]) | set(values))


def include_alive_users_in_traffic(scoped_traffic, scoped_alive):
    for node_id, alive in scoped_alive.items():
        traffic = scoped_traffic.setdefault(node_id, {})
        for uid in alive:
            traffic.setdefault(uid, [0, 0])


def to_panel_int(value):
    try:
        return int(value)
    except Exception:
        return value


def payload_with_string_keys(data):
    return {str(k): v for k, v in data.items()}


def alive_to_online(alive):
    return {
        uid: len(set(ips))
        for uid, ips in alive.items()
        if ips
    }


def read_cpu_snapshot(path="/proc/stat"):
    try:
        line = Path(path).read_text().splitlines()[0]
    except Exception:
        return None

    parts = line.split()
    if not parts or parts[0] != "cpu":
        return None

    values = []
    for item in parts[1:]:
        try:
            values.append(int(item))
        except Exception:
            values.append(0)

    if len(values) < 4:
        return None

    idle = values[3] + (values[4] if len(values) > 4 else 0)
    total = sum(values)
    return total, idle


def collect_cpu_percent():
    first = read_cpu_snapshot()
    if not first:
        return 0.0

    time.sleep(0.1)

    second = read_cpu_snapshot()
    if not second:
        return 0.0

    total_delta = second[0] - first[0]
    idle_delta = second[1] - first[1]
    if total_delta <= 0:
        return 0.0

    return round(max(0.0, min(100.0, (1 - idle_delta / total_delta) * 100)), 2)


def collect_memory_status(path="/proc/meminfo"):
    try:
        lines = Path(path).read_text().splitlines()
    except Exception:
        return (0, 0), (0, 0)

    values = {}
    for line in lines:
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        parts = raw.strip().split()
        if not parts:
            continue
        try:
            values[key] = int(parts[0]) * 1024
        except Exception:
            continue

    mem_total = values.get("MemTotal", 0)
    mem_available = values.get("MemAvailable", values.get("MemFree", 0))
    swap_total = values.get("SwapTotal", 0)
    swap_free = values.get("SwapFree", 0)

    mem_used = max(0, mem_total - mem_available)
    swap_used = max(0, swap_total - swap_free)
    return (mem_total, mem_used), (swap_total, swap_used)


def collect_disk_status(path="/"):
    try:
        usage = shutil.disk_usage(path)
        return usage.total, usage.used
    except Exception:
        return 0, 0


def collect_status():
    mem, swap = collect_memory_status()
    disk = collect_disk_status()
    return {
        "cpu": collect_cpu_percent(),
        "mem": {"total": mem[0], "used": mem[1]},
        "swap": {"total": swap[0], "used": swap[1]},
        "disk": {"total": disk[0], "used": disk[1]},
    }


def build_v2_report_payload(env, node_id, node_type, traffic, alive, status):
    payload = {
        "token": env["PANEL_TOKEN"],
        "node_id": to_panel_int(node_id),
        "node_type": node_type,
        "status": status,
    }

    if traffic:
        payload["traffic"] = payload_with_string_keys(traffic)

    if alive:
        payload["alive"] = payload_with_string_keys(alive)
        online = alive_to_online(alive)
        if online:
            payload["online"] = payload_with_string_keys(online)

    if parse_bool(env.get("REPORT_KERNEL_STATUS", "true"), default=True):
        payload["metrics"] = {"kernel_status": True}

    return payload


def post_report_v2(env, node_id, node_type, traffic, alive, status):
    panel = env["PANEL_URL"].rstrip("/")
    url = f"{panel}/api/v2/server/report"
    payload = build_v2_report_payload(env, node_id, node_type, traffic, alive, status)

    r = requests.post(url, json=payload, timeout=float(env.get("HTTP_TIMEOUT", "25")))

    try:
        resp = r.json()
    except Exception:
        resp = r.text[:500]

    if r.status_code >= 400:
        raise ReportHTTPError(r.status_code, "/api/v2/server/report", resp)

    print(
        f"[report] posted v2 report for node {node_id}:{node_type}: "
        f"traffic={len(traffic)}, alive={len(alive)}, online={len(alive_to_online(alive))}, status=1"
    )


def post_traffic(env, node_id, node_type, traffic):
    if not traffic:
        print(f"[report] node {node_id}:{node_type} has no traffic to push")
        return

    panel = env["PANEL_URL"].rstrip("/")
    token = env["PANEL_TOKEN"]

    url = f"{panel}/api/v1/server/UniProxy/push?node_id={node_id}&node_type={node_type}&token={token}"

    r = requests.post(url, json=traffic, timeout=float(env.get("HTTP_TIMEOUT", "25")))

    try:
        resp = r.json()
    except Exception:
        resp = r.text[:500]

    if r.status_code >= 400:
        raise ReportHTTPError(r.status_code, "/api/v1/server/UniProxy/push", resp)

    print(f"[report] pushed traffic for {len(traffic)} users on node {node_id}:{node_type}")


def post_alive(env, node_id, node_type, alive):
    if not alive:
        print(f"[report] node {node_id}:{node_type} has no alive users to push")
        return

    panel = env["PANEL_URL"].rstrip("/")
    token = env["PANEL_TOKEN"]

    url = f"{panel}/api/v1/server/UniProxy/alive?node_id={node_id}&node_type={node_type}&token={token}"

    r = requests.post(url, json=alive, timeout=float(env.get("HTTP_TIMEOUT", "25")))

    try:
        resp = r.json()
    except Exception:
        resp = r.text[:500]

    if r.status_code >= 400:
        raise ReportHTTPError(r.status_code, "/api/v1/server/UniProxy/alive", resp)

    print(f"[report] pushed alive devices for {len(alive)} users on node {node_id}:{node_type}")


def post_status_legacy(env, node_id, node_type, status):
    panel = env["PANEL_URL"].rstrip("/")
    token = env["PANEL_TOKEN"]

    url = f"{panel}/api/v1/server/UniProxy/status?node_id={node_id}&node_type={node_type}&token={token}"

    r = requests.post(url, json=status, timeout=float(env.get("HTTP_TIMEOUT", "25")))

    try:
        resp = r.json()
    except Exception:
        resp = r.text[:500]

    if r.status_code >= 400:
        raise ReportHTTPError(r.status_code, "/api/v1/server/UniProxy/status", resp)

    print(f"[report] pushed legacy status for node {node_id}:{node_type}")


def post_node_report(env, node_id, node_type, traffic, alive, status):
    if parse_bool(env.get("REPORT_USE_V2_REPORT", "true"), default=True):
        try:
            post_report_v2(env, node_id, node_type, traffic, alive, status)
            return
        except ReportHTTPError as e:
            if e.status not in (404, 405) or not parse_bool(env.get("REPORT_V2_FALLBACK", "true"), default=True):
                raise
            print(f"[report] v2 endpoint unavailable for node {node_id}:{node_type}; using legacy fallback")

    post_traffic(env, node_id, node_type, traffic)
    post_alive(env, node_id, node_type, alive)
    post_status_legacy(env, node_id, node_type, status)


def report_once():
    env = load_env()
    nodes = get_nodes(env)
    stats = run_statsquery(env.get("SING_BOX_CONTAINER", "sing-box"))
    scoped_traffic, legacy_traffic = parse_traffic_by_node(stats)
    scoped_alive, legacy_alive = read_access_log_since(env)

    merge_legacy_map(scoped_traffic, legacy_traffic, nodes, "traffic")
    merge_legacy_map(scoped_alive, legacy_alive, nodes, "alive users")
    active_alive = refresh_online_cache(env, scoped_alive, nodes)
    include_alive_users_in_traffic(scoped_traffic, active_alive)

    pending_path = env.get("REPORT_PENDING", PENDING_PATH)
    pending = load_pending(pending_path)
    merge_pending(pending, scoped_traffic)
    atomic_save_json(pending_path, pending)
    status = collect_status()

    for node_id, node_type in nodes:
        traffic = pending_node_traffic(pending, node_id)
        post_node_report(env, node_id, node_type, traffic, active_alive.get(node_id, {}), status)
        clear_pending_node(pending, node_id)
        atomic_save_json(pending_path, pending)


def main():
    loop = len(sys.argv) > 1 and sys.argv[1] == "loop"
    failures = 0
    while True:
        try:
            report_once(); failures = 0
        except Exception as exc:
            print(f"[report] ERROR: {redact(exc)}", file=sys.stderr, flush=True); failures += 1
            if not loop: raise
        if not loop: return
        env = load_env(); base = max(10, int(env.get("REPORT_INTERVAL", "60"))); maximum = max(base, int(env.get("MAX_BACKOFF", "600")))
        delay = min(maximum, base * (2 ** max(0, failures - 1))) + random.uniform(0, max(0, int(env.get("REPORT_JITTER", "10"))))
        time.sleep(delay)

if __name__ == "__main__":
    try: main()
    except Exception: sys.exit(1)
