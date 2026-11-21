#!/usr/bin/env python3
"""Local network and internet-exposure helpers for REA.

Everything here is standard library only. The local reads (interfaces, WiFi
state, neighbour table) are passive. ``ping_sweep`` is the one bounded active
step: the caller only invokes it for a private LAN after the operator has
chosen WiFi & LAN and attested ownership.

No port scanning, no login attempts, no packet capture.
"""

from __future__ import annotations

import ipaddress
import platform
import re
import socket
import ssl
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

MAX_SWEEP_HOSTS = 254
PING_TIMEOUT_MS = 350
PING_WORKERS = 64
TLS_TIMEOUT = 8
_CERTIFI_CA = None
_CERTIFI_CHECKED = False

IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# A small offline OUI hint table. These are presented as hints, never as
# proof of the device model; randomised MACs are labelled instead.
OUI_VENDORS = {
    "3c22fb": "Apple", "f01898": "Apple", "001b63": "Apple", "001ec2": "Apple",
    "002500": "Apple", "8c8590": "Apple", "d0817a": "Apple", "a483e7": "Apple",
    "b827eb": "Raspberry Pi", "dca632": "Raspberry Pi", "e45f01": "Raspberry Pi",
    "001788": "Philips Hue", "ecb5fa": "Philips Hue",
    "18b430": "Nest", "641666": "Nest",
    "001a11": "Google", "f4f5d8": "Google", "30fd38": "Google", "44070b": "Google",
    "0014bf": "Cisco-Linksys", "001d7e": "Cisco-Linksys",
    "0024b2": "Netgear", "a040a0": "Netgear",
    "50c7bf": "TP-Link", "f4f26d": "TP-Link",
    "001e58": "D-Link", "1c7ee5": "D-Link",
    "000c29": "VMware", "080027": "VirtualBox", "525400": "QEMU/KVM",
    "18fe34": "Espressif", "246f28": "Espressif", "84f3eb": "Espressif",
}

ARP_ROW = re.compile(
    r"(?m)^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+"
    r"([0-9A-Fa-f]{2}(?:[-:][0-9A-Fa-f]{2}){5})\s+(\w+)")
ARP_AT = re.compile(
    r"\((\d{1,3}(?:\.\d{1,3}){3})\)\s+at\s+([0-9A-Fa-f:]{17})")
NEIGH_ROW = re.compile(
    r"(?m)^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+dev\s+\S+\s+lladdr\s+"
    r"([0-9A-Fa-f:]{17})\s+(\S+)")


def run_command(args: list[str], timeout: float = 8.0) -> str:
    """Run one read-only command and return text; never raise."""
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, errors="replace",
            timeout=timeout, creationflags=_no_window(),
        )
        return result.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _no_window() -> int:
    # CREATE_NO_WINDOW keeps helper processes from flashing a console on Windows.
    return 0x08000000 if platform.system() == "Windows" else 0


def ssl_context():
    """Default TLS context, plus certifi roots when that bundle is installed.

    Some Windows Python builds ship an empty OpenSSL CA path and miss modern
    intermediate chains. certifi is optional: without it the system store is
    still used and the exposure probe reports what it could not verify.
    """
    global _CERTIFI_CA, _CERTIFI_CHECKED
    context = ssl.create_default_context()
    if not _CERTIFI_CHECKED:
        try:
            import certifi
            _CERTIFI_CA = certifi.where()
        except Exception:  # noqa: BLE001 - certifi is an optional supplement
            _CERTIFI_CA = ""
        _CERTIFI_CHECKED = True
    if _CERTIFI_CA:
        try:
            context.load_verify_locations(cafile=_CERTIFI_CA)
        except (OSError, ssl.SSLError):
            pass
    return context


def _is_mask(ip: str) -> bool:
    try:
        parts = [int(part) for part in ip.split(".")]
    except ValueError:
        return False
    if len(parts) != 4 or any(part < 0 or part > 255 for part in parts):
        return False
    bits = "".join(f"{part:08b}" for part in parts)
    return "01" not in bits  # contiguous ones then zeros (255.255.255.0 etc.)


def _usable(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return (addr.version == 4 and not addr.is_multicast
            and not addr.is_loopback and str(addr) != "0.0.0.0"
            and str(addr) != "255.255.255.255")


def unique(items) -> list:
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def normalize_mac(mac: str) -> str:
    clean = re.sub(r"[^0-9A-Fa-f]", "", str(mac or ""))
    if len(clean) != 12:
        return ""
    return ":".join(clean[i:i + 2] for i in range(0, 12, 2)).upper()


def mac_vendor(mac: str) -> str:
    clean = re.sub(r"[^0-9A-Fa-f]", "", str(mac or ""))
    if len(clean) < 6:
        return ""
    if int(clean[:2], 16) & 0x02:
        return "randomised MAC"
    return OUI_VENDORS.get(clean[:6].lower(), "")


def parse_arp(text: str) -> list[dict]:
    """Parse Windows ``arp -a``, Linux ``ip neigh`` and BSD ``arp -an``."""
    found: dict[str, dict] = {}
    for match in ARP_ROW.finditer(text or ""):
        ip, mac, state = match.group(1), match.group(2), match.group(3)
        _remember(found, ip, mac, state)
    for match in NEIGH_ROW.finditer(text or ""):
        ip, mac, state = match.group(1), match.group(2), match.group(3)
        _remember(found, ip, mac, state)
    for match in ARP_AT.finditer(text or ""):
        ip, mac = match.group(1), match.group(2)
        _remember(found, ip, mac, "")
    return sorted(found.values(), key=lambda item: _ip_key(item["ip"]))


def _remember(found: dict, ip: str, mac: str, state: str) -> None:
    if not _usable(ip):
        return
    normalized = normalize_mac(mac)
    if not normalized or normalized == "FF:FF:FF:FF:FF:FF":
        return
    if normalized in {"00:00:00:00:00:00", "01:00:5E:00:00:01"}:
        return
    entry = {"ip": ip, "mac": normalized, "state": str(state or "seen").lower(),
             "vendor": mac_vendor(normalized)}
    existing = found.get(ip)
    if existing is None or (not existing.get("vendor") and entry["vendor"]):
        found[ip] = entry


def _ip_key(ip: str):
    try:
        return int(ipaddress.ip_address(ip))
    except ValueError:
        return 0


def parse_wifi(text: str) -> dict:
    """Parse ``netsh wlan show interfaces`` output."""
    data: dict[str, str] = {}
    patterns = {
        "state": r"(?m)^\s*State\s*:\s*(.+)$",
        "ssid": r"(?m)^\s*SSID\s*:\s*(.+)$",
        "bssid": r"(?m)^\s*BSSID\s*:\s*(.+)$",
        "signal": r"(?m)^\s*Signal\s*:\s*(.+)$",
        "radio": r"(?m)^\s*Radio type\s*:\s*(.+)$",
        "auth": r"(?m)^\s*Authentication\s*:\s*(.+)$",
        "channel": r"(?m)^\s*Channel\s*:\s*(.+)$",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text or "", re.I)
        if match:
            value = match.group(1).strip()
            if value:
                data[key] = value
    if data.get("bssid"):
        data["bssid"] = normalize_mac(data["bssid"]) or data["bssid"]
    if data.get("ssid", "").lower() in {"<hidden network>", ""}:
        data.pop("ssid", None)
    return data


def parse_ipconfig(text: str) -> dict:
    """Parse Windows ``ipconfig /all`` for addresses, gateway and DNS."""
    local_ips: list[str] = []
    dns: list[str] = []
    gateway = ""
    dns_lines = 0
    gateway_lines = 0
    for line in (text or "").splitlines():
        low = line.lower()
        if "ipv4" in low:
            for ip in IPV4_RE.findall(line):
                if _usable(ip) and not _is_mask(ip) and ip not in local_ips:
                    local_ips.append(ip)
        if ("default gateway" in low or "standardgateway" in low) and not gateway:
            gateway_lines = 2
            for ip in IPV4_RE.findall(line):
                if _usable(ip):
                    gateway = ip
                    break
            if gateway:
                gateway_lines = 0
            continue
        if gateway_lines and line.strip() and ":" not in line:
            for ip in IPV4_RE.findall(line):
                if _usable(ip):
                    gateway = ip
                    break
            gateway_lines -= 1
            if gateway:
                gateway_lines = 0
        elif not line.strip():
            gateway_lines = 0
        if "dns server" in low or "dns-server" in low:
            dns_lines = 2
            for ip in IPV4_RE.findall(line):
                if _usable(ip) and ip not in dns:
                    dns.append(ip)
            continue
        if dns_lines and line.strip() and ":" not in line:
            for ip in IPV4_RE.findall(line):
                if _usable(ip) and ip not in dns:
                    dns.append(ip)
            dns_lines -= 1
        elif not line.strip():
            dns_lines = 0
    if not local_ips:
        for ip in IPV4_RE.findall(text or ""):
            if _usable(ip) and not _is_mask(ip) and ip != gateway and ip not in local_ips:
                local_ips.append(ip)
    return {"local_ips": local_ips[:8], "gateway": gateway, "dns": dns[:6]}


def parse_linux_ip(text: str) -> dict:
    local_ips: list[str] = []
    for match in re.finditer(r"(?m)^\s*inet\s+(\d+\.\d+\.\d+\.\d+)", text or ""):
        ip = match.group(1)
        if _usable(ip) and not _is_mask(ip) and ip not in local_ips:
            local_ips.append(ip)
    gateway = ""
    match = re.search(r"(?m)^\s*default\s+via\s+(\d+\.\d+\.\d+\.\d+)", text or "")
    if match:
        gateway = match.group(1)
    return {"local_ips": local_ips[:8], "gateway": gateway, "dns": []}


def parse_nmcli(text: str) -> dict:
    """Parse active ``nmcli -t ... dev wifi`` output."""
    for line in (text or "").splitlines():
        fields = line.split(":")
        if len(fields) >= 5 and fields[0].strip().lower() in {"yes", "*"}:
            return {
                "state": "connected",
                "ssid": fields[1].replace("\\:", ":").strip(),
                "bssid": normalize_mac(fields[2]) or fields[2],
                "signal": fields[3].strip() + "%" if fields[3].strip().isdigit() else fields[3],
                "channel": fields[4].strip(),
            }
    return {}


def parse_airport(text: str) -> dict:
    data: dict[str, str] = {}
    for key, field in (("ssid", "SSID"), ("bssid", "BSSID"), ("signal", "agrCtlRSSI"),
                       ("channel", "channel")):
        match = re.search(rf"(?m)^\s*{field}\s*:\s*(.+)$", text or "")
        if match:
            data[key] = match.group(1).strip()
    if data.get("bssid"):
        data["bssid"] = normalize_mac(data["bssid"]) or data["bssid"]
    if data.get("state") is None and data.get("ssid"):
        data["state"] = "connected"
    return data


def _socket_local_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("1.1.1.1", 80))
        return sock.getsockname()[0]
    except OSError:
        return ""
    finally:
        sock.close()


def local_network() -> dict:
    """Read this host's interfaces, WiFi state, gateway and DNS."""
    system = platform.system()
    info = {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "system": system,
        "wifi": {},
        "local_ips": [],
        "gateway": "",
        "dns": [],
    }
    if system == "Windows":
        info.update(parse_ipconfig(run_command(["ipconfig", "/all"])))
        info["wifi"] = parse_wifi(run_command(["netsh", "wlan", "show", "interfaces"]))
        if not info.get("gateway"):
            route = run_command(["route", "print", "-4"])
            for line in route.splitlines():
                fields = line.split()
                if (len(fields) >= 3 and fields[0] == "0.0.0.0"
                        and fields[1] == "0.0.0.0" and _usable(fields[2])):
                    info["gateway"] = fields[2]
                    break
    elif system == "Darwin":
        addresses = parse_linux_ip(run_command(["ifconfig"]))
        route = run_command(["netstat", "-rn"])
        match = re.search(r"(?m)^default\s+(\d+\.\d+\.\d+\.\d+)", route)
        info.update(addresses)
        if match:
            info["gateway"] = match.group(1)
        airport = ("/System/Library/PrivateFrameworks/Apple80211.framework/"
                   "Versions/Current/Resources/airport")
        info["wifi"] = parse_airport(run_command([airport, "-I"]))
    else:
        info.update(parse_linux_ip(run_command(["ip", "-4", "addr", "show"])))
        route = run_command(["ip", "route", "show", "default"])
        match = re.search(r"\bvia\s+(\d+\.\d+\.\d+\.\d+)", route)
        if match:
            info["gateway"] = match.group(1)
        info["wifi"] = parse_nmcli(run_command(
            ["nmcli", "-t", "-f", "ACTIVE,SSID,BSSID,SIGNAL,CHAN", "dev", "wifi"]))
        try:
            resolv = open("/etc/resolv.conf", encoding="utf-8", errors="replace").read()
            info["dns"] = [ip for ip in IPV4_RE.findall(resolv) if _usable(ip)][:6]
        except OSError:
            pass
    if not info["local_ips"]:
        fallback = _socket_local_ip()
        if fallback:
            info["local_ips"] = [fallback]
    return info


def collect_arp() -> list[dict]:
    """Read the local neighbour cache (no packets sent)."""
    outputs: list[str] = []
    if platform.system() == "Windows":
        outputs.append(run_command(["arp", "-a"]))
    else:
        outputs.append(run_command(["ip", "neigh"]))
        outputs.append(run_command(["arp", "-an"]))
    merged: dict[str, dict] = {}
    for text in outputs:
        for device in parse_arp(text):
            merged.setdefault(device["ip"], device)
    return sorted(merged.values(), key=lambda item: _ip_key(item["ip"]))


def lan_subnet(target: str, local_ips: list[str], gateway: str = ""):
    """Return a private IPv4 /24 to inspect, or None.

    Public ranges are refused: REA never sweeps the internet. When the gateway
    is known, prefer its subnet so a VPN/virtual adapter is not swept instead.
    """
    raw = str(target or "").strip()
    if gateway:
        try:
            gateway_ip = ipaddress.ip_address(str(gateway).strip())
        except ValueError:
            gateway_ip = None
        if (gateway_ip is not None and gateway_ip.version == 4
                and gateway_ip.is_private and not gateway_ip.is_loopback
                and not gateway_ip.is_link_local):
            return ipaddress.ip_network(f"{gateway_ip}/24", strict=False)
    candidate = None
    if "/" in raw:
        try:
            candidate = ipaddress.ip_network(raw, strict=False)
        except ValueError:
            candidate = None
    elif raw and raw.lower() not in {
            "wifi", "wi-fi", "lan", "network", "local network", "this network"}:
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            address = None
        if address is not None and address.version == 4:
            candidate = ipaddress.ip_network(f"{address}/24", strict=False)
    if candidate is not None:
        if (candidate.version != 4 or not candidate.is_private
                or candidate.is_loopback or candidate.is_link_local):
            return None
        if candidate.num_addresses > 256:
            first = next(candidate.hosts(), candidate.network_address)
            candidate = ipaddress.ip_network(f"{first}/24", strict=False)
        return candidate
    for ip in local_ips or []:
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if (address.version == 4 and address.is_private
                and not address.is_loopback and not address.is_link_local):
            return ipaddress.ip_network(f"{ip}/24", strict=False)
    return None


def _ping(ip: str) -> tuple[str, bool]:
    system = platform.system()
    if system == "Windows":
        args = ["ping", "-n", "1", "-w", str(PING_TIMEOUT_MS), "-4", ip]
    elif system == "Darwin":
        args = ["ping", "-c", "1", "-W", str(PING_TIMEOUT_MS), ip]
    else:
        args = ["ping", "-c", "1", "-W", "1", ip]
    try:
        result = subprocess.run(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=PING_TIMEOUT_MS / 1000 + 1.8, creationflags=_no_window(),
        )
        return ip, result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return ip, False


def ping_sweep(network, timeout_ms: int = PING_TIMEOUT_MS,
               workers: int = PING_WORKERS) -> tuple[list[str], int]:
    """Bounded ICMP sweep of one private /24. No ports are touched."""
    hosts = [str(host) for host in network.hosts()][:MAX_SWEEP_HOSTS]
    if not hosts:
        return [], 0
    with ThreadPoolExecutor(max_workers=min(workers, len(hosts))) as pool:
        results = list(pool.map(_ping, hosts))
    return [ip for ip, alive in results if alive], len(hosts)


def resolve(host: str) -> tuple[list[str], str]:
    """Resolve A/AAAA addresses; return (addresses, error)."""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        return [], str(exc)
    addresses: list[str] = []
    for info in infos:
        address = str(info[4][0]).split("%", 1)[0]
        if address not in addresses:
            addresses.append(address)
    return addresses, ""


def _cert_name(entries) -> str:
    values: dict[str, str] = {}
    for rdn in entries or ():
        for key, value in rdn:
            values.setdefault(key, value)
    parts = [values.get("commonName", ""), values.get("organizationName", "")]
    return " / ".join(part for part in parts if part)


def _cert_result(tls, verified: bool, verification_error: str = "") -> dict:
    cert = tls.getpeercert() or {}
    expires = str(cert.get("notAfter") or "")
    days_left = None
    if expires:
        try:
            days_left = int((ssl.cert_time_to_seconds(expires) - time.time()) // 86400)
        except (TypeError, ValueError):
            days_left = None
    sans = [f"{kind}:{value}" for kind, value in (cert.get("subjectAltName") or ())][:12]
    cipher = tls.cipher() or ("", "", 0)
    return {
        "ok": True,
        "verified": bool(verified),
        "error": verification_error,
        "issuer": _cert_name(cert.get("issuer")),
        "subject": _cert_name(cert.get("subject")),
        "expires": expires,
        "days_left": days_left,
        "tls_version": tls.version() or "",
        "cipher": cipher[0],
        "sans": sans,
    }


def tls_info(host: str, port: int = 443, timeout: float = TLS_TIMEOUT) -> dict:
    """Read the certificate and negotiated TLS parameters on one port."""
    result = {"ok": False, "verified": False, "error": "", "issuer": "",
              "subject": "", "expires": "", "days_left": None,
              "tls_version": "", "cipher": "", "sans": []}
    context = ssl_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                return _cert_result(tls, verified=True)
    except ssl.SSLCertVerificationError as exc:
        verification_error = f"certificate verification failed: {exc}"
        result["error"] = verification_error
    except (OSError, ssl.SSLError) as exc:
        result["error"] = str(exc)
        return result
    # Read metadata anyway so an expired/self-signed certificate is evidence,
    # not a dead end. The verified=False flag makes that explicit.
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                return _cert_result(tls, verified=False,
                                    verification_error=verification_error)
    except (OSError, ssl.SSLError) as exc:
        result["error"] = f"{verification_error}; {exc}"
        return result
