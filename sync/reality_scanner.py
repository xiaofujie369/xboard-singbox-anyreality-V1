#!/usr/bin/env python3
"""Select a Reality handshake domain by probing from the VPS itself."""
import concurrent.futures
import argparse
import json
import socket
import ssl
import statistics
import sys
import time

import requests

GLOBAL_CANDIDATES = [
    "dl.google.com", "www.microsoft.com", "www.apple.com", "www.samsung.com",
    "www.amazon.com", "www.ibm.com", "www.oracle.com", "www.mozilla.org",
    "www.speedtest.net", "www.nvidia.com", "www.adobe.com", "www.bing.com",
]
REGIONAL_CANDIDATES = {
    "US": ["www.usps.com", "www.nasa.gov", "www.cisco.com"],
    "CA": ["www.canada.ca", "www.cbc.ca"],
    "DE": ["www.bmw.de", "www.sap.com", "www.tagesschau.de"],
    "GB": ["www.bbc.co.uk", "www.gov.uk"],
    "FR": ["www.orange.fr", "www.lemonde.fr"],
    "NL": ["www.philips.nl", "www.rijksoverheid.nl"],
    "JP": ["www.sony.jp", "www.nintendo.co.jp"],
    "KR": ["www.samsung.com", "www.naver.com"],
    "SG": ["www.gov.sg", "www.singtel.com"],
    "HK": ["www.hko.gov.hk", "www.hkt.com"],
    "TW": ["www.cht.com.tw", "www.ntu.edu.tw"],
    "AU": ["www.abc.net.au", "www.telstra.com.au"],
}

def discover_vps():
    result = {"ip": "unknown", "country_code": "unknown", "country": "unknown"}
    try:
        ip = requests.get("https://api.ipify.org", timeout=6).text.strip()
        socket.inet_pton(socket.AF_INET6 if ":" in ip else socket.AF_INET, ip)
        result["ip"] = ip
        geo = requests.get(f"https://ipwho.is/{ip}", timeout=6).json()
        if geo.get("success", True):
            result["country_code"] = str(geo.get("country_code", "unknown")).upper()
            result["country"] = geo.get("country", "unknown")
    except Exception:
        pass
    return result

def probe_once(domain, timeout=5, family="prefer_ipv4"):
    started = time.monotonic()
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.set_alpn_protocols(["h2", "http/1.1"])
    try:
        infos = socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM)
        addresses = sorted({item[4][0] for item in infos})
        if family == "ipv4_only": infos = [x for x in infos if x[0] == socket.AF_INET]
        elif family == "ipv6_only": infos = [x for x in infos if x[0] == socket.AF_INET6]
        elif family == "prefer_ipv4": infos.sort(key=lambda x: x[0] != socket.AF_INET)
        if not infos: raise RuntimeError(f"no address for family {family}")
        selected_info = infos[0]; raw = socket.socket(selected_info[0], selected_info[1], selected_info[2]); raw.settimeout(timeout)
        raw.connect(selected_info[4])
        with raw:
            with context.wrap_socket(raw, server_hostname=domain) as tls:
                version, alpn = tls.version(), tls.selected_alpn_protocol()
                cert = tls.getpeercert()
        latency = round((time.monotonic() - started) * 1000)
        valid = version == "TLSv1.3" and alpn == "h2" and bool(cert)
        return {"domain": domain, "valid": valid, "latency_ms": latency, "tls": version, "alpn": alpn, "certificate": bool(cert), "addresses": addresses[:6], "error": None}
    except Exception as exc:
        return {"domain": domain, "valid": False, "latency_ms": None, "tls": None, "alpn": None, "certificate": False, "addresses": [], "error": str(exc)[:160]}

def probe(domain, timeout=5, attempts=3, family="prefer_ipv4"):
    samples = [probe_once(domain, timeout, family) for _ in range(max(1, attempts))]
    passed = [x for x in samples if x["valid"]]
    latencies = [x["latency_ms"] for x in passed]
    success_rate = len(passed) / len(samples)
    addresses = sorted({ip for x in samples for ip in x["addresses"]})
    p95 = None
    if latencies:
        ordered = sorted(latencies); p95 = ordered[min(len(ordered) - 1, max(0, int(len(ordered) * .95) - 1))]
    return {"domain": domain, "valid": success_rate >= .8, "success_rate": success_rate, "attempts": len(samples), "average_latency_ms": round(statistics.mean(latencies)) if latencies else None, "p95_latency_ms": p95, "addresses": addresses, "samples": samples}

def choose_reality_domain(extra_candidates=None, timeout=5, attempts=3, family="prefer_ipv4", excluded_addresses=None):
    vps = discover_vps()
    candidates = list(extra_candidates or []) + REGIONAL_CANDIDATES.get(vps["country_code"], []) + GLOBAL_CANDIDATES
    candidates = list(dict.fromkeys(x.strip().lower() for x in candidates if x and "." in x))
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(candidates))) as pool:
        results = list(pool.map(lambda domain: probe(domain, timeout, attempts, family), candidates))
    excluded = set(excluded_addresses or []) | {vps["ip"]}
    for item in results:
        if excluded.intersection(item["addresses"]): item["valid"] = False; item["rejected_reason"] = "resolves to VPS/node address"
    valid = sorted((item for item in results if item["valid"]), key=lambda item: (item["average_latency_ms"], item["p95_latency_ms"]))
    if not valid:
        raise RuntimeError("没有候选域名同时通过 TLS 1.3、HTTP/2 ALPN 和证书校验")
    return valid[0]["domain"], {"vps": vps, "selected": valid[0], "valid_candidates": valid, "failed_candidates": [x for x in results if not x["valid"]], "scanned_at": int(time.time())}

if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("domains", nargs="*")
        parser.add_argument("--attempts", type=int, default=3)
        parser.add_argument("--timeout", type=int, default=5)
        parser.add_argument("--family", choices=["prefer_ipv4", "ipv4_only", "ipv6_only"], default="prefer_ipv4")
        parser.add_argument("--exclude-address", action="append", default=[])
        args = parser.parse_args()
        selected, report = choose_reality_domain(args.domains, args.timeout, args.attempts, args.family, args.exclude_address)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"SELECTED={selected}", file=sys.stderr)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr); sys.exit(1)
