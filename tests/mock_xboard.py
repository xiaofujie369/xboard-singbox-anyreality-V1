#!/usr/bin/env python3
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

OUTPUT = Path(sys.argv[1])

class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        body = json.dumps(payload).encode(); self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if "/config?" in self.path: self.send_json(200, {"data": {"id": 1, "type": "anytls", "port": 18443}})
        elif "/user?" in self.path: self.send_json(200, {"data": {"users": [{"id": 1001, "password": "integration-password"}]}})
        else: self.send_json(200, {"ok": True})
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0")); payload = json.loads(self.rfile.read(length) or b"{}")
        with OUTPUT.open("a", encoding="utf-8") as handle: handle.write(json.dumps({"path": self.path, "payload": payload}) + "\n")
        self.send_json(200, {"ok": True})
    def log_message(self, *_args): pass

ThreadingHTTPServer(("0.0.0.0", 19090), Handler).serve_forever()
