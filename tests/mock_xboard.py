#!/usr/bin/env python3
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

OUTPUT = Path(sys.argv[1])

class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        body = json.dumps(payload).encode(); self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        parsed = urlparse(self.path); node_id = parse_qs(parsed.query).get("node_id", ["1"])[0]
        if "/config?" in self.path and node_id == "2":
            self.send_json(200, {"data": {"id": 2, "protocol": "vless", "server_port": 18444, "listen_ip": "::", "network": "tcp", "tls": 2, "flow": "xtls-rprx-vision", "tls_settings": {"server_name": "www.microsoft.com", "private_key": os.environ["MOCK_REALITY_PRIVATE"], "public_key": os.environ["MOCK_REALITY_PUBLIC"], "short_id": "a1b2c3d4e5f60708"}}})
        elif "/config?" in self.path: self.send_json(200, {"data": {"id": 1, "type": "anytls", "port": 18443}})
        elif "/user?" in self.path and node_id == "2": self.send_json(200, {"data": {"users": [{"id": 1002, "uuid": "01994f67-6e13-7bbc-9e47-2c0bad413328"}]}})
        elif "/user?" in self.path: self.send_json(200, {"data": {"users": [{"id": 1001, "password": "integration-password"}]}})
        else: self.send_json(200, {"ok": True})
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0")); payload = json.loads(self.rfile.read(length) or b"{}")
        with OUTPUT.open("a", encoding="utf-8") as handle: handle.write(json.dumps({"path": self.path, "payload": payload}) + "\n")
        self.send_json(200, {"ok": True})
    def log_message(self, *_args): pass

ThreadingHTTPServer(("0.0.0.0", 19090), Handler).serve_forever()
