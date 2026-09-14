import importlib.util
import base64
import json
import os
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
scanner = load("reality_scanner_test", ROOT / "sync" / "reality_scanner.py")

class SyncTests(unittest.TestCase):
    def test_nodes_accept_anyreality_alias(self):
        self.assertEqual(sync.get_nodes({"NODES": "7:anyreality,8:anytls"}), [("7", "anytls"), ("8", "anytls")])

    def test_users_are_scoped_for_statistics(self):
        users = sync.build_users({"users": [{"id": 9, "password": "secret"}, {"id": 10, "uuid": "fallback"}]}, "7")
        self.assertEqual(users, [{"name": "7:9", "password": "secret"}, {"name": "7:10", "password": "fallback"}])

    @mock.patch.object(sync, "choose_reality_domain", return_value=("edge.example.com", {"vps": {"ip": "203.0.113.1", "country_code": "US"}, "selected": {"latency_ms": 20}}))
    @mock.patch.object(sync, "public_key_from_private", return_value="public")
    @mock.patch.object(sync, "valid_x25519_key", return_value=True)
    @mock.patch.object(sync, "generate_keypair", return_value=("private", "public"))
    def test_build_anyreality_inbound_and_persist_key(self, _generate, _valid, _derive, _choose):
        state = {}
        with tempfile.TemporaryDirectory() as tmp:
            inbound = sync.build_inbound({"REALITY_SCAN_DIR": tmp, "REALITY_AUTO_APPLY": "true"}, {"port": 443, "protocol_settings": {"tls": {"server_name": "yahoo.com"}}}, [{"id": 9, "password": "secret"}], "7", "sing-box", state)
        self.assertEqual(inbound["type"], "anytls")
        self.assertTrue(inbound["tls"]["reality"]["enabled"])
        self.assertEqual(inbound["tls"]["server_name"], "edge.example.com")
        self.assertEqual(len(inbound["padding_scheme"]), 9)
        self.assertEqual(state["nodes"]["7"]["public_key"], "public")

    @mock.patch.object(sync, "choose_reality_domain")
    @mock.patch.object(sync, "public_key_from_private", return_value="public")
    @mock.patch.object(sync, "valid_x25519_key", return_value=True)
    def test_saved_reality_domain_ignores_panel_anytls_sni(self, _valid, _derive, choose):
        state = {"version": 1, "nodes": {"7": {"private_key": "private", "public_key": "public", "server_name": "saved.example.com", "short_ids": ["aabb"]}}}
        reality, domain = sync.resolve_reality({}, {"protocol_settings": {"tls": {"server_name": "panel.example.com"}}}, "7", "sing-box", state)
        self.assertEqual(domain, "saved.example.com")
        choose.assert_not_called()

    def test_build_config_enables_per_user_stats(self):
        inbound = {"type": "anytls", "tag": "anytls-7", "listen": "::", "listen_port": 443, "users": [{"name": "7:9", "password": "x"}], "padding_scheme": [], "tls": {"enabled": True, "reality": {"private_key": "x", "short_id": ["aa"]}}}
        config = sync.build_config([inbound])
        stats = config["experimental"]["v2ray_api"]["stats"]
        self.assertEqual(stats["users"], ["7:9"])
        self.assertEqual(stats["inbounds"], ["anytls-7"])

    def test_custom_fragments_and_tcp_only(self):
        inbound = {"type": "anytls", "tag": "anytls-7", "listen": "::", "listen_port": 443, "users": [{"name": "7:9", "password": "x"}], "tls": {"reality": {}}}
        config = sync.build_config([inbound], {"dns": {"servers": []}, "outbounds": [{"type": "block", "tag": "block"}], "route_rules": [{"domain_suffix": ["example.com"], "action": "reject"}], "endpoints": []}, True)
        self.assertIn("dns", config); self.assertIn("endpoints", config)
        self.assertEqual(config["route"]["rules"][0], {"network": "udp", "action": "reject"})
        self.assertEqual(config["outbounds"][1]["tag"], "block")

    def test_duplicate_users_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "重复用户"):
            sync.build_users([{"id": 1, "password": "a"}, {"id": 1, "password": "b"}], "7")

    def test_public_key_derivation_produces_x25519_key(self):
        private = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
        public = sync.public_key_from_private(private)
        self.assertTrue(sync.valid_x25519_key(public))

    def test_legacy_key_state_migrates_and_backs_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "keys.json"; path.write_text('{"7":{"server_name":"example.com"}}', encoding="utf-8")
            migrated = sync.load_state(path)
            self.assertEqual(migrated["version"], 1); self.assertIn("7", migrated["nodes"])
            self.assertEqual(len(list(Path(tmp).glob("keys.json.legacy-*.bak"))), 1)

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
            "+0800 INFO [12345678 0ms] inbound/anytls[anytls-7]: inbound connection from 8.8.8.8:4567\n",
            "+0800 INFO [12345678 2ms] inbound/anytls[anytls-7]: [7:9] inbound connection to example.com:443\n",
        ]
        scoped, legacy = report.parse_alive_from_access_lines(lines)
        self.assertEqual(scoped, {"7": {9: ["8.8.8.8"]}})
        self.assertEqual(legacy, {})

    def test_single_line_ipv6_and_target_not_misreported(self):
        line = "INFO [12345678 1ms] inbound/anytls[x]: [7:9] inbound connection from [2606:4700:4700::1111]:1234 to 1.1.1.1:443\n"
        scoped, _ = report.parse_alive_from_access_lines([line])
        self.assertEqual(scoped, {"7": {9: ["2606:4700:4700::1111"]}})

    def test_private_and_mapped_ip_policy(self):
        self.assertIsNone(report.normalize_client_ip("127.0.0.1:9"))
        self.assertIsNone(report.normalize_client_ip("10.0.0.1:9"))
        self.assertEqual(report.normalize_client_ip("[::ffff:8.8.8.8]:9"), "8.8.8.8")
        self.assertEqual(report.normalize_client_ip("10.0.0.1:9", allow_private=True), "10.0.0.1")

    def test_pending_accumulates_and_clears_one_node(self):
        pending = {"version": 1, "nodes": {"7": {"9": [10, 20]}, "8": {"9": [1, 2]}}}
        report.merge_pending(pending, {"7": {9: [3, 4], 10: [5, 6]}})
        self.assertEqual(report.pending_node_traffic(pending, "7"), {9: [13, 24], 10: [5, 6]})
        report.clear_pending_node(pending, "7")
        self.assertEqual(pending["nodes"], {"8": {"9": [1, 2]}})

    def test_corrupt_pending_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pending.json"; path.write_text("bad", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "损坏"):
                report.load_pending(path)
            self.assertEqual(len(list(Path(tmp).glob("pending.json.corrupt-*"))), 1)

    @mock.patch.object(report, "post_status_legacy")
    @mock.patch.object(report, "post_alive")
    @mock.patch.object(report, "post_traffic")
    @mock.patch.object(report, "post_report_v2", side_effect=report.ReportHTTPError(401, "/api/v2/server/report"))
    def test_401_does_not_fallback(self, _v2, traffic, alive, status):
        with self.assertRaises(report.ReportHTTPError):
            report.post_node_report({"REPORT_USE_V2_REPORT": "true", "REPORT_V2_FALLBACK": "true"}, "7", "anytls", {}, {}, {})
        traffic.assert_not_called(); alive.assert_not_called(); status.assert_not_called()

    @mock.patch.object(report, "post_status_legacy")
    @mock.patch.object(report, "post_alive")
    @mock.patch.object(report, "post_traffic")
    @mock.patch.object(report, "post_report_v2", side_effect=report.ReportHTTPError(404, "/api/v2/server/report"))
    def test_404_falls_back(self, _v2, traffic, alive, status):
        report.post_node_report({"REPORT_USE_V2_REPORT": "true", "REPORT_V2_FALLBACK": "true"}, "7", "anytls", {}, {}, {})
        traffic.assert_called_once(); alive.assert_called_once(); status.assert_called_once()

    def test_report_failure_keeps_pending_and_success_clears(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = str(Path(tmp) / "pending.json"); state = str(Path(tmp) / "state.json")
            env = {"NODES": "7:anytls", "REPORT_PENDING": pending, "REPORT_STATE": state, "REPORT_ONLINE_TTL": "180"}
            stats = {"stat": [{"name": "user>>>7:9>>>traffic>>>uplink", "value": "12"}]}
            with mock.patch.object(report, "load_env", return_value=env), mock.patch.object(report, "run_statsquery", return_value=stats), mock.patch.object(report, "read_access_log_since", return_value=({}, {})), mock.patch.object(report, "collect_status", return_value={}), mock.patch.object(report, "post_node_report", side_effect=RuntimeError("offline")):
                with self.assertRaisesRegex(RuntimeError, "offline"): report.report_once()
            self.assertEqual(report.load_pending(pending)["nodes"]["7"]["9"], [12, 0])
            with mock.patch.object(report, "load_env", return_value=env), mock.patch.object(report, "run_statsquery", return_value={}), mock.patch.object(report, "read_access_log_since", return_value=({}, {})), mock.patch.object(report, "collect_status", return_value={}), mock.patch.object(report, "post_node_report"):
                report.report_once()
            self.assertEqual(report.load_pending(pending)["nodes"], {})

    def test_log_inode_change_restarts_from_beginning(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "access.log"; state = Path(tmp) / "state.json"
            log.write_text("INFO [12345678 1ms] inbound/anytls[x]: [7:9] inbound connection from 8.8.4.4:22 to example.com:443\n", encoding="utf-8")
            report.atomic_save_json(state, {"access_log": {"path": str(log), "inode": 1, "offset": 999, "size": 999}})
            scoped, _ = report.read_access_log_since({"SING_BOX_ACCESS_LOG": str(log), "REPORT_STATE": str(state)})
            self.assertEqual(scoped, {"7": {9: ["8.8.4.4"]}})
            saved = json.loads(state.read_text(encoding="utf-8"))["access_log"]
            self.assertIn("inode", saved); self.assertIn("updated_at", saved)

class ScannerTests(unittest.TestCase):
    @mock.patch.object(scanner, "probe_once")
    def test_attempt_threshold_and_latency(self, probe_once):
        probe_once.side_effect = [
            {"valid": True, "latency_ms": 10, "addresses": ["1.1.1.1"]},
            {"valid": True, "latency_ms": 20, "addresses": ["1.1.1.1"]},
            {"valid": False, "latency_ms": None, "addresses": []},
            {"valid": True, "latency_ms": 30, "addresses": ["1.1.1.1"]},
            {"valid": True, "latency_ms": 40, "addresses": ["1.1.1.1"]},
        ]
        result = scanner.probe("example.com", attempts=5)
        self.assertTrue(result["valid"]); self.assertEqual(result["success_rate"], .8); self.assertEqual(result["average_latency_ms"], 25)

if __name__ == "__main__": unittest.main()
