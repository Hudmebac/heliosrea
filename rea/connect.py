#!/usr/bin/env python3
"""Guided connection to one device on the operator's own LAN.

Choose a device, answer the crew's questions, check a small documented port
set on one private host, then save a local profile and runbook. It never
captures credentials, attempts a login, changes device settings or touches a
public address. Standard library only; records live in rea/data/connections.
"""

from __future__ import annotations

import ipaddress
import json
import re
import secrets
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import discovery

STORE = Path(__file__).resolve().parent / "data" / "connections"
MAX_PORTS = 16
MAX_DEVICES = 64
TIMEOUT = 0.45
MAX_BANNER = 8192

DEVICE_TYPES = {
    "home-alarm": {
        "label": "Home alarm",
        "blurb": "Panel, hub or alarm bridge on the local network.",
        "ports": [80, 443, 554, 8000, 8080, 8443, 37777, 34567, 1883, 8883, 5000, 5060],
        "questions": [
            ("brand", "Make and model", "e.g. Pyronix Enforcer, Ring Alarm, Ajax Hub"),
            ("app", "Which app or hub controls it?", "Vendor app, Home Assistant, SmartThings, none"),
            ("goal", "What should REA do with it?", "Read status, receive events, arm/disarm, notify"),
            ("link", "Wired or Wi-Fi?", "Wired to the router, Wi-Fi, or not sure"),
            ("local_api", "Is a local API, RTSP or MQTT enabled?", "Yes, no, or not sure"),
            ("monitoring", "Is it professionally monitored?", "Yes, no, or not sure"),
        ],
    },
    "camera": {
        "label": "Camera or doorbell",
        "blurb": "IP camera, NVR or video doorbell on the LAN.",
        "ports": [80, 443, 554, 8000, 8080, 8443, 8554, 37777, 34567],
        "questions": [
            ("brand", "Make and model", "e.g. Reolink, Hikvision, Tapo"),
            ("app", "Which app or NVR controls it?", "Vendor app, NVR, Home Assistant, none"),
            ("goal", "What should REA do with it?", "Read status, snapshot, events, notify"),
            ("link", "Wired or Wi-Fi?", "Wired, Wi-Fi, or not sure"),
            ("local_api", "Is ONVIF, RTSP or a local API enabled?", "Yes, no, or not sure"),
            ("monitoring", "Is it cloud-only or local-only?", "Cloud, local, or both"),
        ],
    },
    "other": {
        "label": "Other local device",
        "blurb": "Any other owned device with a documented local interface.",
        "ports": [80, 443, 1883, 5000, 8000, 8080, 8443, 8883],
        "questions": [
            ("brand", "Make and model", "Vendor and model"),
            ("app", "Which app or hub controls it?", "Vendor app, hub, none"),
            ("goal", "What should REA do with it?", "Read status, events, notify, integrate"),
            ("link", "Wired or Wi-Fi?", "Wired, Wi-Fi, or not sure"),
            ("local_api", "Is a local API, MQTT or web console enabled?", "Yes, no, or not sure"),
            ("monitoring", "Is it cloud-managed?", "Yes, no, or not sure"),
        ],
    },
}

SERVICE_LABELS = {
    80: "HTTP", 443: "HTTPS", 445: "SMB", 554: "RTSP", 631: "IPP",
    1883: "MQTT", 5000: "HTTP/UPnP", 5001: "HTTPS", 5060: "SIP",
    8000: "HTTP", 8080: "HTTP", 8443: "HTTPS", 8554: "RTSP",
    8883: "MQTT/TLS", 9000: "HTTP", 9100: "RAW print", 34567: "DVR/P2P",
    37777: "DVR/P2P",
}
HTTP_PORTS = {80, 443, 5000, 5001, 8000, 8080, 8443, 9000}
NO_HTTP = {445, 554, 631, 1883, 5060, 8554, 8883, 9100, 34567, 37777}

CREW = (
    ("Systems Architect",
     "Find the control surface before changing anything: one host, one app, one "
     "documented interface. Map status first, actuation second."),
    ("Red Team",
     "Keep the check on the LAN, one host and a small port set. No default passwords, "
     "no login replay, no internet exposure, no firmware changes."),
    ("App Orchestrator",
     "Start from the outcome you want. If the panel has no supported local interface, "
     "the honest route is the vendor app or a hub, not a port trick."),
)

SAFETY = (
    "One private address only; public addresses are refused.",
    "No usernames, passwords, tokens or PINs are collected or stored.",
    "No login attempts, pairing attempts, firmware writes or setting changes.",
    "The HTTP check is a single unauthenticated GET / with a short timeout.",
    "Nothing leaves this machine. Profiles and runbooks stay in rea/data/connections.",
)

FIXTURES = ("example.com", "example.org", "example.net", ".test", ".invalid", "fixture", "demo")


def _now() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def _clean(value: object, limit: int = 240) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _authorised(payload: dict) -> None:
    if payload.get("authorized") is not True:
        raise PermissionError("confirm you own this device or have written permission")


def _private_host(host: object) -> tuple[str, str]:
    name = _clean(host, 253)
    if not name:
        raise ValueError("enter the device IP address or hostname")
    low = name.lower()
    if any(marker in low for marker in FIXTURES):
        raise ValueError("fixture, demo and example hosts are refused")
    try:
        address = ipaddress.ip_address(name)
        if not (address.is_private or address.is_loopback):
            raise ValueError("only private LAN or loopback addresses are allowed")
        return name, str(address)
    except ValueError as exc:
        if "only private" in str(exc):
            raise
    try:
        infos = socket.getaddrinfo(name, None)
    except OSError as exc:
        raise ValueError(f"could not resolve {name}: {exc}") from exc
    addresses = sorted({info[4][0] for info in infos})
    if not addresses:
        raise ValueError(f"could not resolve {name}")
    for text in addresses:
        address = ipaddress.ip_address(text)
        if not (address.is_private or address.is_loopback):
            raise ValueError(f"{name} resolves outside the private LAN ({text}); refused")
    return name, addresses[0]


def _ports(value: object, fallback: list[int]) -> list[int]:
    items = value if isinstance(value, list) else fallback
    ports: list[int] = []
    for item in items:
        try:
            port = int(item)
        except (TypeError, ValueError):
            continue
        if 1 <= port <= 65535 and port not in ports:
            ports.append(port)
    if not ports:
        raise ValueError("no usable ports to check")
    return ports[:MAX_PORTS]


def _device(value: object) -> tuple[str, dict]:
    key = _clean(value, 40) or "other"
    if key not in DEVICE_TYPES:
        raise ValueError("choose a supported device type")
    return key, DEVICE_TYPES[key]


def _answers(device: dict, value: object) -> dict:
    given = value if isinstance(value, dict) else {}
    return {qid: _clean(given.get(qid), 240) for qid, _, _ in device["questions"]}


def _routes(ports: list[int], answers: dict) -> list[dict]:
    routes: list[dict] = []
    if any(port in ports for port in (80, 443, 8000, 8080, 8443, 5000, 5001)):
        routes.append({"route": "local-web", "label": "Local web interface",
                       "why": "A web port answered. Read the title and vendor docs, then create a read-only local account.",
                       "risk": "low", "requires": "documented local API and a read-only account"})
    if 554 in ports or 8554 in ports:
        routes.append({"route": "rtsp", "label": "RTSP video stream",
                       "why": "RTSP is video only. Use ONVIF or the vendor app for status and events.",
                       "risk": "low", "requires": "stream path and read-only credentials"})
    if 1883 in ports or 8883 in ports:
        routes.append({"route": "mqtt", "label": "MQTT event bus",
                       "why": "A broker answered. Subscribe read-only to a documented topic; do not publish commands yet.",
                       "risk": "medium", "requires": "broker host, topic and a read-only account"})
    if any(port in ports for port in (37777, 34567)):
        routes.append({"route": "vendor-p2p", "label": "Vendor DVR/P2P port",
                       "why": "This looks like a vendor DVR/P2P service. Do not attempt a login; use the vendor app, ONVIF or the NVR.",
                       "risk": "high", "requires": "vendor documentation"})
    app = answers.get("app", "").lower()
    if "home assistant" in app or "hub" in app:
        routes.append({"route": "hub", "label": "Existing hub",
                       "why": "The hub already owns the device session. Point REA at the hub's documented API instead of the panel.",
                       "risk": "low", "requires": "hub API token, stored outside this profile"})
    routes.append({"route": "vendor-app", "label": "Vendor app or cloud",
                   "why": "Always available. If no supported local interface exists, use the vendor app and keep the panel off the public internet.",
                   "risk": "low", "requires": "vendor account"})
    if answers.get("monitoring", "").lower().startswith("yes"):
        routes.insert(0, {"route": "monitoring", "label": "Monitoring path stays untouched",
                          "why": "The alarm is professionally monitored. Read status through a supported interface; never interfere with the signalling path.",
                          "risk": "high", "requires": "installer agreement"})
    return routes


def catalogue() -> dict:
    return {
        "devices": [{"id": key, "label": value["label"], "blurb": value["blurb"],
                     "ports": list(value["ports"]),
                     "questions": [{"id": qid, "label": label, "placeholder": placeholder}
                                   for qid, label, placeholder in value["questions"]]}
                    for key, value in DEVICE_TYPES.items()],
        "crew": [{"role": role, "advice": advice} for role, advice in CREW],
        "safety": list(SAFETY),
    }


def plan(payload: dict) -> dict:
    device_key, device = _device(payload.get("device"))
    answers = _answers(device, payload.get("answers"))
    host = _clean(payload.get("host"), 253)
    authorised = payload.get("authorized") is True
    missing = [{"id": qid, "label": label} for qid, label, _ in device["questions"]
               if not answers.get(qid)]
    return {
        "device": device_key, "label": device["label"], "host": host,
        "authorised": authorised, "answers": answers, "missing": missing,
        "questions": [{"id": qid, "label": label, "placeholder": placeholder}
                      for qid, label, placeholder in device["questions"]],
        "ports": list(device["ports"]), "routes": _routes(device["ports"], answers),
        "crew": [{"role": role, "advice": advice} for role, advice in CREW],
        "safety": list(SAFETY),
        "steps": [
            "Confirm the device is yours and note its IP in the router's client list.",
            "Answer the questions; do not paste passwords or PINs.",
            "Run the safe check against that one address.",
            "Read the findings, then save the profile and runbook.",
            "If a local interface exists, add a read-only account and document the endpoint.",
        ],
        "ready": bool(host and authorised),
        "note": "No probe has run. The plan is advice; the safe check is a separate click.",
    }


def _http_banner(target: str, port: int) -> dict:
    result = {"status": "", "server": "", "title": "", "auth": ""}
    try:
        with socket.create_connection((target, port), timeout=TIMEOUT) as sock:
            sock.settimeout(TIMEOUT)
            sock.sendall((f"GET / HTTP/1.0\r\nHost: {target}\r\n"
                          "User-Agent: REA-connect/1.0\r\nConnection: close\r\n\r\n").encode("ascii", "replace"))
            raw = b""
            while len(raw) < MAX_BANNER:
                chunk = sock.recv(2048)
                if not chunk:
                    break
                raw += chunk
    except OSError:
        return result
    text = raw.decode("utf-8", "replace")
    lines = text.splitlines()
    if lines:
        result["status"] = _clean(lines[0], 120)
    for line in lines[1:40]:
        if ":" not in line:
            break
        name, value = line.split(":", 1)
        if name.strip().lower() == "server":
            result["server"] = _clean(value, 160)
        if name.strip().lower() == "www-authenticate":
            result["auth"] = _clean(value, 80)
    match = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    if match:
        result["title"] = _clean(re.sub(r"<[^>]+>", " ", match.group(1)), 160)
    return result


def _probe(target: str, port: int) -> dict:
    started = time.monotonic()
    try:
        with socket.create_connection((target, port), timeout=TIMEOUT):
            pass
    except OSError:
        return {"port": port, "open": False}
    item = {"port": port, "open": True, "service": SERVICE_LABELS.get(port, "unknown"),
            "elapsed_ms": int((time.monotonic() - started) * 1000)}
    if port not in NO_HTTP:
        item["banner"] = _http_banner(target, port)
    return item


def scan(payload: dict) -> dict:
    _authorised(payload)
    device_key, device = _device(payload.get("device"))
    answers = _answers(device, payload.get("answers"))
    host, target = _private_host(payload.get("host"))
    ports = _ports(payload.get("ports"), device["ports"])
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=min(8, len(ports))) as pool:
        results = list(pool.map(lambda port: _probe(target, port), ports))
    open_ports = [item for item in results if item.get("open")]
    findings = []
    for item in open_ports:
        banner = item.get("banner") or {}
        detail = item["service"]
        if banner.get("title"):
            detail += f" · title: {banner['title']}"
        if banner.get("server"):
            detail += f" · server: {banner['server']}"
        findings.append({"port": item["port"], "status": "signal", "summary": detail,
                         "confidence": 0.75,
                         "limit": "An open port is not proof of a supported API or of authorisation."})
    if not findings:
        findings.append({"port": 0, "status": "empty",
                         "summary": "No response on the checked ports.", "confidence": 0.6,
                         "limit": "A closed port does not prove there is no interface; it may use another port or the vendor cloud."})
    return {
        "at": _now(), "device": device_key, "label": device["label"], "host": host,
        "target": target, "ports_checked": ports, "open_ports": open_ports,
        "findings": findings, "routes": _routes([item["port"] for item in open_ports], answers),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "note": "One unauthenticated check on one private host. Nothing was changed.",
    }


def devices(payload: dict) -> dict:
    _authorised(payload)
    info = discovery.local_network()
    local_ips = [str(item) for item in info.get("local_ips") or []]
    gateway = str(info.get("gateway") or "")
    subnet = discovery.lan_subnet("", local_ips, gateway)
    before = discovery.collect_arp()
    responded: list[str] = []
    attempted = 0
    if payload.get("sweep") is not False and subnet is not None:
        responded, attempted = discovery.ping_sweep(subnet)
    after = discovery.collect_arp()
    merged = {item["ip"]: item for item in before + after}
    for ip in responded:
        merged.setdefault(ip, {"ip": ip, "mac": "", "state": "answered ICMP", "vendor": ""})
    own = set(local_ips)
    found = [{"ip": ip, "mac": str(item.get("mac") or ""), "vendor": str(item.get("vendor") or ""),
              "state": str(item.get("state") or "seen"), "gateway": ip == gateway}
             for ip, item in merged.items() if ip not in own]
    try:
        found.sort(key=lambda item: tuple(int(part) for part in item["ip"].split(".")))
    except ValueError:
        pass
    return {"subnet": str(subnet) if subnet is not None else "", "attempted": attempted,
            "devices": found[:MAX_DEVICES],
            "note": "Neighbour cache plus one bounded ICMP sweep. No ports were checked."}


def _store(store: Path | None = None) -> Path:
    return Path(store) if store else STORE


def _record_path(record_id: str, store: Path | None = None) -> Path:
    safe = re.sub(r"[^A-Za-z0-9\-]", "", str(record_id or ""))
    if not safe:
        raise ValueError("connection id is required")
    return _store(store) / f"{safe}.json"


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save(payload: dict, store: Path | None = None) -> dict:
    _authorised(payload)
    device_key, device = _device(payload.get("device"))
    host, target = _private_host(payload.get("host"))
    answers = _answers(device, payload.get("answers"))
    scan_result = payload.get("scan") if isinstance(payload.get("scan"), dict) else {}
    record_id = f"CON-{datetime.now(tz=timezone.utc).strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"
    now = _now()
    record = {
        "id": record_id, "created_at": now, "updated_at": now,
        "device": device_key, "label": device["label"], "host": host, "target": target,
        "answers": answers,
        "plan": plan({"device": device_key, "host": host, "answers": answers, "authorized": True}),
        "scan": {key: scan_result.get(key, []) for key in ("ports_checked", "open_ports", "findings", "routes")},
    }
    record["scan"]["at"] = _clean(scan_result.get("at"), 40)
    path = _store(store) / f"{record_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(path)
    markdown = runbook(record)
    (path.with_suffix(".md")).write_text(markdown, "utf-8")
    return {"connection": record, "runbook": markdown,
            "runbook_url": f"/api/connect/{record_id}/runbook.md"}


def list_connections(store: Path | None = None) -> list[dict]:
    items = []
    for path in _store(store).glob("CON-*.json"):
        record = _read(path)
        if record.get("id"):
            items.append(record)
    items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return items[:100]


def get(record_id: object, store: Path | None = None) -> dict | None:
    record = _read(_record_path(str(record_id or ""), store))
    return record if record.get("id") else None


def delete(record_id: object, store: Path | None = None) -> dict:
    path = _record_path(str(record_id or ""), store)
    if not path.exists():
        raise ValueError("connection not found")
    path.unlink()
    md = path.with_suffix(".md")
    if md.exists():
        md.unlink()
    return {"deleted": path.stem}


def runbook(record: dict) -> str:
    scan_result = record.get("scan") or {}
    lines = [
        f"# Connect {record.get('label')} at {record.get('host')}", "",
        f"- Record: `{record.get('id')}`", f"- Created: {record.get('created_at')}",
        f"- Device type: {record.get('label')}",
        f"- Last check: {scan_result.get('at') or 'not run'}", "",
        "## Scope and safety", "",
        "One private host, a small documented port set, one unauthenticated HTTP GET, no login attempts, no setting changes.", "",
        "## Answers", "",
    ]
    for key, value in (record.get("answers") or {}).items():
        lines.append(f"- **{key}:** {value or 'not answered'}")
    lines += ["", "## What answered", ""]
    open_ports = scan_result.get("open_ports") or []
    if open_ports:
        for item in open_ports:
            banner = item.get("banner") or {}
            detail = f"- Port {item.get('port')} · {item.get('service')}"
            for key in ("status", "server", "title"):
                if banner.get(key):
                    detail += f" · {banner[key]}"
            lines.append(detail)
    else:
        lines.append("- No checked port answered. Check the router client list and the vendor app.")
    lines += ["", "## Findings", ""]
    for finding in scan_result.get("findings") or []:
        lines.append(f"- [{finding.get('status')}] {finding.get('summary')}")
    lines += ["", "## Suggested routes", ""]
    for route in scan_result.get("routes") or (record.get("plan") or {}).get("routes") or []:
        lines.append(f"- **{route.get('label')}** ({route.get('risk')}): {route.get('why')}")
    lines += ["", "## Change to make", "",
              "1. Reserve the device's IP in the router so the profile keeps working.",
              "2. Create a read-only local account if the device supports one.",
              "3. Record the documented endpoint and protocol; do not store the password.",
              "4. Re-run the safe check and confirm the same ports and banner.", "",
              "## Rollback", "",
              f"Delete this record and runbook: `python -c \"import sys; sys.path.insert(0, 'rea'); import connect; print(connect.delete('{record.get('id')}'))\"`",
              "Remove the router reservation and the read-only account if you created them.", "",
              "## Limits", "",
              "An open port is not proof of a supported API. A closed port does not prove there is no interface. Nothing here changes the device."]
    return "\n".join(lines) + "\n"
