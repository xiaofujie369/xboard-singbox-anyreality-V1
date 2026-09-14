import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
    return module

sync = load("xboard_sync", ROOT / "sync" / "xboard_sync.py")
report = load("xboard_report", ROOT / "sync" / "xboard_report.py")

class SyncTests(unittest.TestCase):
    def test_nodes_accept_anyreality_alias(self):
        self.assertEqual(sync.get_nodes({"NODES": "7:anyreality,8:anytls"}), [("7", "anytls"), ("8", "anytls")])

    def test_users_are_scoped_for_statistics(self):
        users = sync.build_users({"users": [{"id": 9, "password": "secret"}, {"id": 10, "uuid": "fallback"}]}, "7")
        self.assertEqual(users, [{"name": "7:9", "password": "secret"}, {"name": "7:10", "password": "fallback"}])

    @mock.patch.object(sync, "choose_reality_domain", return_value=("edge.example.com", {"vps": {"ip": "203.0.113.1", "country_code": "US"}, "selected": {"latency_ms": 20}}))
    @mock.patch.object(sync, "generate_keypair", return_value=("private", "public"))
    def test_build_anyreality_inbound_and_persist_key(self, _generate, _choose):
        state = {}
        with tempfile.TemporaryDirectory() as tmp:
            inbound = sync.build_inbound({"REALITY_SCAN_DIR": tmp}, {"port": 443, "protocol_settings": {"tls": {"server_name": "yahoo.com"}}}, [{"id": 9, "password": "secret"}], "7", "sing-box", state)
        self.assertEqual(inbound["type"], "anytls")
        self.assertTrue(inbound["tls"]["reality"]["enabled"])
        self.assertEqual(inbound["tls"]["server_name"], "edge.example.com")
        self.assertEqual(len(inbound["padding_scheme"]), 9)
        self.assertEqual(state["7"]["public_key"], "public")

    @mock.patch.object(sync, "choose_reality_domain")
    def test_saved_reality_domain_ignores_panel_anytls_sni(self, choose):
        state = {"7": {"private_key": "private", "public_key": "public", "server_name": "saved.example.com", "short_id": "aabb"}}
        reality, domain = sync.resolve_reality({}, {"protocol_settings": {"tls": {"server_name": "panel.example.com"}}}, "7", "sing-box", state)
        self.assertEqual(domain, "saved.example.com")
        choose.assert_not_called()

    def test_build_config_enables_per_user_stats(self):
        inbound = {"type": "anytls", "tag": "anytls-7", "listen": "::", "listen_port": 443, "users": [{"name": "7:9", "password": "x"}], "padding_scheme": [], "tls": {"enabled": True, "reality": {"private_key": "x", "short_id": ["aa"]}}}
        config = sync.build_config([inbound])
        stats = config["experimental"]["v2ray_api"]["stats"]
        self.assertEqual(stats["users"], ["7:9"])
        self.assertEqual(stats["inbounds"], ["anytls-7"])

    def test_duplicate_ports_rejected(self):
        inbound = {"tag": "a", "listen_port": 443, "users": [], "tls": {"reality": {"private_key": "x", "short_id": ["aa"]}}}
        with self.assertRaisesRegex(RuntimeError, "重复"):
            sync.validate_config([*[]] if False else {"inbounds": [inbound, dict(inbound, tag="b")]})

class ReportTests(unittest.TestCase):
    def test_scoped_stats_aggregate(self):
        payload = {"stat": [{"name": "user>>>7:9>>>traffic>>>uplink", "value": "12"}, {"name": "user>>>7:9>>>traffic>>>downlink", "value": "34"}]}
        scoped, legacy = report.parse_traffic_by_node(payload)
        self.assertEqual(scoped, {"7": {9: [12, 34]}}); self.assertEqual(legacy, {})

    def test_report_normalizes_alias(self):
        self.assertEqual(report.normalize_node_type("AnyReality"), "anytls")

    def test_sing_box_log_lines_correlate_user_and_ip(self):
        lines = [
            "+0800 INFO [12345678 0ms] inbound/anytls[anytls-7]: inbound connection from 203.0.113.9:4567\n",
            "+0800 INFO [12345678 2ms] inbound/anytls[anytls-7]: [7:9] inbound connection to example.com:443\n",
        ]
        scoped, legacy = report.parse_alive_from_access_lines(lines)
        self.assertEqual(scoped, {"7": {9: ["203.0.113.9"]}})
        self.assertEqual(legacy, {})

if __name__ == "__main__": unittest.main()
