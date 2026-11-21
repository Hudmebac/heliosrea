#!/usr/bin/env python3
"""Offline checks for REA. No internet, no credentials, no fixtures outside temp."""

from __future__ import annotations

import json
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import autonomy  # noqa: E402
import decision  # noqa: E402
import discovery  # noqa: E402
import engine  # noqa: E402
import executor  # noqa: E402
import improvements  # noqa: E402
import mailroom  # noqa: E402
import media  # noqa: E402
import revenue  # noqa: E402
import studio  # noqa: E402


def synthetic_pe() -> bytes:
    """Minimal PE32+ with a synthetic cert table, .upx0 and an overlay."""
    raw = bytearray(0x1000)
    raw[0:2] = b"MZ"
    struct.pack_into("<I", raw, 0x3C, 0x80)
    raw[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<HHIIIHH", raw, 0x84,
                     0x8664, 1, 0, 0, 0, 240, 0x22)
    opt = 0x98
    struct.pack_into("<H", raw, opt, 0x20B)
    struct.pack_into("<H", raw, opt + 68, 10)  # EFI subsystem
    struct.pack_into("<I", raw, opt + 112 + 4 * 8 + 4, 0x100)  # cert table
    section = opt + 240
    raw[section:section + 8] = b".upx0\x00\x00\x00"
    struct.pack_into("<IIII", raw, section + 8, 0x1000, 0x1000, 0x200, 0x200)
    struct.pack_into("<I", raw, section + 36, 0xA0000020)
    text = (b"Secure Boot policy: enforced\n"
            b"TPM measured boot PCR[7]\n"
            b"rollback counter: 12\n"
            b"ERROR: signature invalid\n"
            b"https://firmware.example.test/update\n"
            b"Version 3.4.5\n")
    raw[0x200:0x200 + len(text)] = text
    return bytes(raw)


class FixtureProject:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        (root / "package.json").write_text(json.dumps({
            "name": "fixture-app",
            "version": "1.2.3",
            "main": "src/server.js",
            "scripts": {"dev": "node src/server.js", "build": "tsc", "test": "jest"},
            "dependencies": {"express": "4.19.2", "axios": "1.7.0"},
            "devDependencies": {"jest": "29.7.0"},
        }), "utf-8")
        (root / "src").mkdir(exist_ok=True)
        (root / "src" / "server.js").write_text(
            "const express = require('express');\n"
            "const app = express();\n"
            "const port = process.env.PORT || 3000;\n"
            "app.get('/api/health', (req, res) => res.json({ok: true}));\n"
            "app.post('/api/items', (req, res) => res.json({}));\n"
            "fetch('https://api.fixture.test/v1/items');\n"
            "// TODO: replace the stub handler\n",
            "utf-8",
        )
        (root / "src" / "config.js").write_text(
            'export const API_KEY = "sk-live-abc123def456";\n'
            "export const BASE = 'https://api.fixture.test';\n",
            "utf-8",
        )
        (root / ".env.example").write_text(
            "API_KEY=sk-live-abc123def456\nDB_URL=postgresql://user:pass@localhost/db\n",
            "utf-8",
        )
        (root / "README.md").write_text(
            "# Fixture\n\n## Install\n\nnpm install\n\n## Usage\n\nnpm run dev\n",
            "utf-8",
        )
        (root / "tests").mkdir(exist_ok=True)
        (root / "tests" / "app.test.js").write_text("test('health', () => {});\n", "utf-8")


class SiteHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep test output clean
        return

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        headers = [("Server", "nginx/1.25"), ("Content-Type", "text/html; charset=utf-8"),
                   ("Content-Security-Policy", "default-src 'self'"),
                   ("Set-Cookie", "sid=1; HttpOnly; Secure; SameSite=Lax"),
                   ("X-Powered-By", "Fixture")]
        if path == "/":
            body = (
                "<!doctype html><html><head><title>Fixture Console</title>"
                '<meta name="generator" content="Next.js" />'
                '<script src="/static/app.js"></script>'
                '<script src="https://cdn.example.test/analytics.js"></script>'
                '<link rel="stylesheet" href="/static/app.css" /></head><body>'
                '<form action="/api/login" method="post"></form>'
                '<a href="/docs">Developer docs</a>'
                '<a href="/admin">Admin</a>'
                '<script>fetch("/api/items").then(r => r.json());</script>'
                "</body></html>"
            ).encode("utf-8")
        elif path == "/robots.txt":
            headers[1] = ("Content-Type", "text/plain")
            body = b"User-agent: *\nDisallow: /admin\n"
        elif path == "/openapi.json":
            headers[1] = ("Content-Type", "application/json")
            body = json.dumps({
                "openapi": "3.0.0",
                "paths": {"/api/items": {"get": {}}, "/api/login": {"post": {}}},
            }).encode("utf-8")
        elif path == "/docs":
            body = b"<html><body><h1>Developer docs</h1><h2>Authentication</h2></body></html>"
        else:
            self.send_response(404)
            for key, value in headers:
                self.send_header(key, value)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        for key, value in headers:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FakeHttp:
    """Stands in for GitHub so the learner is tested without a network call."""

    def __init__(self, responses):
        self.responses = responses
        self.requests = 0
        self.host = "api.github.com"

    def get(self, url, method="GET", limit=engine.MAX_HTTP_BODY):
        self.requests += 1
        for needle in sorted(self.responses, key=len, reverse=True):
            if needle in url:
                payload = self.responses[needle]
                body = payload if isinstance(payload, str) else json.dumps(payload)
                return {"ok": True, "status": 200, "url": url, "headers": {},
                        "body": body, "raw": body.encode("utf-8"), "error": ""}
        return {"ok": False, "status": 404, "url": url, "headers": {},
                "body": "", "raw": b"", "error": "HTTP 404"}


class REATests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "fixture"
        FixtureProject(self.root)
        self._old_investigations = engine.INVESTIGATIONS_DIR
        engine.INVESTIGATIONS_DIR = Path(self.tmp.name) / "dossiers"
        self._old_site = studio.SITE_DIR
        studio.SITE_DIR = Path(self.tmp.name) / "site"
        self._old_mailroom = mailroom.STORE_PATH
        mailroom.STORE_PATH = Path(self.tmp.name) / "mailroom.json"
        self._old_improvements = improvements.STORE_PATH
        improvements.STORE_PATH = Path(self.tmp.name) / "improvements.json"

    def tearDown(self):
        engine.INVESTIGATIONS_DIR = self._old_investigations
        studio.SITE_DIR = self._old_site
        mailroom.STORE_PATH = self._old_mailroom
        improvements.STORE_PATH = self._old_improvements
        self.tmp.cleanup()

    def _real_dossier(self, suffix="lab"):
        """A completed, non-fixture dossier with real-shaped evidence."""
        lab = Path(self.tmp.name) / f"real-{suffix}"
        lab.mkdir(parents=True, exist_ok=True)
        (lab / "app.py").write_text("print('hello')\n", encoding="utf-8")
        inv = engine.new_investigation(
            str(lab), authorized=True, kind="folder", network=False,
            scope="owned lab target",
        )
        inv.update({
            "status": "complete",
            "finished": engine.now_iso(),
            "findings": [
                {"id": "F-001", "probe": "surface.scan", "family": "Surface",
                 "kind": "inventory", "title": "Python entry point found",
                 "detail": "app.py is readable and starts the local workflow.",
                 "path": "app.py", "line": 1, "quote": "", "confidence": 0.93,
                 "tags": ["inventory"]},
                {"id": "F-002", "probe": "code.structure", "family": "Code",
                 "kind": "structure", "title": "Single-module runtime",
                 "detail": "The app is one Python module with no package manifest.",
                 "path": "app.py", "line": 1, "quote": "", "confidence": 0.84,
                 "tags": ["source"]},
                {"id": "F-003", "probe": "manifest.read", "family": "Surface",
                 "kind": "manifest", "title": "No dependency manifest",
                 "detail": "There is no lockfile; the runtime is implicit.",
                 "path": "", "line": None, "quote": "", "confidence": 0.78,
                 "tags": ["manifest"]},
            ],
            "edits": [
                {"id": "E-01", "category": "source", "locator": "app.py:1",
                 "action": "Add a single entry point and record its behaviour before changing it.",
                 "risk": "low", "confidence": 0.86, "requires": [], "note": "",
                 "probe": "code.structure", "evidence": ["F-001"]},
            ],
            "probes": [
                {"id": "surface.scan", "label": "Surface scan", "family": "Surface",
                 "status": "signal", "summary": "3 files read.", "findings": 3, "ms": 12},
                {"id": "git.history", "label": "Git history", "family": "History",
                 "status": "empty", "summary": "No repository.", "findings": 0, "ms": 4},
            ],
            "next_moves": [{"move": "Add a package manifest.",
                            "because": "No dependency manifest was found.",
                            "probe": "manifest.read"}],
            "summary": {
                "verdict": "3 findings mapped across 2 access modes: Source, Configuration.",
                "confidence": 0.82, "kind_label": "Local source tree",
                "counts": {"probes": 2, "signal": 1, "empty": 1, "error": 0,
                           "skipped": 0, "retries": 0, "findings": 3, "edits": 1,
                           "sources": 0, "modes": 2},
                "headline": [],
            },
            "facts": {"languages": ["Python"], "tech": ["Python"],
                      "endpoints": [], "controls": []},
        })
        engine.save(inv)
        return inv

    # -- local ---------------------------------------------------------------
    def test_local_investigation_maps_surfaces_and_escalates(self):
        inv = engine.investigate(str(self.root), authorized=True,
                                 scope="test fixture", network=False)
        self.assertEqual(inv["status"], "complete")
        self.assertEqual(inv["kind"], "folder")
        probes = {p["id"]: p for p in inv["probes"]}
        self.assertEqual(probes["manifest.read"]["status"], "signal")
        self.assertEqual(probes["code.structure"]["status"], "signal")
        self.assertIn("api.surface", probes)
        # git.history has no repo here, so it must route to GitHub rather than fail.
        self.assertEqual(probes["git.history"]["status"], "empty")
        self.assertTrue(probes["source.github"]["retry_of"] == "git.history")
        self.assertEqual(probes["source.github"]["status"], "skipped")
        # Concrete seams: a config surface and a source surface.
        categories = {edit["category"] for edit in inv["edits"]}
        self.assertIn("config", categories)
        self.assertIn("source", categories)
        self.assertIn("protocol", categories)
        blob = json.dumps(inv)
        self.assertIn("/api/health", blob)
        self.assertIn("https://api.fixture.test/v1/items", blob)
        self.assertGreaterEqual(len(inv["graph"]["nodes"]), 4)
        self.assertGreaterEqual(inv["summary"]["counts"]["edits"], 3)
        self.assertTrue(inv["next_moves"])

    def test_binary_strings_error_text_is_a_finding(self):
        binary = self.root / "demo.img"
        binary.write_bytes(b"MZ" + b"\x00" * 128 +
                           b"\nERROR: update failed because signature invalid\n")
        inv = engine.new_investigation(str(binary), authorized=True,
                                       kind="file", network=False)
        ctx = engine.Ctx(inv, network=False)
        ctx.probe = "binary.strings"
        result = engine.PROBES["binary.strings"]["run"](ctx)
        self.assertEqual(result["status"], "signal")
        self.assertTrue(any("update failed" in finding["detail"]
                            for finding in ctx.findings))

    def test_signed_packed_pe_maps_control_surfaces(self):
        binary = self.root / "hardened.exe"
        binary.write_bytes(synthetic_pe())
        inv = engine.investigate(str(binary), authorized=True, kind="file",
                                 network=False, stage="hardened-hardware")
        self.assertEqual(inv["status"], "complete")
        self.assertEqual(inv["stage"], "hardened-hardware")
        probes = {p["id"]: p for p in inv["probes"]}
        self.assertEqual(probes["binary.header"]["status"], "signal")
        self.assertEqual(probes["binary.controls"]["status"], "signal")
        self.assertEqual(probes["binary.strings"]["status"], "signal")
        self.assertTrue(inv["facts"].get("signed"))
        self.assertTrue(any("Secure Boot" in item for item in inv["facts"]["controls"]))
        self.assertTrue(any(finding["kind"] == "control" for finding in inv["findings"]))
        self.assertTrue(any("PE" in finding["title"] for finding in inv["findings"]))
        self.assertTrue(any("Packed" in edit["action"] for edit in inv["edits"]))
        self.assertFalse(any(p["status"] == "error" for p in inv["probes"]))

    def test_unauthorised_target_is_refused(self):
        with self.assertRaises(PermissionError):
            engine.investigate(str(self.root), authorized=False)

    def test_improve_refuses_fixture_targets(self):
        inv = engine.investigate(str(self.root), authorized=True, network=False)
        engine.save(inv)
        self.assertTrue(inv["edits"])
        with self.assertRaises(ValueError):
            improvements.create(
                inv["id"], inv["edits"][0]["id"],
                goal="Make one checked improvement.",
                change="Change one seam only.",
                baseline="Record the current result.",
                verification="Repeat the recorded check.",
                rollback="Restore the recorded baseline.",
                owner="Test owner",
                system_class="software",
                intervention="debloat",
            )

    def test_secrets_are_not_stored(self):
        inv = engine.investigate(str(self.root), authorized=True, network=False)
        blob = json.dumps(inv)
        self.assertNotIn("abc123def456", blob)
        self.assertNotIn("postgresql://user:pass", blob)

    def test_report_and_persistence(self):
        inv = engine.investigate(str(self.root), authorized=True,
                                 scope="report check", network=False)
        engine.save(inv)
        report = engine.report_markdown(inv)
        self.assertIn(str(self.root), report)
        self.assertIn("Edit points", report)
        self.assertIn("Probe ladder", report)
        loaded = engine.load(inv["id"])
        self.assertEqual(loaded["id"], inv["id"])
        self.assertEqual(loaded["target"], inv["target"])
        summaries = engine.list_investigations()
        self.assertTrue(any(item["id"] == inv["id"] for item in summaries))

    def test_github_learner_reads_licence_and_source_without_network(self):
        inv = engine.new_investigation("acme/widget", authorized=True, network=True)
        ctx = engine.Ctx(inv, network=True)
        ctx.repo = "acme/widget"
        ctx.http = FakeHttp({
            "api.github.com/repos/acme/widget/git/trees": {
                "tree": [
                    {"path": "package.json", "type": "blob"},
                    {"path": "README.md", "type": "blob"},
                    {"path": "src/index.js", "type": "blob"},
                ]
            },
            "api.github.com/repos/acme/widget": {
                "full_name": "acme/widget", "stargazers_count": 42,
                "language": "JavaScript", "license": {"spdx_id": "MIT"},
                "default_branch": "main", "pushed_at": "2026-11-01T00:00:00Z",
                "archived": False, "open_issues_count": 3,
                "html_url": "https://github.com/acme/widget",
            },
            "raw.githubusercontent.com/acme/widget/main/package.json":
                '{"name":"widget","scripts":{"build":"x"}}',
            "raw.githubusercontent.com/acme/widget/main/README.md":
                "# Widget\n\n## Install\n\nnpm install\n",
            "raw.githubusercontent.com/acme/widget/main/src/index.js":
                "app.get('/api/widget', handler);",
        })
        ctx.probe = "source.github"
        result = engine.PROBES["source.github"]["run"](ctx)
        self.assertEqual(result["status"], "signal")
        self.assertEqual(ctx.facts["license"], "MIT")
        self.assertIn("package.json", ctx.blobs)
        self.assertIn("src/index.js", ctx.blobs)
        self.assertTrue(any(e["risk"] == "low" and "MIT" in e["locator"]
                            for e in ctx.edits))
        self.assertTrue(any(s["label"] == "GitHub repository" for s in ctx.sources))

    # -- local network -------------------------------------------------------
    def test_arp_wifi_and_interface_parsers(self):
        windows = (
            "Interface: 192.168.1.20 --- 0x9\n"
            "  Internet Address      Physical Address      Type\n"
            "  192.168.1.1           aa-bb-cc-dd-ee-ff     dynamic\n"
            "  192.168.1.23          3c-22-fb-11-22-33     dynamic\n"
            "  224.0.0.22            01-00-5e-00-00-16     static\n"
            "  192.168.1.255         ff-ff-ff-ff-ff-ff     static\n"
        )
        devices = discovery.parse_arp(windows)
        self.assertEqual([item["ip"] for item in devices],
                         ["192.168.1.1", "192.168.1.23"])
        self.assertEqual(devices[1]["vendor"], "Apple")
        self.assertEqual(devices[0]["mac"], "AA:BB:CC:DD:EE:FF")

        wifi = discovery.parse_wifi(
            "    State                  : connected\n"
            "    SSID                   : HomeNet\n"
            "    BSSID                  : 3c:22:fb:11:22:33\n"
            "    Signal                 : 82%\n"
            "    Radio type             : 802.11ac\n"
            "    Authentication         : WPA2-Personal\n"
            "    Channel                : 44\n")
        self.assertEqual(wifi["ssid"], "HomeNet")
        self.assertEqual(wifi["signal"], "82%")
        self.assertEqual(wifi["channel"], "44")

        ipconfig = discovery.parse_ipconfig(
            "Wireless LAN adapter Wi-Fi:\n"
            "   IPv4 Address. . . . . . . . . . . : 192.168.1.20\n"
            "   Subnet Mask . . . . . . . . . . . : 255.255.255.0\n"
            "   Default Gateway . . . . . . . . . : fe80::1%12\n"
            "                                       192.168.1.1\n"
            "   DNS Servers . . . . . . . . . . . : 1.1.1.1\n")
        self.assertIn("192.168.1.20", ipconfig["local_ips"])
        self.assertNotIn("255.255.255.0", ipconfig["local_ips"])
        self.assertEqual(ipconfig["gateway"], "192.168.1.1")
        self.assertIn("1.1.1.1", ipconfig["dns"])

    def test_private_lan_guard_and_vendor_hints(self):
        self.assertEqual(str(discovery.lan_subnet("wifi", ["192.168.1.20"])),
                         "192.168.1.0/24")
        self.assertIsNone(discovery.lan_subnet("8.8.8.0/24", ["192.168.1.20"]))
        self.assertEqual(discovery.mac_vendor("B8:27:EB:11:22:33"), "Raspberry Pi")
        self.assertIn("randomised", discovery.mac_vendor("02:11:22:33:44:55").lower())

    def test_wifi_and_internet_targets_are_classified(self):
        wifi = engine.new_investigation("wifi", authorized=True, kind="wifi",
                                        network=False)
        self.assertEqual(wifi["kind"], "wifi")
        cidr = engine.new_investigation("192.168.1.0/24", authorized=True,
                                        network=False)
        self.assertEqual(cidr["kind"], "wifi")
        egress = engine.new_investigation("internet", authorized=True,
                                          kind="internet", network=False)
        self.assertEqual(egress["kind"], "internet")
        host = engine.new_investigation("example.com", authorized=True,
                                        kind="internet", network=False)
        self.assertEqual(host["kind"], "internet")
        self.assertEqual(host["url"], "https://example.com/")
        with self.assertRaises(ValueError):
            engine.new_investigation("8.8.8.0/24", authorized=True, network=False)

    def test_device_probe_uses_cache_and_bounded_sweep(self):
        inv = engine.new_investigation("wifi", authorized=True, kind="wifi",
                                       network=False)
        ctx = engine.Ctx(inv, network=True)
        ctx.probe = "net.devices"
        ctx.facts.update({"local_ips": ["192.168.1.20"],
                          "gateway": "192.168.1.1"})
        old_sweep = discovery.ping_sweep
        old_arp = discovery.collect_arp
        discovery.ping_sweep = lambda network: (["192.168.1.23"], 254)
        discovery.collect_arp = lambda: [
            {"ip": "192.168.1.1", "mac": "AA:BB:CC:DD:EE:FF",
             "state": "dynamic", "vendor": "randomised MAC"},
            {"ip": "192.168.1.23", "mac": "3C:22:FB:11:22:33",
             "state": "dynamic", "vendor": "Apple"},
            {"ip": "192.168.2.99", "mac": "DE:AD:BE:EF:00:01",
             "state": "static", "vendor": ""},
        ]
        try:
            result = engine.PROBES["net.devices"]["run"](ctx)
        finally:
            discovery.ping_sweep = old_sweep
            discovery.collect_arp = old_arp
        self.assertEqual(result["status"], "signal")
        devices = {item["ip"]: item for item in ctx.facts["devices"]}
        self.assertIn("192.168.1.1", devices)
        self.assertIn("192.168.1.23", devices)
        self.assertNotIn("192.168.2.99", devices)
        self.assertEqual(devices["192.168.1.23"]["vendor"], "Apple")
        self.assertTrue(devices["192.168.1.23"]["answered"])
        self.assertEqual(ctx.facts["sweep_responded"], 1)
        self.assertTrue(any(edit["category"] == "network" for edit in ctx.edits))

    def test_passive_wifi_skips_sweep(self):
        inv = engine.new_investigation("wifi", authorized=True, kind="wifi",
                                       network=True, sweep=False,
                                       stage="wifi-internal")
        self.assertFalse(inv["sweep"])
        self.assertEqual(inv["stage"], "wifi-internal")
        old_local = discovery.local_network
        old_arp = discovery.collect_arp
        discovery.local_network = lambda: {
            "hostname": "test-host", "platform": "test",
            "wifi": {"ssid": "TestNet"}, "local_ips": ["192.168.1.20"],
            "gateway": "192.168.1.1", "dns": ["1.1.1.1"],
        }
        discovery.collect_arp = lambda: []
        try:
            engine.run_investigation(inv, network=True)
        finally:
            discovery.local_network = old_local
            discovery.collect_arp = old_arp
        ids = [record["id"] for record in inv["probes"]]
        self.assertIn("net.local", ids)
        self.assertNotIn("net.devices", ids)
        self.assertEqual(inv["status"], "complete")

    # -- playbook and ideas --------------------------------------------------
    def test_playbook_catalogue_and_brief_generator(self):
        import playbook

        data = playbook.payload()
        self.assertGreaterEqual(len(data["professions"]), 12)
        self.assertGreaterEqual(len(data["ideas"]), 10)
        self.assertGreaterEqual(len(data["guides"]), 6)
        self.assertGreaterEqual(len(data["prompts"]), 7)
        self.assertTrue(all("prompt" in profession for profession in data["professions"]))

        brief = playbook.generate_brief("vehicle-intelligence", target="C:/diag.apk",
                                        goal="map the diagnostic protocol", variant=1)
        self.assertGreaterEqual(len(brief["phases"]), 5)
        self.assertIn("C:/diag.apk", brief["prompts"][0]["text"])
        self.assertEqual(brief["start"]["kind"], "file")
        self.assertEqual(brief["angle"], "Compatibility")
        self.assertTrue(any(probe["id"] == "binary.header" for probe in brief["probes"]))

        custom = playbook.generate_brief("custom", target="https://example.test",
                                         goal="reverse a smart TV firmware")
        self.assertEqual(custom["start"]["kind"], "website")
        self.assertTrue(any(probe["id"] == "device.firmware" for probe in custom["probes"]))

        self.assertTrue(any(idea["id"] == "rounding-leak" for idea in data["ideas"]))
        rounding = playbook.generate_brief("rounding-leak", target="payroll.csv",
                                           goal="find rounding leakage")
        self.assertEqual(rounding["id"], "rounding-leak")
        self.assertTrue(rounding["phases"])

        self.assertEqual(len(data["range"]), 4)
        self.assertTrue(all(stage["stop"] for stage in data["range"]))
        self.assertFalse(data["range"][0]["start"]["sweep"])
        self.assertTrue(any(stage["id"] == "encrypted-systems" for stage in data["range"]))
        for stage in data["range"]:
            queue = set(engine.ORDER.get(stage["start"]["kind"], []))
            advertised = {probe["id"] if isinstance(probe, dict) else probe
                          for probe in stage["probes"]}
            self.assertLessEqual(advertised, queue, stage["id"])

    def test_revenue_offer_is_productised_and_honest(self):
        result = revenue.build_offer(
            "reverse engineering", buyer="hardware vendors", model_id="firmware-teardown")
        offer = result["offer"]
        self.assertEqual(offer["model_id"], "firmware-teardown")
        self.assertIn("reverse engineering", offer["title"].lower())
        self.assertTrue(offer["price"])
        self.assertTrue(offer["deliverables"])
        self.assertTrue(offer["first_10"])
        self.assertTrue(offer["outreach"])
        self.assertTrue(offer["risks"])
        self.assertIn("not a guarantee", result["note"].lower())
        self.assertIn("hypothesis", offer["pricing_basis"].lower())
        with self.assertRaises(ValueError):
            revenue.build_offer("")
        with self.assertRaises(ValueError):
            revenue.build_offer("reverse engineering", model_id="fake-route")

    def test_revenue_demand_scan_parses_public_signals(self):
        old_fetch = revenue._fetch_json
        revenue._fetch_json = lambda url, timeout=8: {"hits": [{
            "objectID": "12345",
            "title": "Looking for firmware reverse engineering help",
            "comment_text": "We need help mapping a device update path. Budget approved.",
            "created_at_i": 1_760_000_000,
            "_tags": ["comment", "story_999"],
            "points": 12,
            "num_comments": 4,
        }]}
        try:
            result = revenue.scan_demand("reverse engineering", "firmware", limit=5)
        finally:
            revenue._fetch_json = old_fetch
        self.assertEqual(len(result["signals"]), 1)
        signal = result["signals"][0]
        self.assertEqual(signal["source"], "Hacker News")
        self.assertEqual(signal["kind"], "comment")
        self.assertIn("budget", signal["snippet"].lower())
        self.assertIn("paid expertise", signal["why"].lower())
        self.assertIn("news.ycombinator.com/item?id=12345", signal["url"])
        self.assertIn("not a lead list", result["note"].lower())
        with self.assertRaises(ValueError):
            revenue.scan_demand("")

    def test_revenue_models_are_real_routes(self):
        data = revenue.models_payload()
        self.assertGreaterEqual(len(data["models"]), 6)
        self.assertTrue(all(model["price"] and model["deliverables"] for model in data["models"]))
        self.assertIn("hypotheses", data["note"].lower())

    def test_media_is_brand_and_checkout_only(self):
        data = media.payload()
        self.assertEqual(data["channel"]["name"], "The Teardown Room")
        self.assertTrue(data["channel"]["description"])
        self.assertNotIn("slate", data)
        self.assertNotIn("money_path", data)
        self.assertNotIn("crew", data)
        self.assertGreaterEqual(len(data["stack"]), 7)
        self.assertTrue(any("OBS" in item["tool"] for item in data["stack"]))
        self.assertTrue(any("DaVinci" in item["tool"] for item in data["stack"]))
        self.assertTrue(data["principles"])
        self.assertEqual(data["stripe"]["statement_descriptor"], "HELIOSTD")
        self.assertNotIn("payment_link", data["stripe"])
        self.assertNotIn("product_name", data["stripe"])
        self.assertTrue(data["stripe"]["donation_link"].startswith("https://buy.stripe.com/"))
        self.assertIn("£5", data["stripe"]["donation_price"])
        self.assertEqual(data["stripe"]["donation_name"], "HeliosTD Donation")
        self.assertEqual(data["contact_email"], "mysurnameandfirsname@gmail.com")
        self.assertNotIn("£29", json.dumps(data))
        self.assertNotIn("sk_live", json.dumps(data).lower())
        self.assertNotIn("secret key value", json.dumps(data).lower())

    def test_mailroom_is_opt_in_deduplicated_and_exportable(self):
        first = mailroom.subscribe(
            "Reader@Example.com", "Ada", ["monthly"], source="pricing-donation",
            donated=True, consent=True)
        self.assertEqual(first["status"], "active")
        self.assertTrue(first["donated"])
        second = mailroom.subscribe(
            "reader@example.com", "Ada", ["monthly", "evidence"],
            source="field-notes", consent=True)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(mailroom.stats()["total"], 1)
        self.assertEqual(mailroom.stats()["active"], 1)
        with self.assertRaises(ValueError):
            mailroom.subscribe("not-an-email", consent=True)
        with self.assertRaises(ValueError):
            mailroom.subscribe("other@example.com", consent=False)
        admin = mailroom.payload()
        self.assertEqual(admin["contacts"][0]["email"], "reader@example.com")
        self.assertNotIn("token", admin["contacts"][0])
        self.assertIn("reader@example.com", mailroom.export_csv())
        mailroom.set_status(first["id"], "unsubscribed")
        self.assertEqual(mailroom.stats()["unsubscribed"], 1)
        self.assertNotIn("reader@example.com", json.dumps(mailroom.public_payload()))

    def test_monthly_draft_uses_real_evidence_and_stays_draft_until_ready(self):
        pub = studio.build_publication(
            self._real_dossier("letter"), site_root=studio.SITE_DIR)
        latest, facts = studio.monthly_material()
        self.assertEqual(latest["slug"], pub["slug"])
        issue = mailroom.draft_issue("2026-11", publication=latest, facts=facts)
        self.assertEqual(issue["status"], "draft")
        self.assertTrue(issue["sections"])
        self.assertEqual(mailroom.public_issues(), [])
        ready = mailroom.set_issue_status(issue["id"], "ready")
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(len(mailroom.public_issues()), 1)
        self.assertIn("The limit", mailroom.issue_markdown(ready))
        mailroom.set_issue_status(issue["id"], "sent")
        with self.assertRaises(ValueError):
            mailroom.draft_issue("2026-11", publication=latest, facts=facts)

    def test_studio_gate_rejects_thin_unauthorised_and_fixture_dossiers(self):
        inv = self._real_dossier("gate")
        self.assertTrue(studio.gate_for(inv)["ready"])

        unauthorised = dict(inv)
        unauthorised["authorized"] = False
        self.assertFalse(studio.gate_for(unauthorised)["ready"])

        running = dict(inv)
        running["status"] = "running"
        self.assertFalse(studio.gate_for(running)["ready"])

        thin = dict(inv)
        thin["findings"] = inv["findings"][:1]
        self.assertFalse(studio.gate_for(thin)["ready"])
        self.assertIn("evidence records", studio.gate_for(thin)["reason"])

        demo = self._real_dossier("demo")
        self.assertTrue(studio.is_fixture_target(demo["target"]))
        self.assertFalse(studio.gate_for(demo)["ready"])
        with self.assertRaises(ValueError):
            studio.build_publication(demo, site_root=Path(self.tmp.name) / "site")

    def test_studio_payload_hides_fixtures_and_exposes_production_crew(self):
        inv = self._real_dossier("payload")
        self._real_dossier("demo")
        data = studio.payload(site_root=Path(self.tmp.name) / "site")
        self.assertEqual(data["totals"]["ready"], 1)
        self.assertEqual(data["totals"]["real_dossiers"], 1)
        self.assertEqual(data["hidden_fixtures"], 1)
        self.assertEqual(data["candidates"][0]["id"], inv["id"])
        self.assertEqual(len(data["pipeline"]), 5)
        self.assertEqual(len(data["crew_lanes"]), 6)
        self.assertTrue(all(lane["crew"] for lane in data["crew_lanes"]))
        self.assertNotIn("slate", data)

    def test_studio_builds_real_evidence_site_and_video_script(self):
        inv = self._real_dossier("publish")
        inv["findings"][0]["detail"] += " token=sk-live-should-not-publish"
        inv["findings"].append({
            "id": "F-004", "probe": "test.surface", "family": "Code",
            "kind": "test", "title": "Test-only fixture URL",
            "detail": "https://api.fixture.test/v1/items", "path": "test_engine.py",
            "line": 1, "quote": "", "confidence": 0.99, "tags": ["test"],
        })
        site = Path(self.tmp.name) / "site"
        pub = studio.build_publication(inv, site_root=site)
        page_path = site / "publications" / pub["slug"] / "index.html"
        evidence_path = site / "publications" / pub["slug"] / "evidence.md"
        video_path = site / "publications" / pub["slug"] / "video-script.md"
        card_path = site / "publications" / pub["slug"] / "card.svg"
        for path in (page_path, evidence_path, video_path, card_path,
                     site / "index.html", site / "assets" / "site.css",
                     site / "evidence" / "index.html", site / "facts" / "index.html",
                     site / "guides" / "index.html", site / "method" / "index.html",
                     site / "crew" / "index.html", site / "proof" / "index.html",
                     site / "newsletter" / "index.html", site / "pricing" / "index.html",
                     site / "start" / "index.html", site / "watch" / "index.html",
                     site / "glossary" / "index.html",
                     site / "assets" / "site.js", site / "assets" / "search-index.json"):
            self.assertTrue(path.is_file(), str(path))
        page = page_path.read_text("utf-8")
        evidence = evidence_path.read_text("utf-8")
        video = video_path.read_text("utf-8")
        index = (site / "index.html").read_text("utf-8")
        proof = (site / "proof" / "index.html").read_text("utf-8")
        facts_index = (site / "facts" / "index.html").read_text("utf-8")
        pricing = (site / "pricing" / "index.html").read_text("utf-8")
        newsletter = (site / "newsletter" / "index.html").read_text("utf-8")
        self.assertIn("Field guides", index)
        self.assertIn("Choose your way in", index)
        self.assertIn("Search the room", index)
        self.assertIn("path-grid", index)
        start = (site / "start" / "index.html").read_text("utf-8")
        watch = (site / "watch" / "index.html").read_text("utf-8")
        glossary = (site / "glossary" / "index.html").read_text("utf-8")
        search = json.loads((site / "assets" / "search-index.json").read_text("utf-8"))
        self.assertIn("Three routes. One evidence standard.", start)
        self.assertIn("data-evidence-reader", start)
        self.assertIn("field-note.mp4", watch)
        self.assertIn("field-note.vtt", watch)
        self.assertIn("Read the transcript", watch)
        self.assertIn("Ten field notes", watch)
        self.assertIn("note-card", watch)
        self.assertIn("George · forward", watch)
        self.assertIn("Susan · light", watch)
        self.assertNotIn("teardown-room-explainer", watch)
        self.assertEqual(len(list((site / "watch").glob("*/index.html"))), 10)
        note_ten = (site / "watch" / "10" / "index.html").read_text("utf-8")
        self.assertIn("What the room refuses", note_ten)
        self.assertIn("Susan · light", note_ten)
        self.assertIn("Locator", glossary)
        self.assertGreaterEqual(len(search), 20)
        self.assertTrue(any(item["url"] == "start/" for item in search))
        self.assertTrue(any(item["url"].startswith("facts/") for item in search))
        self.assertIn("no customer testimonials", proof.lower())
        self.assertIn("Fact files", facts_index)
        self.assertIn("monthly field notes", newsletter.lower())
        self.assertIn("subscribe-form", newsletter)
        self.assertIn("mysurnameandfirsname@gmail.com", newsletter)
        self.assertIn("£5", pricing)
        self.assertNotIn("£29", index + pricing + newsletter)
        self.assertNotIn("payment_link", index + pricing + newsletter)
        self.assertIn("site.js", index)
        self.assertGreaterEqual(len(list((site / "facts").glob("*.html"))), 5)
        self.assertIn("Python entry point found", page)
        self.assertIn(pub["digest"], page)
        self.assertIn("F-001", evidence)
        self.assertIn("Shot list", video)
        self.assertIn("Python entry point found", video)
        self.assertIn(pub["title"], index)
        self.assertIn(pub["slug"], index)
        self.assertNotIn("should-not-publish", page + evidence + video)
        self.assertNotIn(str(HERE), page + evidence + video)
        self.assertNotIn("api.fixture.test", page + video)
        self.assertIn("api.fixture.test", evidence)
        self.assertEqual(len(studio.published(site)), 1)

    # -- autonomy ------------------------------------------------------------
    def test_autonomy_routes_the_control_map_and_keeps_boundaries(self):
        binary = self.root / "hardened.exe"
        binary.write_bytes(synthetic_pe())
        plan = autonomy.build_plan(
            str(binary), stage_id="hardened-hardware", depth="deep", network=False)
        self.assertEqual(plan["kind"], "file")
        self.assertEqual(plan["stage"]["id"], "hardened-hardware")
        self.assertTrue(plan["phases"])
        steps = [step for phase in plan["phases"] for step in phase["steps"]]
        self.assertTrue(any(step["id"] == "binary.controls" for step in steps))
        self.assertTrue(any(step["id"] == "device.firmware" for step in steps))
        self.assertTrue(plan["stop"])
        self.assertTrue(any("control" in note.lower() for note in plan["notes"]))
        self.assertIn("binary.controls", autonomy.plan_text(plan))
        self.assertEqual(plan["counts"]["blocked"], 0)

        wifi = autonomy.build_plan("wifi", kind="wifi", stage_id="wifi-internal",
                                   depth="standard", network=True, sweep=False)
        wifi_ids = [step["id"] for phase in wifi["phases"] for step in phase["steps"]]
        self.assertIn("net.local", wifi_ids)
        self.assertNotIn("net.devices", wifi_ids)
        self.assertTrue(any("sweep" in item.lower() for item in wifi["stop"]))

        with self.assertRaises(ValueError):
            autonomy.build_plan("", stage_id="hardened-hardware")
        with self.assertRaises(ValueError):
            autonomy.build_plan(str(binary), depth="turbo")

    def test_depth_quick_limits_the_route_and_deep_expands_it(self):
        quick = engine.investigate(str(self.root), authorized=True,
                                   network=False, depth="quick")
        deep = engine.investigate(str(self.root), authorized=True,
                                  network=False, depth="deep")
        self.assertEqual(quick["depth"], "quick")
        self.assertEqual(deep["depth"], "deep")
        self.assertLess(len(quick["probes"]), len(deep["probes"]))
        self.assertEqual(deep["status"], "complete")
        self.assertTrue(any(probe["id"] == "binary.controls" for probe in deep["probes"]))

    # -- captured traffic ----------------------------------------------------
    def test_har_capture_maps_protocol_without_storing_secrets(self):
        har = {
            "log": {
                "version": "1.2",
                "creator": {"name": "DevTools", "version": "1.0"},
                "entries": [
                    {
                        "request": {
                            "method": "GET",
                            "url": "https://api.fixture.test/v1/items?token=sk-live-abc123def456",
                            "headers": [{"name": "Authorization",
                                         "value": "Bearer sk-live-abc123def456"}],
                            "queryString": [{"name": "token",
                                             "value": "sk-live-abc123def456"}],
                        },
                        "response": {"status": 200,
                                     "headers": [{"name": "Server", "value": "nginx"}],
                                     "content": {"mimeType": "application/json"},
                                     "_securityDetails": {
                                         "protocol": "TLS 1.3",
                                         "cipher": "TLS_AES_128_GCM_SHA256",
                                         "issuerName": "Synthetic Test CA",
                                         "subjectName": "api.fixture.test",
                                         "validTo": "2027-01-01T00:00:00Z",
                                     }},
                    },
                    {
                        "request": {
                            "method": "POST",
                            "url": "https://api.fixture.test/v1/items",
                            "headers": [{"name": "Content-Type", "value": "application/json"}],
                            "postData": {"mimeType": "application/json",
                                         "text": '{"name":"x"}'},
                        },
                        "response": {"status": 201, "headers": [],
                                     "content": {"mimeType": "application/json"}},
                    },
                    {
                        "request": {"method": "GET", "url": "https://cdn.fixture.test/app.js",
                                    "headers": []},
                        "response": {"status": 200, "headers": [],
                                     "content": {"mimeType": "text/javascript"}},
                    },
                    {"request": {"method": "GET", "url": "data:image/png;base64,AAAA"},
                     "response": {"status": 0}},
                ],
            }
        }
        capture = self.root / "capture.har"
        capture.write_text(json.dumps(har), "utf-8")
        inv = engine.investigate(str(capture), authorized=True, network=False,
                                 stage="encrypted-systems")
        self.assertEqual(inv["status"], "complete")
        self.assertEqual(inv["kind"], "har")
        self.assertEqual(inv["stage"], "encrypted-systems")
        self.assertFalse(any(p["status"] == "error" for p in inv["probes"]))
        probes = {p["id"]: p for p in inv["probes"]}
        self.assertEqual(probes["traffic.har"]["status"], "signal")
        self.assertIn("3 requests", probes["traffic.har"]["summary"])
        self.assertIn("2 host(s)", probes["traffic.har"]["summary"])
        self.assertEqual(inv["facts"]["tls"]["protocols"], ["TLS 1.3"])
        self.assertTrue(any(finding["kind"] == "tls" for finding in inv["findings"]))
        self.assertTrue(any("api.fixture.test" in endpoint
                            for endpoint in inv["facts"]["endpoints"]))
        self.assertTrue(any(edit["category"] == "protocol" for edit in inv["edits"]))
        self.assertTrue(any(edit["risk"] == "high" and "token" in edit["locator"].lower()
                            for edit in inv["edits"]))
        self.assertTrue(any(source["label"] == "Captured traffic (HAR)"
                            for source in inv["sources"]))
        self.assertGreaterEqual(len(inv["graph"]["nodes"]), 3)
        blob = json.dumps(inv).lower()
        self.assertNotIn("abc123def456", blob)
        self.assertNotIn("bearer sk", blob)
        self.assertIn("authorization", blob)

    def test_malformed_har_is_a_dead_end_with_a_next_move(self):
        capture = self.root / "broken.har"
        capture.write_text("{not json", "utf-8")
        inv = engine.investigate(str(capture), authorized=True, network=False)
        self.assertEqual(inv["status"], "complete")
        probes = {p["id"]: p for p in inv["probes"]}
        self.assertEqual(probes["traffic.har"]["status"], "empty")
        self.assertTrue(any("HAR" in move["move"] for move in inv["next_moves"]))

    # -- website -------------------------------------------------------------
    def test_website_probes_and_escalation(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), SiteHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}/"
            inv = engine.investigate(base, authorized=True, network=True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        self.assertEqual(inv["status"], "complete")
        self.assertEqual(inv["kind"], "website")
        probes = {p["id"]: p for p in inv["probes"]}
        self.assertEqual(probes["web.fetch"]["status"], "signal")
        self.assertEqual(probes["web.headers"]["status"], "signal")
        self.assertEqual(probes["web.endpoints"]["status"], "signal")
        self.assertIn("Next.js", inv["facts"]["tech"])
        blob = json.dumps(inv["findings"])
        self.assertIn("/api/items", blob)
        self.assertIn("/api/login", blob)
        self.assertTrue(any(edit["category"] == "protocol" for edit in inv["edits"]))
        self.assertTrue(any(source["label"] == "API description" for source in inv["sources"]))
        self.assertGreaterEqual(len(inv["graph"]["nodes"]), 5)

    # -- console -------------------------------------------------------------
    def test_console_api_end_to_end(self):
        import server as rea_server

        real = self._real_dossier("console")
        httpd = rea_server.make_server(0)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=5) as response:
                health = json.loads(response.read().decode("utf-8"))
            self.assertTrue(health["ok"])
            with urllib.request.urlopen(base + "/", timeout=5) as response:
                page = response.read().decode("utf-8")
            self.assertIn("Engine Room", page)
            self.assertIn("Manage the work. Review the surfaces.", page)
            self.assertIn("aria-label=\"Main areas\"", page)
            self.assertIn(">Overview</button>", page)
            self.assertIn("href=\"/published/\"", page)
            self.assertIn("href=\"/mailroom/\"", page)
            self.assertNotIn("45-second", page)
            self.assertIn("data-view=\"engine\"", page)
            self.assertIn("data-view=\"improve\"", page)
            self.assertIn("Any system. Evidence in. Change out. Proof attached.", page)
            self.assertIn("System class", page)
            self.assertIn("Change level", page)
            self.assertIn("TVs, cars, appliances, alarms", page)
            self.assertIn("Where is it?", page)
            self.assertIn("Local folder / this computer", page)
            self.assertIn("Same network / WiFi", page)
            self.assertIn("Public internet / host", page)
            self.assertIn("Investigate &amp; suggest", page)
            self.assertIn("Suggest from dossier", page)
            self.assertIn("Suggested improvements", page)
            self.assertIn("Approve", page)
            self.assertIn("Decline", page)
            self.assertIn('id="improve-source"', page)
            self.assertIn('id="improve-target"', page)
            self.assertIn('id="improve-investigate"', page)
            self.assertIn('id="improve-suggest"', page)
            self.assertIn('id="improve-suggestions"', page)
            self.assertIn('id="improve-brief"', page)
            self.assertIn('id="hub"', page)
            self.assertIn('data-hub-scan="local"', page)
            self.assertIn('data-hub-scan="wifi"', page)
            self.assertIn('data-hub-scan="external"', page)
            self.assertIn('id="hub-plan"', page)
            self.assertIn('id="crew-personas"', page)
            self.assertIn('id="crew-search"', page)
            self.assertIn('id="crew-lens-hint"', page)
            self.assertIn("Smallest reversible change", page)
            self.assertIn("One dossier. One evidence file.", page)
            self.assertIn("data-view=\"studio\"", page)
            self.assertIn("The reference shelf", page)
            self.assertIn("data-view=\"method\"", page)
            self.assertIn("Playbook", page)
            self.assertIn("Operator manual", page)
            self.assertIn("Revenue Engine", page)
            self.assertIn("Turn a capability into a paid offer.", page)
            self.assertIn("Scan live demand", page)
            self.assertIn("data-view=\"revenue\"", page)
            self.assertIn("The Teardown Room", page)
            self.assertNotIn("Media Studio", page)
            self.assertNotIn("6-week launch slate", page)
            self.assertNotIn("data-view=\"media\"", page)
            self.assertNotIn("Money Glitch Lab", page)
            self.assertIn("Authorised capability range", page)
            self.assertIn("Start simple. Earn every next step.", page)
            self.assertIn("reverse-engineers as aggressively", page)
            self.assertIn("graph-hint", page)
            self.assertIn("graph-wrap", page)
            self.assertIn("data-view=\"autonomy\"", page)
            self.assertIn("Plan the route. Then make it happen.", page)
            self.assertIn("Probe inventory", page)
            self.assertIn("Run route", page)

            with urllib.request.urlopen(base + "/api/playbook", timeout=5) as response:
                book = json.loads(response.read().decode("utf-8"))
            self.assertGreaterEqual(len(book["ideas"]), 10)
            self.assertGreaterEqual(len(book["professions"]), 15)
            self.assertGreaterEqual(len(book["personas"]), 12)
            self.assertEqual(len(book["range"]), 4)

            with urllib.request.urlopen(base + "/api/revenue/models", timeout=5) as response:
                routes = json.loads(response.read().decode("utf-8"))
            self.assertGreaterEqual(len(routes["models"]), 6)

            offer_request = urllib.request.Request(
                base + "/api/revenue/offer",
                data=json.dumps({"capability": "reverse engineering",
                                 "buyer": "hardware vendors",
                                 "model": "firmware-teardown"}).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(offer_request, timeout=5) as response:
                offer = json.loads(response.read().decode("utf-8"))["offer"]
            self.assertEqual(offer["model_id"], "firmware-teardown")
            self.assertTrue(offer["first_10"])

            old_fetch = revenue._fetch_json
            revenue._fetch_json = lambda url, timeout=8: {"hits": [{
                "objectID": "998877",
                "title": "Need help reverse engineering an update path",
                "story_text": "We need paid help with firmware. Budget approved.",
                "created_at_i": 1_760_000_000,
                "_tags": ["story"],
                "points": 9,
                "num_comments": 2,
            }]}
            try:
                scan_request = urllib.request.Request(
                    base + "/api/revenue/scan",
                    data=json.dumps({"capability": "reverse engineering",
                                     "buyer": "firmware", "limit": 5}).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(scan_request, timeout=5) as response:
                    scan = json.loads(response.read().decode("utf-8"))
            finally:
                revenue._fetch_json = old_fetch
            self.assertEqual(scan["signals"][0]["source"], "Hacker News")
            self.assertIn("public", scan["note"].lower())

            with urllib.request.urlopen(base + "/api/media", timeout=5) as response:
                kit = json.loads(response.read().decode("utf-8"))
            self.assertEqual(kit["channel"]["name"], "The Teardown Room")
            self.assertNotIn("slate", kit)
            self.assertNotIn("money_path", kit)
            self.assertEqual(kit["stripe"]["statement_descriptor"], "HELIOSTD")
            self.assertNotIn("payment_link", kit["stripe"])
            self.assertTrue(kit["stripe"]["donation_link"].startswith("https://buy.stripe.com/"))

            subscribe_request = urllib.request.Request(
                base + "/api/newsletter/subscribe",
                data=json.dumps({"email": "reader@example.com", "name": "Reader",
                                 "topics": ["monthly"], "consent": True,
                                 "donated": True, "source": "test"}).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(subscribe_request, timeout=5) as response:
                subscribed = json.loads(response.read().decode("utf-8"))
            self.assertTrue(subscribed["ok"])
            self.assertEqual(subscribed["contact"]["status"], "active")
            with urllib.request.urlopen(base + "/api/newsletter", timeout=5) as response:
                newsletter = json.loads(response.read().decode("utf-8"))
            self.assertNotIn("reader@example.com", json.dumps(newsletter))
            with urllib.request.urlopen(base + "/api/mailroom", timeout=5) as response:
                mailroom_data = json.loads(response.read().decode("utf-8"))
            self.assertEqual(mailroom_data["stats"]["active"], 1)
            self.assertEqual(mailroom_data["contacts"][0]["email"], "reader@example.com")
            with urllib.request.urlopen(base + "/mailroom/", timeout=5) as response:
                self.assertEqual(response.status, 200)

            with urllib.request.urlopen(base + "/api/studio", timeout=5) as response:
                engine_room = json.loads(response.read().decode("utf-8"))
            self.assertEqual(engine_room["candidates"][0]["id"], real["id"])
            self.assertEqual(engine_room["hidden_fixtures"], 0)
            self.assertEqual(len(engine_room["pipeline"]), 5)
            self.assertEqual(len(engine_room["crew_lanes"]), 6)
            self.assertNotIn("slate", engine_room)

            with urllib.request.urlopen(base + "/api/improvements", timeout=5) as response:
                initial_improvements = json.loads(response.read().decode("utf-8"))
            self.assertEqual(initial_improvements["plans"], [])
            class_ids = {item["id"] for item in initial_improvements["system_classes"]}
            level_ids = {item["id"] for item in initial_improvements["interventions"]}
            self.assertTrue({"software", "mobile", "web", "streaming", "vehicle", "appliance",
                             "alarm", "smart", "network", "embedded"} <= class_ids)
            self.assertTrue({"observe", "configure", "debloat", "interface", "extend",
                             "replace"} <= level_ids)

            suggest_request = urllib.request.Request(
                base + "/api/improvements/suggest",
                data=json.dumps({"dossier": real["id"],
                                 "goal": "Remove bloat and put our own interface in.",
                                 "system_class": "software"}).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(suggest_request, timeout=5) as response:
                suggested = json.loads(response.read().decode("utf-8"))
            self.assertGreaterEqual(len(suggested["suggestions"]), 2)
            suggestion = suggested["suggestions"][0]
            self.assertEqual(suggestion["status"], "suggested")
            self.assertTrue(suggestion["why"])
            self.assertTrue(suggestion["baseline"])
            self.assertIn(suggestion["intervention"], {"debloat", "interface"})

            approve_suggestion_request = urllib.request.Request(
                base + "/api/improvements/suggestions/approve",
                data=json.dumps({"id": suggestion["id"], "owner": "Test owner"}).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(approve_suggestion_request, timeout=5) as response:
                approved = json.loads(response.read().decode("utf-8"))
            self.assertEqual(approved["suggestion"]["status"], "approved")
            self.assertEqual(approved["plan"]["suggestion_id"], suggestion["id"])
            self.assertEqual(approved["plan"]["status"], "draft")

            second = suggested["suggestions"][1]
            decline_request = urllib.request.Request(
                base + "/api/improvements/suggestions/decline",
                data=json.dumps({"id": second["id"]}).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(decline_request, timeout=5) as response:
                declined = json.loads(response.read().decode("utf-8"))["suggestion"]
            self.assertEqual(declined["status"], "declined")

            def improvement_body(**overrides):
                data = {
                    "dossier": real["id"], "seam": real["edits"][0]["id"],
                    "goal": "Make the entry point explicit without changing behaviour.",
                    "change": "Add a main guard and change nothing else.",
                    "baseline": "python app.py prints hello.",
                    "verification": "Run the same command and compare its output.",
                    "rollback": "Restore app.py from the recorded baseline.",
                    "patch": "if __name__ == '__main__':\n    main()",
                    "owner": "Test owner",
                    "system_class": "software", "intervention": "debloat",
                }
                data.update(overrides)
                return data

            unsafe_request = urllib.request.Request(
                base + "/api/improvements",
                data=json.dumps(improvement_body(system_class="alarm",
                                                 intervention="debloat")).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as unsafe:
                urllib.request.urlopen(unsafe_request, timeout=5)
            self.assertEqual(unsafe.exception.code, 400)

            improve_request = urllib.request.Request(
                base + "/api/improvements",
                data=json.dumps(improvement_body()).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(improve_request, timeout=5) as response:
                improvement = json.loads(response.read().decode("utf-8"))["plan"]
            self.assertEqual(improvement["status"], "draft")
            self.assertEqual(improvement["dossier_id"], real["id"])
            self.assertEqual(improvement["seam"]["id"], real["edits"][0]["id"])
            self.assertEqual(improvement["owner"], "Test owner")
            self.assertEqual(improvement["system_class"], "software")
            self.assertEqual(improvement["intervention"], "debloat")
            self.assertEqual(improvement["history"][0]["status"], "draft")

            bad_transition = urllib.request.Request(
                base + "/api/improvements/status",
                data=json.dumps({"id": improvement["id"],
                                 "status": "verified"}).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as skipped:
                urllib.request.urlopen(bad_transition, timeout=5)
            self.assertEqual(skipped.exception.code, 400)

            for next_status in ("approved", "baselined", "changed", "verified"):
                status_request = urllib.request.Request(
                    base + "/api/improvements/status",
                    data=json.dumps({"id": improvement["id"],
                                     "status": next_status}).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(status_request, timeout=5) as response:
                    updated = json.loads(response.read().decode("utf-8"))["plan"]
                self.assertEqual(updated["status"], next_status)
            self.assertEqual(len(updated["history"]), 5)

            with urllib.request.urlopen(
                    base + "/api/improvements/" + improvement["id"] + "/brief.md",
                    timeout=5) as response:
                change_brief = response.read().decode("utf-8")
            self.assertIn("# Change brief", change_brief)
            self.assertIn("## Rollback", change_brief)
            self.assertIn("## Class limits", change_brief)
            self.assertIn("## Change-level conditions", change_brief)
            self.assertIn("    main()", change_brief)
            self.assertIn("A human approves the plan and checks the outcome", change_brief)
            with urllib.request.urlopen(base + "/api/improvements", timeout=5) as response:
                final_improvements = json.loads(response.read().decode("utf-8"))
            self.assertEqual(final_improvements["counts"]["verified"], 1)

            publish_request = urllib.request.Request(
                base + "/api/studio/publish",
                data=json.dumps({"investigation": real["id"]}).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(publish_request, timeout=5) as response:
                publication = json.loads(response.read().decode("utf-8"))["publication"]
            self.assertTrue(publication["page_url"].startswith("/published/"))
            self.assertTrue((studio.SITE_DIR / "publications" / publication["slug"] /
                             "index.html").is_file())
            with urllib.request.urlopen(base + publication["page_url"], timeout=5) as response:
                published_page = response.read().decode("utf-8")
            self.assertIn("Python entry point found", published_page)
            self.assertIn(publication["digest"], published_page)
            with urllib.request.urlopen(base + "/published/", timeout=5) as response:
                published_index = response.read().decode("utf-8")
            self.assertIn(publication["title"], published_index)
            self.assertIn("The Teardown Room", published_index)
            self.assertIn('href="download/"', published_index)
            download_page = (studio.SITE_DIR / "download" / "index.html").read_text(encoding="utf-8")
            self.assertIn("github.com/Hudmebac/heliosrea", download_page)
            self.assertIn("python rea/server.py", download_page)
            self.assertIn("REA.bat", download_page)
            self.assertIn("local preview of this public website", download_page)
            self.assertIn("Official site", published_index)

            brief_request = urllib.request.Request(
                base + "/api/ideas/generate",
                data=json.dumps({"idea_id": "shadow-it",
                                 "target": "https://example.test",
                                 "goal": "find shadow systems"}).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(brief_request, timeout=5) as response:
                brief = json.loads(response.read().decode("utf-8"))["brief"]
            self.assertEqual(brief["id"], "shadow-it")
            self.assertTrue(brief["phases"])
            self.assertEqual(brief["start"]["kind"], "website")

            plan_request = urllib.request.Request(
                base + "/api/plan",
                data=json.dumps({"target": str(self.root),
                                 "stage": "permissive-hardware",
                                 "depth": "deep", "network": False}).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(plan_request, timeout=5) as response:
                plan = json.loads(response.read().decode("utf-8"))["plan"]
            self.assertEqual(plan["kind"], "folder")
            self.assertTrue(plan["phases"])
            self.assertGreater(plan["counts"]["probes"], 0)
            self.assertTrue(plan["stop"])
            self.assertIn("Stop conditions", autonomy.plan_text(plan))

            bad_plan_request = urllib.request.Request(
                base + "/api/plan",
                data=json.dumps({"target": str(self.root), "depth": "turbo"}).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as bad_plan:
                urllib.request.urlopen(bad_plan_request, timeout=5)
            self.assertEqual(bad_plan.exception.code, 400)

            payload = json.dumps({
                "target": str(self.root), "authorized": True,
                "scope": "console test", "network": False,
                "stage": "permissive-hardware",
            }).encode("utf-8")
            request = urllib.request.Request(
                base + "/api/investigations", data=payload,
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=5) as response:
                started = json.loads(response.read().decode("utf-8"))
            dossier_id = started["investigation"]["id"]
            self.assertEqual(started["investigation"]["status"], "queued")
            self.assertEqual(started["investigation"]["stage"], "permissive-hardware")
            self.assertEqual(started["investigation"]["depth"], "standard")

            deadline = time.time() + 20
            dossier = {}
            while time.time() < deadline:
                with urllib.request.urlopen(base + "/api/investigations/" + dossier_id,
                                            timeout=5) as response:
                    dossier = json.loads(response.read().decode("utf-8"))
                if dossier.get("status") in {"complete", "failed"}:
                    break
                time.sleep(0.15)
            self.assertEqual(dossier.get("status"), "complete", dossier.get("error"))
            with urllib.request.urlopen(base + "/api/investigations/" + dossier_id + "/report.md",
                                        timeout=5) as response:
                report = response.read().decode("utf-8")
            self.assertIn("REA dossier", report)

            with self.assertRaises(urllib.error.HTTPError) as caught:
                bad = urllib.request.Request(
                    base + "/api/investigations",
                    data=json.dumps({"target": str(self.root)}).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST")
                urllib.request.urlopen(bad, timeout=5)
            self.assertEqual(caught.exception.code, 403)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)


class ImproveExecutorTests(unittest.TestCase):
    """Real git apply, verification and automatic reversal in a temp repo."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old_investigations = engine.INVESTIGATIONS_DIR
        engine.INVESTIGATIONS_DIR = Path(self.tmp.name) / "dossiers"
        self._old_improvements = improvements.STORE_PATH
        improvements.STORE_PATH = Path(self.tmp.name) / "improvements.json"
        self.lab = Path(self.tmp.name) / "repo"
        self.lab.mkdir(parents=True)
        self.app = self.lab / "app.py"
        self.app.write_text("print('hello')\n", encoding="utf-8")
        self.inv = engine.new_investigation(
            str(self.lab), authorized=True, kind="folder", network=False,
            scope="owned lab target")
        self.inv.update({
            "status": "complete",
            "finished": engine.now_iso(),
            "edits": [{"id": "E-01", "category": "source", "locator": "app.py:1",
                       "action": "Add one comment and change nothing else.",
                       "risk": "low", "confidence": 0.86, "requires": [], "note": "",
                       "probe": "code.structure", "evidence": ["F-001"]}],
            "summary": {"verdict": "One mapped source seam.", "confidence": 0.8},
        })
        engine.save(self.inv)
        self._git("init")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test")
        self._git("config", "core.autocrlf", "false")
        self._git("add", "-A")
        self._git("commit", "-m", "baseline")

    def tearDown(self):
        engine.INVESTIGATIONS_DIR = self._old_investigations
        improvements.STORE_PATH = self._old_improvements
        self.tmp.cleanup()

    def _git(self, *args):
        return subprocess.run(["git", "-C", str(self.lab), *args],
                              capture_output=True, text=True, timeout=30)

    def _patch(self):
        self.app.write_text("print('hello')\n# improved\n", encoding="utf-8")
        patch = self._git("diff").stdout
        self._git("checkout", "--", "app.py")
        return patch

    def _baselined_plan(self, verify_command, system_class="software", intervention="debloat"):
        plan = improvements.create(
            self.inv["id"], "E-01",
            goal="Make the entry point explicit.",
            change="Add one comment; change nothing else.",
            baseline="app.py prints hello.",
            verification="The comment exists and the output is unchanged.",
            rollback="Reverse the recorded patch.",
            patch=self._patch(), owner="Test owner",
            system_class=system_class, intervention=intervention,
            verify_command=verify_command)
        improvements.set_status(plan["id"], "approved")
        return improvements.set_status(plan["id"], "baselined")

    def test_apply_verifies_and_rolls_back_a_real_patch(self):
        verify = ("python -c \"import sys; sys.exit(0 if 'improved' in "
                  "open('app.py').read() else 1)\"")
        plan = self._baselined_plan(verify)
        preview = executor.preview(plan)
        self.assertIn("app.py", preview["files"])
        applied = executor.apply(plan["id"])
        self.assertEqual(applied["status"], "verified")
        self.assertIn("# improved", self.app.read_text(encoding="utf-8"))
        self.assertTrue(applied["execution"]["applied"])
        self.assertEqual(applied["execution"]["verify_exit"], 0)
        self.assertEqual([event["status"] for event in applied["history"]][-2:],
                         ["changed", "verified"])
        reverted = executor.rollback(plan["id"])
        self.assertEqual(reverted["status"], "rolled_back")
        self.assertEqual(self.app.read_text(encoding="utf-8"), "print('hello')\n")
        self.assertTrue(reverted["execution"]["reverted"])

    def test_failed_verification_reverses_the_patch_automatically(self):
        plan = self._baselined_plan('python -c "import sys; sys.exit(1)"')
        failed = executor.apply(plan["id"])
        self.assertEqual(failed["status"], "rolled_back")
        self.assertTrue(failed["execution"]["reverted"])
        self.assertEqual(failed["execution"]["verify_exit"], 1)
        self.assertEqual(self.app.read_text(encoding="utf-8"), "print('hello')\n")

    def test_crew_decision_drafts_and_executes_the_guard_patch(self):
        self.app.write_text(
            """def greet():
    print('hello')


greet()
""",
            encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "call")
        result = decision.decide(self.inv["id"], "Add a single entry point and keep the output.",
                                 "software", owner="Test owner")
        plan = result["plan"]
        self.assertIsNotNone(plan)
        self.assertTrue(plan["patch"])
        self.assertIn('if __name__ == "__main__":', plan["patch"])
        self.assertIn("py_compile", plan["verify_command"])
        self.assertEqual(result["suggestion"]["status"], "approved")
        self.assertEqual(plan["decision"]["plan_id"], plan["id"])
        self.assertTrue(result["advice"])
        improvements.set_status(plan["id"], "approved")
        improvements.set_status(plan["id"], "baselined")
        applied = executor.apply(plan["id"])
        self.assertEqual(applied["status"], "verified")
        self.assertIn('if __name__ == "__main__":', self.app.read_text(encoding="utf-8"))
        executor.rollback(plan["id"])
        self.assertNotIn("__main__", self.app.read_text(encoding="utf-8"))

    def test_crew_decision_admits_when_no_safe_patch_fits(self):
        self.app.write_text(
            """value = 1
print(value)
""",
            encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "assignment")
        result = decision.decide(self.inv["id"], "Add a single entry point.", "software",
                                 owner="Test owner")
        self.assertIsNone(result["recipe"])
        self.assertEqual(result["plan"]["patch"], "")
        self.assertIn("no safe automatic recipe", result["advice"])

    def test_executor_refuses_a_draft_and_a_non_local_target(self):
        draft = improvements.create(
            self.inv["id"], "E-01", "Goal.", "Change.", "Baseline.", "Verify.",
            "Rollback.", patch=self._patch(), owner="Owner",
            system_class="software", intervention="debloat")
        with self.assertRaises(executor.ExecutionError):
            executor.apply(draft["id"])
        outside = Path(self.tmp.name) / "not-a-repo"
        outside.mkdir()
        with self.assertRaises(executor.ExecutionError) as caught:
            executor.preview({"target": str(outside), "patch": "x", "system_class": "software"})
        self.assertIn("git work tree", str(caught.exception))
        with self.assertRaises(executor.ExecutionError) as caught:
            executor.preview({"target": "https://example.com", "patch": "x",
                              "system_class": "software"})
        self.assertIn("local path", str(caught.exception))
        with self.assertRaises(executor.ExecutionError) as caught:
            executor.preview({"target": str(self.lab), "patch": "x", "system_class": "alarm"})
        self.assertIn("software or web", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class PublicSiteHygieneTest(unittest.TestCase):
    def test_canonicals_indexes_and_custom_404(self):
        import re
        import tempfile
        from pathlib import Path

        import studio

        with tempfile.TemporaryDirectory() as tmp:
            studio.ensure_site(tmp)
            pages = sorted(Path(tmp).rglob("*.html"))
            self.assertGreater(len(pages), 20)
            not_found = (Path(tmp) / "404.html").read_text("utf-8")
            self.assertIn("not in the room", not_found)
            self.assertIn('name="robots" content="noindex"', not_found)
            titles, descriptions, canonicals = [], [], 0
            for page in pages:
                text = page.read_text("utf-8")
                if page.name == "404.html":
                    continue
                self.assertIn('rel="canonical"', text, page.name)
                self.assertIn('href="https://heliosrea.netlify.app/', text, page.name)
                canonicals += 1
                titles.append(re.search(r"<title>(.*?)</title>", text, re.S).group(1))
                descriptions.append(
                    re.search(r'<meta name="description" content="(.*?)"', text, re.S).group(1)
                )
            self.assertEqual(len(titles), len(set(titles)))
            self.assertEqual(len(descriptions), len(set(descriptions)))
            sitemap = (Path(tmp) / "sitemap.xml").read_text("utf-8")
            self.assertEqual(sitemap.count("<url>"), canonicals)
            self.assertNotIn("404.html", sitemap)
            robots = (Path(tmp) / "robots.txt").read_text("utf-8")
            self.assertIn("Sitemap: https://heliosrea.netlify.app/sitemap.xml", robots)


class ConnectTests(unittest.TestCase):
    def test_plan_and_scan_refusals(self):
        import connect

        plan = connect.plan({
            "device": "home-alarm", "host": "192.168.1.50", "authorized": True,
            "answers": {"brand": "Pyronix Enforcer"},
        })
        self.assertIn("brand", plan["answers"])
        self.assertTrue(plan["ports"])
        self.assertTrue(plan["crew"])
        self.assertTrue(plan["ready"])
        with self.assertRaises(PermissionError):
            connect.scan({"device": "home-alarm", "host": "192.168.1.50", "authorized": False})
        with self.assertRaises(ValueError):
            connect.scan({"device": "home-alarm", "host": "8.8.8.8", "authorized": True})
        with self.assertRaises(ValueError):
            connect.scan({"device": "home-alarm", "host": "example.com", "authorized": True})

    def test_scan_finds_loopback_http_banner(self):
        import http.server
        import threading

        import connect

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"<html><title>Alarm panel</title>ok</html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Server", "TestPanel/1.0")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = connect.scan({
                "device": "home-alarm", "host": "127.0.0.1", "authorized": True,
                "ports": [server.server_port],
            })
            self.assertEqual(result["open_ports"][0]["port"], server.server_port)
            self.assertIn("Alarm panel", result["open_ports"][0]["banner"]["title"])
        finally:
            server.shutdown()
            server.server_close()

    def test_save_list_runbook_and_delete(self):
        import pathlib
        import tempfile

        import connect

        with tempfile.TemporaryDirectory() as tmp:
            store = pathlib.Path(tmp)
            saved = connect.save({
                "device": "home-alarm", "host": "192.168.1.50", "authorized": True,
                "answers": {"brand": "Pyronix"},
                "scan": {"at": "now", "ports_checked": [80],
                         "open_ports": [{"port": 80, "service": "HTTP", "open": True,
                                         "banner": {"title": "Panel"}}],
                         "findings": [], "routes": []},
            }, store=store)
            record_id = saved["connection"]["id"]
            self.assertTrue((store / f"{record_id}.md").exists())
            self.assertIn("Rollback", saved["runbook"])
            self.assertEqual(len(connect.list_connections(store)), 1)
            connect.delete(record_id, store=store)
            self.assertEqual(connect.list_connections(store), [])

    def test_devices_requires_permission(self):
        import connect

        with self.assertRaises(PermissionError):
            connect.devices({"authorized": False})

class CrewMissionTests(unittest.TestCase):
    def test_roster_exposes_delivery_crew_and_investigation_lenses(self):
        import mission

        roster = mission.roster()
        self.assertGreaterEqual(len(roster["personas"]), 12)
        self.assertEqual(len(roster["professions"]), 17)
        self.assertEqual(roster["counts"]["crew"],
                         len(roster["personas"]) + len(roster["professions"]))
        self.assertTrue(roster["principles"])
        self.assertTrue(all(person["name"] for person in roster["personas"]))

    def test_mission_plan_keeps_the_operator_in_control(self):
        import mission

        result = mission.plan("Find the local alarm interface", "wifi",
                              kind="wifi", sweep=False)
        self.assertEqual(result["plan"]["kind"], "wifi")
        self.assertEqual(result["crew"]["lead"]["id"], "mission-controller")
        self.assertTrue(any(lens["id"] == "protocol"
                            for lens in result["crew"]["lenses"]))
        self.assertFalse(result["plan"]["sweep"])
        self.assertTrue(result["run"]["requires_authorisation"])
        self.assertIn("Stop conditions", result["prompt"])
        self.assertIn("Initiator: you", result["prompt"])

    def test_mission_plan_refuses_public_lan_ranges(self):
        import mission

        with self.assertRaises(ValueError):
            mission.plan("scan a public range", "8.8.8.0/24", kind="wifi")
