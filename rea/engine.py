#!/usr/bin/env python3
"""REA — Reverse Engineer Anything.

A local-first investigation engine. Point it at a GitHub repository, a website,
a local folder/binary, a captured HAR, this WiFi/LAN or a public host and it runs
a ladder of probes, records evidence, connects what it learned, and reports where
behaviour can be changed.

Rules baked into the engine:

- Only targets the operator owns or is authorised to analyse.
- Passive observation by default: local interface reads, one HTTP GET/HEAD per path.
- LAN device discovery is an explicit, bounded ICMP sweep of one private /24. It
  never scans ports. Internet checks are DNS, one TLS handshake and HTTP on 80/443.
- No auth bypass, no brute force, no exploitation.
- A probe that finds nothing is a route, not a verdict. The ladder retries a
  different technique, records why, and names the next move.
- Secrets are masked before anything is written to disk.

Stdlib only, so it runs beside Helios with no new dependencies.
"""

from __future__ import annotations

import gzip
import hashlib
import ipaddress
import json
import os
import re
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path

import discovery

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
INVESTIGATIONS_DIR = DATA_DIR / "investigations"

MAX_FILES = 3000
MAX_READ = 512_000
MAX_HTTP_BODY = 1_500_000
HTTP_TIMEOUT = 8
MAX_REQUESTS = 22
MAX_PROBES = 26
MAX_RETRY_DEPTH = 2
HAR_MAX_BYTES = 8_000_000
HAR_MAX_ENTRIES = 4000
AUTH_HEADERS = {"authorization", "cookie", "proxy-authorization", "x-api-key",
                "x-auth-token", "x-csrf-token"}
CREDENTIAL_PARAMS = {"token", "access_token", "id_token", "refresh_token", "api_key",
                     "apikey", "key", "secret", "password", "auth", "session",
                     "sig", "signature"}
USER_AGENT = "REA/1.0 (authorised passive analysis)"

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "env", ".env", "dist", "build", "out", "target", "bin", "obj", ".next",
    ".nuxt", ".cache", "coverage", ".idea", ".vscode", "vendor", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "site-packages",
}

SOURCE_EXT = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".java", ".kt",
    ".kts", ".cs", ".go", ".rb", ".php", ".rs", ".swift", ".m", ".mm", ".c",
    ".cc", ".cpp", ".h", ".hpp", ".lua", ".pl", ".sh", ".ps1", ".sql", ".html",
    ".htm", ".vue", ".svelte", ".dart", ".scala", ".clj", ".ex", ".exs",
}

CONFIG_EXT = {
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
    ".xml", ".plist", ".gradle", ".properties",
}

BINARY_EXT = {
    ".exe", ".dll", ".so", ".dylib", ".bin", ".img", ".apk", ".ipa", ".deb",
    ".rpm", ".msi", ".elf", ".ko", ".sys", ".jar", ".wasm", ".zip", ".7z",
    ".rar", ".gz", ".tgz", ".tar", ".xz", ".bz2", ".iso", ".fw", ".hex",
    ".uf2", ".cab", ".app", ".appimage", ".rom",
}

PERMISSIVE = {
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC",
    "Unlicense", "0BSD", "CC0-1.0", "Zlib",
}
COPYLEFT = {"GPL-2.0", "GPL-3.0", "AGPL-3.0", "LGPL-2.1", "LGPL-3.0", "MPL-2.0"}

SECRET_PATTERNS = (
    re.compile(r"\b(sk-[A-Za-z0-9_\-]{8,})"),
    re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{10,})"),
    re.compile(r"\b(AKIA[0-9A-Z]{12,})"),
    re.compile(r"\b(xox[baprs]-[A-Za-z0-9\-]{8,})"),
    re.compile(r"(?i)((?:api[_-]?key|secret|token|password|passwd)\s*[=:]\s*['\"]?)([^\s'\",]{6,})"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
)


# --------------------------------------------------------------- utilities --

def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def redact(text: object) -> str:
    """Mask credential-shaped values before evidence is stored or shown."""
    value = str(text if text is not None else "")
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 2:
            value = pattern.sub(lambda m: m.group(1) + "***", value)
        elif pattern.groups == 1:
            value = pattern.sub("***", value)
        else:
            value = pattern.sub("***", value)
    return value


def read_text(path: Path, limit: int = MAX_READ) -> str:
    try:
        with path.open("rb") as handle:
            raw = handle.read(limit)
        if b"\x00" in raw[:2048]:
            return ""
        return raw.decode("utf-8", "replace")
    except OSError:
        return ""


def human_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    total = float(len(data))
    result = 0.0
    for count in counts.values():
        p = count / total
        result -= p * _log2(p)
    return result


def _log2(value: float) -> float:
    import math
    return math.log2(value) if value > 0 else 0.0


def line_of(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def safe_json(text: str) -> dict:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


# ------------------------------------------------------------------- http --

class BudgetExceeded(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


class Http:
    """Small same-host, request-budgeted fetcher. Passive GET/HEAD only."""

    def __init__(self, authorized: bool, budget: int = MAX_REQUESTS):
        self.authorized = authorized
        self.budget = budget
        self.host = None
        self.requests = 0
        self.opener = urllib.request.build_opener(
            _NoRedirect,
            urllib.request.HTTPSHandler(context=discovery.ssl_context()),
        )

    def get(self, url: str, method: str = "GET", limit: int = MAX_HTTP_BODY) -> dict:
        if not self.authorized:
            raise PermissionError("network probing requires an authorised target")
        for _ in range(4):
            parts = urllib.parse.urlsplit(url)
            if parts.scheme not in {"http", "https"}:
                return {"ok": False, "status": 0, "url": url, "error": "only http(s) is supported"}
            host = parts.netloc.lower()
            if self.host is None:
                self.host = host
            elif host != self.host:
                return {"ok": False, "status": 0, "url": url,
                        "error": "off-host request refused"}
            if self.requests >= self.budget:
                raise BudgetExceeded(f"request budget of {self.budget} exhausted")
            self.requests += 1
            request = urllib.request.Request(url, method=method, headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/json,text/plain,*/*",
                "Accept-Language": "en-GB,en;q=0.8",
            })
            try:
                with self.opener.open(request, timeout=HTTP_TIMEOUT) as response:
                    raw = response.read(limit + 1)[:limit]
                    headers = {k.lower(): v for k, v in response.headers.items()}
                    if headers.get("content-encoding", "").lower() == "gzip":
                        try:
                            raw = gzip.decompress(raw)
                        except OSError:
                            pass
                    return {
                        "ok": True, "status": response.status, "url": response.geturl(),
                        "headers": headers, "raw": raw,
                        "body": raw.decode(self._charset(headers), "replace"),
                        "error": "",
                    }
            except urllib.error.HTTPError as exc:
                location = exc.headers.get("Location") if exc.headers else None
                if exc.code in (301, 302, 303, 307, 308) and location:
                    if method != "GET":
                        method = "GET"
                    url = urllib.parse.urljoin(url, location)
                    continue
                body = b""
                try:
                    body = exc.read(limit)
                except OSError:
                    pass
                return {
                    "ok": False, "status": exc.code, "url": url,
                    "headers": {k.lower(): v for k, v in (exc.headers or {}).items()},
                    "body": body.decode("utf-8", "replace"), "raw": body,
                    "error": f"HTTP {exc.code}",
                }
            except Exception as exc:  # noqa: BLE001 - the target may simply refuse us
                return {"ok": False, "status": 0, "url": url, "headers": {},
                        "body": "", "raw": b"", "error": str(exc)}
        return {"ok": False, "status": 0, "url": url, "error": "too many redirects"}

    @staticmethod
    def _charset(headers: dict) -> str:
        match = re.search(r"charset=([\w\-]+)", headers.get("content-type", ""), re.I)
        return match.group(1) if match else "utf-8"


# -------------------------------------------------------------- context ----

class Ctx:
    def __init__(self, inv: dict, network: bool = True):
        self.inv = inv
        self.target = str(inv.get("target") or "")
        self.kind = str(inv.get("kind") or "")
        self.local_root = Path(inv["local_path"]) if inv.get("local_path") else None
        self.repo = inv.get("repo") or ""
        self.url = inv.get("url") or ""
        self.network = network
        self.http = Http(authorized=bool(inv.get("authorized")), budget=MAX_REQUESTS)
        self.findings: list[dict] = []
        self.edits: list[dict] = []
        self.sources: list[dict] = []
        self.facts: dict = {"tech": set(), "endpoints": set(), "packages": [], "languages": set()}
        self.probe = ""
        self.web: dict | None = None
        self.blobs: dict[str, str] = {}
        self.repo_files: list[str] = []
        self._inventory: dict | None = None

    # -- evidence ------------------------------------------------------------
    def add(self, kind: str, title: str, detail: str = "", *, path: str = "",
            line: int | None = None, quote: str = "", confidence: float = 0.6,
            tags: tuple = (), edit: dict | None = None) -> dict:
        finding = {
            "id": f"F-{len(self.findings) + 1:03d}",
            "probe": self.probe,
            "family": PROBES[self.probe]["family"] if self.probe in PROBES else "General",
            "kind": kind,
            "title": redact(title)[:240],
            "detail": redact(detail)[:1600],
            "path": redact(path)[:300],
            "line": line,
            "quote": redact(quote)[:400],
            "confidence": round(max(0.05, min(0.99, float(confidence))), 2),
            "tags": list(tags)[:8],
        }
        self.findings.append(finding)
        if edit:
            self.edit(**{**edit, "evidence": finding["id"]})
        return finding

    def edit(self, category: str, locator: str, action: str, risk: str = "medium",
             confidence: float = 0.6, requires: tuple = (), note: str = "",
             evidence: str = "") -> dict | None:
        category = str(category or "source").lower()
        locator = redact(locator)[:300]
        action = redact(action)[:600]
        for existing in self.edits:
            if (existing["category"], existing["locator"], existing["action"]) == (
                    category, locator, action):
                if evidence and evidence not in existing["evidence"]:
                    existing["evidence"].append(evidence)
                return existing
        item = {
            "id": f"E-{len(self.edits) + 1:02d}",
            "category": category,
            "locator": locator,
            "action": action,
            "risk": risk if risk in {"low", "medium", "high", "blocked"} else "medium",
            "confidence": round(max(0.05, min(0.99, float(confidence))), 2),
            "requires": list(requires)[:4],
            "note": redact(note)[:400],
            "probe": self.probe,
            "evidence": [evidence] if evidence else [],
        }
        self.edits.append(item)
        return item

    def source(self, label: str, locator: str, note: str = "") -> None:
        entry = {"label": redact(label)[:160], "locator": redact(locator)[:400],
                 "note": redact(note)[:300], "probe": self.probe}
        if entry not in self.sources:
            self.sources.append(entry)

    def learn(self, *items) -> None:
        for item in items:
            if item:
                self.facts["tech"].add(str(item))

    # -- local inventory -----------------------------------------------------
    def inventory(self) -> dict:
        if self._inventory is not None:
            return self._inventory
        files: list[Path] = []
        total = 0
        if self.local_root and self.local_root.is_file():
            files = [self.local_root]
            try:
                total = self.local_root.stat().st_size
            except OSError:
                total = 0
        elif self.local_root and self.local_root.is_dir():
            for dirpath, dirnames, filenames in os.walk(self.local_root):
                dirnames[:] = [d for d in dirnames
                               if d.lower() not in SKIP_DIRS and not d.startswith(".git")]
                for name in filenames:
                    path = Path(dirpath) / name
                    try:
                        total += path.stat().st_size
                    except OSError:
                        continue
                    files.append(path)
                    if len(files) >= MAX_FILES:
                        break
                if len(files) >= MAX_FILES:
                    break
        self._inventory = {
            "files": files,
            "bytes": total,
            "exts": Counter(path.suffix.lower() for path in files),
            "binaries": [path for path in files if path.suffix.lower() in BINARY_EXT],
        }
        return self._inventory

    def texts(self, limit: int = 120):
        if self.blobs:
            for path, text in list(self.blobs.items())[:limit]:
                if text:
                    yield path, text
            return
        if not self.local_root or not self.local_root.is_dir():
            return
        count = 0
        for path in self.inventory()["files"]:
            if path.suffix.lower() not in SOURCE_EXT:
                continue
            text = read_text(path)
            if not text:
                continue
            try:
                label = str(path.relative_to(self.local_root))
            except ValueError:
                label = path.name
            yield label, text
            count += 1
            if count >= limit:
                return


# --------------------------------------------------------------- targets ----

WIFI_TARGETS = {"wifi", "wi-fi", "lan", "network", "local network", "this network"}
INTERNET_TARGETS = {"internet", "my internet", "public ip", "my ip", "egress", "connection"}


def _egress_target(target: str) -> bool:
    return str(target or "").strip().lower() in INTERNET_TARGETS


def classify(target: str) -> dict:
    raw = str(target or "").strip()
    if not raw:
        raise ValueError("target is required")
    lowered = raw.lower()
    if lowered in WIFI_TARGETS:
        return {"kind": "wifi", "url": ""}
    if lowered in INTERNET_TARGETS:
        return {"kind": "internet", "url": ""}
    if re.match(r"^https?://", raw, re.I):
        parts = urllib.parse.urlsplit(raw)
        host = parts.netloc.lower()
        segments = [s for s in parts.path.split("/") if s]
        if host in {"github.com", "www.github.com"} and len(segments) >= 2:
            repo = f"{segments[0]}/{segments[1].removesuffix('.git')}"
            return {"kind": "repo", "repo": repo, "url": f"https://github.com/{repo}"}
        return {"kind": "website", "url": raw}
    if "/" in raw:
        try:
            network = ipaddress.ip_network(raw, strict=False)
        except ValueError:
            network = None
        if network is not None:
            if network.version != 4 or not network.is_private:
                raise ValueError("WiFi & LAN only inspects private IPv4 ranges; "
                                 "public ranges are refused.")
            return {"kind": "wifi", "url": ""}
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        address = None
    if address is not None:
        return {"kind": "internet", "url": f"https://{raw}/"}
    path = Path(raw).expanduser()
    if path.exists():
        path = path.resolve()
        if path.is_dir():
            return {"kind": "folder", "local_path": str(path), "url": ""}
        if path.suffix.lower() == ".har":
            return {"kind": "har", "local_path": str(path), "url": ""}
        return {"kind": "file", "local_path": str(path), "url": ""}
    if re.fullmatch(r"[\w.\-]+/[\w.\-]+", raw):
        return {"kind": "repo", "repo": raw.removesuffix(".git"),
                "url": f"https://github.com/{raw.removesuffix('.git')}"}
    raise ValueError(f"target not found: {raw}")


def _network_classification(target: str, kind: str) -> dict:
    """Validate an explicitly chosen WiFi/LAN or internet target."""
    raw = str(target or "").strip()
    if kind == "wifi":
        if not raw or raw.lower() in WIFI_TARGETS:
            return {"kind": "wifi", "url": ""}
        try:
            network = ipaddress.ip_network(raw, strict=False)
        except ValueError as exc:
            raise ValueError("WiFi & LAN expects 'wifi' or a private IPv4 subnet.") from exc
        if network.version != 4 or not network.is_private:
            raise ValueError("WiFi & LAN only inspects private IPv4 ranges; "
                             "public ranges are refused.")
        return {"kind": "wifi", "url": ""}
    if not raw or raw.lower() in INTERNET_TARGETS:
        return {"kind": "internet", "url": ""}
    parts = urllib.parse.urlsplit(raw if "://" in raw else f"//{raw}")
    host = (parts.hostname or "").strip()
    if not host:
        raise ValueError("Internet exposure expects a domain, URL, IP or 'internet'.")
    return {"kind": "internet",
            "url": raw if "://" in raw else f"https://{host}/"}


# ---------------------------------------------------------------- probes ----

ROUTE_PATTERNS = (
    re.compile(r"""\.(get|post|put|patch|delete|use)\(\s*['"`](/[^'"`]+)['"`]""", re.I),
    re.compile(r"""@\w+\.(get|post|put|delete|route)\(\s*['"]([^'"]+)""", re.I),
    re.compile(r"""@(?:Get|Post|Put|Delete|Request)Mapping\(\s*(?:value\s*=\s*)?["']([^"']+)"""),
    re.compile(r"""HandleFunc\(\s*"([^"]+)"""),
    re.compile(r"""Route::(get|post|put|delete)\(\s*['"]([^'"]+)""", re.I),
    re.compile(r"""(?:path|url)\(\s*['"](/[^'"]+)"""),
)
METHODS = {"get", "post", "put", "patch", "delete", "use", "route"}
URL_RE = re.compile(r"""https?://[^\s'"`<>)\]]+""")
WS_RE = re.compile(r"""wss?://[^\s'"`<>)\]]+""")
FETCH_RE = re.compile(
    r"""(?:fetch|axios(?:\.\w+)?|requests\.(?:get|post|put|delete)|urlopen|http\.(?:get|post))"""
    r"""\(\s*['"`]([^'"`]+)""", re.I)
ENV_RE = re.compile(
    r"""(?:process\.env\.([A-Z][A-Z0-9_]{2,})|os\.environ(?:\.get)?\(\s*['"]([A-Z][A-Z0-9_]{2,})['"]"""
    r"""|ENV\[['"]([A-Z][A-Z0-9_]{2,})['"]\])""")
TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b[:\s]*(.{0,90})")
GITHUB_RE = re.compile(r"github\.com[:/]([\w.\-]+)/([\w.\-]+)", re.I)

TECH_SIGNATURES = {
    "Next.js": ("__NEXT_DATA__", "/_next/"),
    "Nuxt": ("__NUXT__", "/_nuxt/"),
    "React": ("react-dom", "data-reactroot", "react.production"),
    "Vue": ("vue.js", "data-v-", "__vue__"),
    "Angular": ("ng-version", "angular.min.js", "ng-app"),
    "Svelte": ("svelte", "__svelte"),
    "WordPress": ("wp-content", "wp-includes"),
    "Shopify": ("cdn.shopify.com", "shopify.theme"),
    "Squarespace": ("static1.squarespace.com",),
    "Wix": ("wixstatic.com", "wix.com"),
    "Webflow": ("webflow.js", "webflow.com"),
    "Cloudflare": ("cf-ray", "__cf_bm"),
    "Firebase": ("firebaseapp.com", "firebase.js"),
    "Sentry": ("sentry.io", "sentry-cdn"),
    "Stripe": ("js.stripe.com",),
    "Auth0": ("auth0.com", "auth0-spa-js"),
    "Okta": ("okta.com",),
    "Google Analytics": ("googletagmanager.com", "gtag("),
    "Electron": ("electron", "nodeIntegration"),
}


def extract_routes(text: str) -> list[tuple[str, str]]:
    routes: list[tuple[str, str]] = []
    for pattern in ROUTE_PATTERNS:
        for match in pattern.finditer(text):
            groups = match.groups()
            path = groups[-1]
            method = "ANY"
            if len(groups) > 1 and str(groups[0]).lower() in METHODS:
                method = str(groups[0]).upper()
            if method == "USE":
                continue
            item = (method, path)
            if item not in routes:
                routes.append(item)
    return routes


def scan_source(text: str) -> dict:
    imports = len(re.findall(r"^\s*(?:import|from|require\(|using\s+|use\s+)", text, re.M))
    return {
        "routes": extract_routes(text),
        "urls": sorted(set(URL_RE.findall(text))),
        "ws": sorted(set(WS_RE.findall(text))),
        "fetches": sorted(set(FETCH_RE.findall(text))),
        "env": sorted({g for groups in ENV_RE.findall(text) for g in groups if g}),
        "todos": [(m.group(1), m.group(2).strip(), line_of(text, m.start()))
                  for m in TODO_RE.finditer(text)][:8],
        "imports": imports,
        "graphql": bool(re.search(r"/graphql\b|\bquery\s+[A-Za-z_]\w*\s*[({]", text)),
    }


def p_surface_scan(ctx: Ctx) -> dict:
    inv = ctx.inventory()
    files = inv["files"]
    if not files:
        return {"status": "empty", "summary": "Nothing readable at this path."}
    exts = inv["exts"]
    languages = [ext for ext, _ in exts.most_common(8) if ext]
    bins = inv["binaries"]
    dirs = Counter()
    if ctx.local_root and ctx.local_root.is_dir():
        for path in files:
            try:
                rel = path.relative_to(ctx.local_root)
            except ValueError:
                continue
            if len(rel.parts) > 1:
                dirs[rel.parts[0]] += 1
    ctx.facts["languages"] = {e.lstrip(".") for e in languages}
    ctx.facts["source_files"] = sum(1 for p in files if p.suffix.lower() in SOURCE_EXT)
    top_dirs = ", ".join(f"{name} ({count})" for name, count in dirs.most_common(6)) or "flat tree"
    ctx.add(
        "inventory", f"{len(files)} files · {human_size(inv['bytes'])}",
        f"Languages by extension: {', '.join(languages) or 'none obvious'}. "
        f"Top folders: {top_dirs}. Binaries/containers: {len(bins)}.",
        confidence=0.95, tags=("inventory",),
    )
    entries = [p.name for p in files
               if p.name.lower() in {"main.py", "app.py", "server.py", "index.js", "index.ts",
                                     "main.go", "program.cs", "index.html", "package.json",
                                     "cargo.toml", "pyproject.toml"}][:6]
    if entries:
        ctx.add("entry-points", "Entry-point candidates",
                ", ".join(entries), confidence=0.8, tags=("entry",))
    if ctx.facts["source_files"]:
        ctx.edit("source", str(ctx.local_root),
                 "Edit the source tree directly; take a baseline test first, then patch the smallest module.",
                 risk="low", confidence=0.9, requires=("local write access",))
    if bins:
        ctx.edit("binary", ", ".join(p.name for p in bins[:3]),
                 "Treat as a compiled artifact: map sections and imports before any patch.",
                 risk="medium", confidence=0.75)
    return {"status": "signal",
            "summary": f"{len(files)} files, {len(languages)} language signals, {len(bins)} binaries."}


def p_manifest_read(ctx: Ctx) -> dict:
    found = 0
    packages: list[str] = []

    def get(name: str) -> str:
        if ctx.local_root and ctx.local_root.is_dir():
            path = ctx.local_root / name
            return read_text(path) if path.is_file() else ""
        return ctx.blobs.get(name, "")

    def walk_names() -> list[str]:
        if not ctx.local_root or not ctx.local_root.is_dir():
            return []
        try:
            return [p.name for p in ctx.local_root.iterdir() if p.is_file()]
        except OSError:
            return []

    names = walk_names()
    candidate_names = [
        "package.json", "pyproject.toml", "requirements.txt", "setup.py", "Cargo.toml",
        "go.mod", "composer.json", "Gemfile", "pubspec.yaml", "AndroidManifest.xml",
        "Info.plist", "app.json", "app.config.js", ".env.example", ".env.sample",
    ] + [n for n in names if n.endswith(".csproj")]
    if ctx.blobs:
        candidate_names += [n for n in ctx.blobs if n not in candidate_names]

    for name in dict.fromkeys(candidate_names):
        text = get(name)
        if not text:
            continue
        found += 1
        label = Path(name).name
        if label == "package.json":
            data = safe_json(text)
            deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
            scripts = data.get("scripts") or {}
            packages.extend(str(k) for k in list(deps)[:40])
            ctx.add("manifest", f"{data.get('name') or label} · {len(deps)} dependencies",
                    f"Scripts: {', '.join(list(scripts)[:8]) or 'none'}. "
                    f"Entry: {data.get('main') or data.get('bin') or 'not declared'}. "
                    f"Runtime hints: {', '.join(f'{k}@{v}' for k, v in list(deps.items())[:6])}",
                    path=label, confidence=0.9, tags=("manifest", "node"))
            ctx.learn("Node.js")
            ctx.facts["config"] = True
            if scripts:
                ctx.edit("config", f"{label} → scripts",
                         "Change build/start/test behaviour in the declared scripts before touching code.",
                         risk="low", confidence=0.9, evidence="")
            if deps:
                ctx.edit("dependency", f"{label} → dependencies",
                         "Pin, fork or vendor a dependency to change behaviour without editing upstream.",
                         risk="low", confidence=0.7)
        elif label == "requirements.txt":
            lines = [ln.strip() for ln in text.splitlines()
                     if ln.strip() and not ln.strip().startswith("#")]
            packages.extend(re.split(r"[=<>!~ ]", ln)[0] for ln in lines[:40])
            ctx.add("manifest", f"Python requirements · {len(lines)} pins",
                    ", ".join(lines[:10]), path=label, confidence=0.9,
                    tags=("manifest", "python"))
            ctx.learn("Python")
            ctx.edit("dependency", label,
                     "Change or pin the dependency set to alter behaviour at the package boundary.",
                     risk="low", confidence=0.75)
        elif label == "pyproject.toml":
            name_match = re.search(r'(?m)^name\s*=\s*"([^"]+)"', text)
            dep_block = re.search(r"dependencies\s*=\s*\[(.*?)\]", text, re.S)
            deps = re.findall(r'"([^"]+)"', dep_block.group(1)) if dep_block else []
            packages.extend(d.split(">=")[0].split("==")[0].strip() for d in deps[:40])
            ctx.add("manifest", f"Python project · {name_match.group(1) if name_match else 'unnamed'}",
                    f"{len(deps)} declared dependencies: {', '.join(deps[:8])}",
                    path=label, confidence=0.9, tags=("manifest", "python"))
            ctx.learn("Python")
        elif label == "Cargo.toml":
            name_match = re.search(r'(?m)^name\s*=\s*"([^"]+)"', text)
            ctx.add("manifest", f"Rust crate · {name_match.group(1) if name_match else 'unnamed'}",
                    "Cargo manifest found; crate is forkable at the module boundary.",
                    path=label, confidence=0.9, tags=("manifest", "rust"))
            ctx.learn("Rust")
            ctx.edit("source", label, "Fork the crate or patch the local vendored copy.",
                     risk="low", confidence=0.8)
        elif label == "go.mod":
            module = re.search(r"(?m)^module\s+(\S+)", text)
            requires = re.findall(r"(?m)^\s*require\s+(\S+)", text)
            packages.extend(requires[:20])
            ctx.add("manifest", f"Go module · {module.group(1) if module else 'unnamed'}",
                    f"{len(requires)} direct requirements.", path=label,
                    confidence=0.9, tags=("manifest", "go"))
            ctx.learn("Go")
        elif label == "AndroidManifest.xml":
            pkg = re.search(r'package="([^"]+)"', text)
            perms = re.findall(r'<uses-permission[^>]*android:name="([^"]+)"', text)
            acts = re.findall(r'<activity[^>]*android:name="([^"]+)"', text)
            ctx.add("manifest", f"Android package · {pkg.group(1) if pkg else 'unknown'}",
                    f"{len(perms)} permissions, {len(acts)} activities. "
                    f"Permissions: {', '.join(p.rsplit('.', 1)[-1] for p in perms[:8])}",
                    path=label, confidence=0.85, tags=("manifest", "android"))
            ctx.learn("Android")
            ctx.edit("binary", label,
                     "Changing the manifest requires a rebuild and re-sign (or a patched, re-signed APK).",
                     risk="medium", confidence=0.8)
        elif label == "Info.plist":
            bundle = re.search(r"<key>CFBundleIdentifier</key>\s*<string>([^<]+)", text)
            ctx.add("manifest", f"Apple bundle · {bundle.group(1) if bundle else 'unknown'}",
                    "Info.plist present; entitlements and code signing constrain edits.",
                    path=label, confidence=0.85, tags=("manifest", "apple"))
            ctx.learn("Apple platform")
        elif label.endswith(".csproj"):
            target = re.search(r"<TargetFramework>([^<]+)", text)
            refs = re.findall(r'<PackageReference\s+Include="([^"]+)"', text)
            packages.extend(refs[:20])
            ctx.add("manifest", f".NET project · {target.group(1) if target else 'unknown TFM'}",
                    f"{len(refs)} package references.", path=label,
                    confidence=0.85, tags=("manifest", "dotnet"))
            ctx.learn(".NET")
        elif "env" in label.lower():
            keys = re.findall(r"(?m)^\s*([A-Z][A-Z0-9_]{2,})\s*=", text)
            ctx.facts["config"] = True
            ctx.add("config", f"Environment contract · {len(keys)} keys",
                    "Keys only; values are never stored. " + ", ".join(keys[:12]),
                    path=label, confidence=0.9, tags=("config",))
            if keys:
                ctx.edit("config", f"{label} → {', '.join(keys[:3])}",
                         "Override behaviour through supported environment configuration first.",
                         risk="low", confidence=0.85)
        else:
            ctx.add("manifest", f"{label} present", text.strip().splitlines()[0][:160] if text.strip() else "",
                    path=label, confidence=0.7, tags=("manifest",))

    if packages:
        ctx.facts["packages"] = list(dict.fromkeys(packages))[:20]
    if not found:
        return {"status": "empty", "summary": "No recognisable manifest or config file."}
    return {"status": "signal", "summary": f"{found} manifest/config file(s), {len(packages)} packages."}


def p_code_structure(ctx: Ctx) -> dict:
    files = 0
    routes: list[tuple[str, str, str]] = []
    imports = 0
    todos: list[tuple[str, str, int]] = []
    flags: set[str] = set()
    module_dirs: Counter = Counter()
    for label, text in ctx.texts():
        files += 1
        info = scan_source(text)
        imports += info["imports"]
        flags.update(info["env"])
        for method, path in info["routes"]:
            if len(routes) < 60:
                routes.append((method, path, label))
        for kind, note, line in info["todos"]:
            if len(todos) < 8:
                todos.append((f"{label}:{line}", f"{kind}: {note}", line))
        parts = Path(label).parts
        if len(parts) > 1:
            module_dirs[parts[0]] += 1
    if not files:
        return {"status": "empty", "summary": "No source files readable."}
    ctx.facts["source_files"] = files
    ctx.facts["routes"] = [(m, p) for m, p, _ in routes][:30]
    detail = ", ".join(f"{m} {p}" for m, p, _ in routes[:10]) or "no route declarations found"
    ctx.add("structure", f"{files} source files · {imports} imports · {len(routes)} routes",
            detail, confidence=0.85, tags=("structure",))
    if routes:
        ctx.edit("source", ", ".join(sorted({label for _, _, label in routes}))[:200],
                 "Route handlers are the behaviour seam: change the handler, not the framework.",
                 risk="low", confidence=0.85)
    if module_dirs:
        ctx.add("modules", "Module map",
                ", ".join(f"{name} ({count})" for name, count in module_dirs.most_common(8)),
                confidence=0.8, tags=("modules",))
    if flags:
        ctx.facts["flags"] = set(flags)
        ctx.facts["config"] = True
        ctx.add("switches", f"{len(flags)} environment switches",
                ", ".join(sorted(flags)[:14]), confidence=0.8, tags=("config", "flags"))
        ctx.edit("config", ", ".join(sorted(flags)[:4]),
                 "Flip a supported switch before editing code; it is the cheapest reversible change.",
                 risk="low", confidence=0.8)
    if todos:
        ctx.add("debt", f"{len(todos)} marked change sites",
                "; ".join(f"{loc} {note}" for loc, note, _ in todos[:5]),
                confidence=0.7, tags=("todos",))
        for loc, note, _ in todos[:4]:
            ctx.edit("source", loc, f"Inspect the marked change site ({note[:80]}).",
                     risk="low", confidence=0.65)
    return {"status": "signal", "summary": f"{files} files, {len(routes)} route(s), {len(flags)} switch(es)."}


def p_api_surface(ctx: Ctx) -> dict:
    urls: set[str] = set()
    fetches: set[str] = set()
    sockets: set[str] = set()
    graphql = False
    auth_hints: set[str] = set()
    for label, text in ctx.texts():
        info = scan_source(text)
        urls.update(info["urls"])
        fetches.update(info["fetches"])
        sockets.update(info["ws"])
        graphql = graphql or info["graphql"]
        low = text.lower()
        for hint in ("bearer ", "basic ", "oauth", "x-api-key", "authorization"):
            if hint in low:
                auth_hints.add(hint.strip())
    total = len(urls) + len(fetches) + len(sockets)
    if not total and not graphql:
        return {"status": "empty", "summary": "No request sites found in source."}
    ctx.facts["endpoints"].update(urls)
    ctx.facts["endpoints"].update(fetches)
    ctx.learn("HTTP API")
    if graphql:
        ctx.learn("GraphQL")
    ctx.add("protocol", f"{total} request surface(s)",
            "Absolute hosts: " + (", ".join(sorted(urls)[:8]) or "none") +
            ". Client calls: " + (", ".join(sorted(fetches)[:8]) or "none") +
            (". GraphQL operations detected." if graphql else ""),
            confidence=0.85, tags=("api", "protocol"))
    for url in sorted(urls)[:5]:
        host = urllib.parse.urlsplit(url).netloc
        if host:
            ctx.add("host", f"Remote dependency · {host}", url, confidence=0.9, tags=("host",))
    if auth_hints:
        ctx.add("auth", "Authentication shape observed",
                ", ".join(sorted(auth_hints)) + " (scheme only; no values recorded).",
                confidence=0.75, tags=("auth",))
    ctx.edit("protocol", ", ".join(sorted(urls)[:3]) or "in-app request sites",
             "Reimplement or intercept the client protocol at the request boundary; keep it read-only until authorised.",
             risk="medium", confidence=0.8, requires=("authorised integration",))
    if sockets:
        ctx.add("protocol", f"{len(sockets)} websocket endpoint(s)",
                ", ".join(sorted(sockets)[:6]), confidence=0.85, tags=("protocol", "realtime"))
    return {"status": "signal", "summary": f"{total} request sites, {len(urls)} hosts."}


def p_git_history(ctx: Ctx) -> dict:
    if not ctx.local_root or not ctx.local_root.is_dir():
        return {"status": "empty", "summary": "No local working tree."}
    git_dir = ctx.local_root / ".git"
    if not git_dir.exists():
        return {"status": "empty", "summary": "Not a git working tree."}
    config_text = read_text(git_dir / "config", 20000)
    remote_match = GITHUB_RE.search(config_text)
    if remote_match:
        ctx.repo = ctx.repo or f"{remote_match.group(1)}/{remote_match.group(2).removesuffix('.git')}"
        ctx.add("source", "Git remote found", f"github.com/{ctx.repo}",
                confidence=0.95, tags=("git", "source"))
        ctx.source("Git remote", f"https://github.com/{ctx.repo}")
    log = ""
    try:
        result = subprocess.run(
            ["git", "-C", str(ctx.local_root), "log", "-n", "150",
             "--pretty=format:%h|%ad|%an|%s", "--date=short", "--name-only"],
            capture_output=True, text=True, errors="replace", timeout=12,
        )
        log = result.stdout or ""
    except (OSError, subprocess.SubprocessError) as exc:
        ctx.add("warning", "Git CLI unavailable", str(exc), confidence=0.6)
    if not log.strip():
        return {"status": "empty",
                "summary": "No commit history readable (shallow, bare or git missing)."}
    churn: Counter = Counter()
    authors: Counter = Counter()
    subjects: list[str] = []
    for line in log.splitlines():
        if "|" in line and len(line.split("|")) >= 4:
            _, _, author, subject = line.split("|", 3)
            authors[author] += 1
            subjects.append(subject)
        elif line.strip():
            churn[line.strip()] += 1
    ctx.add("history", f"{len(subjects)} commits · {len(authors)} authors",
            "Recent work: " + "; ".join(subjects[:6]), confidence=0.85, tags=("git",))
    ctx.add("history", "Highest-churn files",
            ", ".join(f"{name} ({count})" for name, count in churn.most_common(8)),
            confidence=0.8, tags=("git", "churn"))
    for name, _ in churn.most_common(4):
        ctx.edit("source", name,
                 "High-churn file: the most likely place a behaviour change belongs (and the most likely to regress).",
                 risk="low", confidence=0.7)
    return {"status": "signal", "summary": f"{len(subjects)} commits, {len(churn)} touched files."}


def p_binary_header(ctx: Ctx) -> dict:
    binaries = ctx.inventory()["binaries"] if ctx.local_root else []
    if not binaries:
        return {"status": "empty", "summary": "No compiled artifact at this target."}
    parsed = 0
    for path in binaries[:4]:
        try:
            raw = path.read_bytes()[:2_000_000]
        except OSError:
            continue
        if not raw:
            continue
        name = path.name
        head = raw[:32]
        kind = "unknown"
        details: list[str] = []
        if head[:2] == b"MZ":
            kind = "PE"
            info = parse_pe(raw)
            details = info["details"]
            ctx.learn("Windows PE")
            if info["signed"]:
                ctx.facts["signed"] = True
                details.append("Authenticode certificate table present")
            for section in info["writable"]:
                ctx.edit("binary", f"{name}#{section}",
                         "Writable section: a byte patch survives here, but signature and integrity checks come first.",
                         risk="high", confidence=0.8)
            if info["overlay"]:
                ctx.edit("binary", f"{name} overlay",
                         "Appended overlay region: common carrier for config, licences or packed payloads.",
                         risk="medium", confidence=0.7)
            if info["packed"]:
                ctx.edit("binary", f"{name} packed",
                         "Packed/obfuscated: unpack (or hook at runtime) before static patching is useful.",
                         risk="high", confidence=0.75)
        elif head[:4] == b"\x7fELF":
            kind = "ELF"
            details = parse_elf(raw)
            ctx.learn("ELF")
        elif head[:4] in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe"):
            kind = "Mach-O"
            details = parse_macho(raw)
            ctx.learn("Mach-O")
        elif head[:4] == b"PK\x03\x04":
            kind = "ZIP"
            try:
                with zipfile.ZipFile(path) as archive:
                    names = archive.namelist()
                if "AndroidManifest.xml" in names:
                    kind = "APK"
                    ctx.learn("Android")
                elif any(n.startswith("META-INF/") for n in names):
                    kind = "JAR"
                    ctx.learn("JVM")
                details = [f"{len(names)} entries", ", ".join(names[:6])]
            except (OSError, zipfile.BadZipFile):
                details = ["ZIP container (unreadable index)"]
        elif head[:4] == b"hsqs":
            kind = "SquashFS"
            details = parse_squashfs(raw)
            ctx.learn("SquashFS")
        elif head[:2] == b"\x1f\x8b":
            kind = "gzip"
            details = ["gzip stream", f"entropy {entropy(raw[:65536]):.2f} bits/byte"]
        elif head[:4] == b"\x27\x05\x19\x56" or head[:4] == b"E\xd3\xd2\x81":
            kind = "CramFS"
            details = ["CramFS image"]
        parsed += 1
        if kind == "unknown":
            details = [f"magic {head[:8].hex()}", f"entropy {entropy(raw[:65536]):.2f}"]
        ctx.add("binary", f"{name} · {kind}",
                ". ".join(str(d) for d in details if d),
                path=name, confidence=0.9 if kind != "unknown" else 0.45,
                tags=("binary", kind.lower()))
        if kind in {"PE", "ELF", "Mach-O"}:
            ctx.edit("binary", name,
                     "Prefer a runtime hook or import shim over a byte patch; patches break on update and signature.",
                     risk="medium", confidence=0.7)
    if not parsed:
        return {"status": "empty", "summary": "Binary candidates were unreadable."}
    return {"status": "signal", "summary": f"{parsed} artifact(s) identified."}


def parse_pe(raw: bytes) -> dict:
    details = []
    signed = False
    packed = False
    writable: list[str] = []
    overlay = False
    try:
        pe = struct.unpack_from("<I", raw, 0x3C)[0]
        if raw[pe:pe + 4] != b"PE\x00\x00":
            return {"details": ["DOS stub without a PE header"], "signed": False,
                    "writable": [], "overlay": False, "packed": False}
        machine, sections = struct.unpack_from("<HH", raw, pe + 4)
        opt_size = struct.unpack_from("<H", raw, pe + 20)[0]
        opt = pe + 24
        magic = struct.unpack_from("<H", raw, opt)[0]
        bitness = "PE32+" if magic == 0x20B else "PE32"
        subsystem = struct.unpack_from("<H", raw, opt + 68)[0]
        machines = {0x14C: "x86", 0x8664: "x86-64", 0x1C0: "ARM", 0xAA64: "ARM64"}
        subsystems = {2: "GUI", 3: "console", 9: "Windows CE", 10: "EFI"}
        details = [f"{machines.get(machine, hex(machine))} · {bitness} · "
                   f"{subsystems.get(subsystem, subsystem)} · {sections} sections"]
        section_at = opt + opt_size
        end = 0
        for index in range(min(sections, 24)):
            offset = section_at + index * 40
            if offset + 40 > len(raw):
                break
            sname = raw[offset:offset + 8].rstrip(b"\x00").decode("latin-1", "replace")
            raw_size, raw_ptr = struct.unpack_from("<II", raw, offset + 16)
            flags = struct.unpack_from("<I", raw, offset + 36)[0]
            end = max(end, raw_ptr + raw_size)
            details.append(f"{sname or '?'}({'W' if flags & 0x80000000 else '-'}"
                           f"{'X' if flags & 0x20000000 else '-'})")
            if flags & 0x80000000:
                writable.append(sname or f"section{index}")
            if sname in {".aspack", ".upx0", ".upx1", ".themida", ".vmp0"}:
                packed = True
        if b"UPX!" in raw[:2_000_000]:
            packed = True
        if len(raw) > end + 0x400 and end > 0:
            overlay = True
        dd = opt + (112 if magic == 0x20B else 96)
        if dd + 40 <= len(raw):
            cert_size = struct.unpack_from("<I", raw, dd + 4 * 8 + 4)[0]
            signed = cert_size > 0
    except (struct.error, IndexError):
        details.append("PE header truncated")
    return {"details": details, "signed": signed, "writable": writable,
            "overlay": overlay, "packed": packed}


def parse_elf(raw: bytes) -> list[str]:
    try:
        bitness = {1: "32-bit", 2: "64-bit"}.get(raw[4], "?")
        endian = {1: "little", 2: "big"}.get(raw[5], "?")
        etype = struct.unpack_from("<H", raw, 16)[0]
        machine = struct.unpack_from("<H", raw, 18)[0]
        machines = {0x03: "x86", 0x3E: "x86-64", 0x28: "ARM", 0xB7: "AArch64",
                    0x08: "MIPS", 0xF3: "RISC-V"}
        types = {1: "relocatable", 2: "executable", 3: "shared object", 4: "core"}
        return [f"{machines.get(machine, hex(machine))} · {bitness} {endian} · "
                f"{types.get(etype, etype)}"]
    except (struct.error, IndexError):
        return ["ELF header truncated"]


def parse_macho(raw: bytes) -> list[str]:
    try:
        cputype = struct.unpack_from("<I", raw, 4)[0]
        cpus = {7: "x86", 0x01000007: "x86-64", 12: "ARM", 0x0100000C: "ARM64"}
        return [f"{cpus.get(cputype, hex(cputype))} Mach-O"]
    except (struct.error, IndexError):
        return ["Mach-O header truncated"]


def parse_squashfs(raw: bytes) -> list[str]:
    try:
        inodes = struct.unpack_from("<I", raw, 4)[0]
        block_size = struct.unpack_from("<I", raw, 12)[0]
        compression = struct.unpack_from("<H", raw, 20)[0]
        bytes_used = struct.unpack_from("<Q", raw, 40)[0]
        codecs = {1: "gzip", 2: "lzma", 3: "lzo", 4: "xz", 5: "lz4", 6: "zstd"}
        return [f"{inodes} inodes · {bytes_used} bytes used · "
                f"{block_size}-byte blocks · {codecs.get(compression, compression)}"]
    except (struct.error, IndexError):
        return ["SquashFS superblock truncated"]


def p_binary_strings(ctx: Ctx) -> dict:
    binaries = ctx.inventory()["binaries"] if ctx.local_root else []
    if not binaries:
        return {"status": "empty", "summary": "No compiled artifact to pull strings from."}
    urls: set[str] = set()
    versions: set[str] = set()
    frameworks: set[str] = set()
    registry: set[str] = set()
    paths: set[str] = set()
    errors: list[str] = []
    scanned = 0
    for path in binaries[:3]:
        try:
            raw = path.read_bytes()[:4_000_000]
        except OSError:
            continue
        scanned += 1
        ascii_strings = [s.decode("latin-1", "replace")
                         for s in re.findall(rb"[\x20-\x7e]{5,}", raw)][:6000]
        wide = [s.decode("utf-16-le", "replace")
                for s in re.findall(rb"(?:[\x20-\x7e]\x00){5,}", raw)][:1500]
        strings = ascii_strings + wide
        urls.update(re.findall(r"https?://[^\s\"'<>]+", "\n".join(strings))[:20])
        versions.update(re.findall(r"\b\d+\.\d+\.\d+(?:\.\d+)?\b", "\n".join(strings))[:12])
        registry.update(re.findall(r"(?:HKEY_[A-Z_]+|SOFTWARE\\[^\s\"']{3,60})",
                                   "\n".join(strings))[:6])
        paths.update(re.findall(r"(?:[A-Za-z]:\\[^\s\"'<>|]{3,60}|/(?:usr|etc|opt)/[^\s\"'<>]{2,50})",
                                "\n".join(strings))[:8])
        for token in ("Qt5", "Qt6", "Electron", "UnityPlayer", "flutter", "python3",
                      "libcrypto", "OpenSSL", "mscoree", "libcef", "JVM", "WebView2"):
            if token.lower() in "\n".join(strings).lower():
                frameworks.add(token)
        errors.extend([s for s in strings if len(s) > 24 and
                       re.search(r"(?i)\b(error|failed|cannot|denied|invalid)\b", s)][:5])
    if not scanned:
        return {"status": "empty", "summary": "String extraction failed."}
    ctx.learn(*frameworks)
    if urls:
        ctx.add("strings", f"{len(urls)} embedded URL(s)",
                ", ".join(sorted(urls)[:8]), confidence=0.85, tags=("strings", "endpoint"))
        ctx.facts["endpoints"].update(urls)
        ctx.edit("protocol", ", ".join(sorted(urls)[:3]),
                 "Embedded endpoint: a hosts/proxy override can redirect behaviour without touching code.",
                 risk="medium", confidence=0.7)
    if frameworks:
        ctx.add("runtime", "Runtime fingerprint",
                ", ".join(sorted(frameworks)), confidence=0.8, tags=("runtime",))
        ctx.learn(*frameworks)
    if versions:
        ctx.add("strings", "Version strings", ", ".join(sorted(versions)[:10]),
                confidence=0.7, tags=("versions",))
    if registry or paths:
        ctx.add("config", "Embedded paths and keys",
                ", ".join(sorted(registry)[:4] + sorted(paths)[:4]),
                confidence=0.75, tags=("config",))
        ctx.edit("config", ", ".join(sorted(registry)[:2] + sorted(paths)[:2]) or "embedded paths",
                 "Configuration and lookup paths are an override surface; confirm precedence before changing them.",
                 risk="medium", confidence=0.65)
    if errors:
        ctx.add("strings", "Error surface", " | ".join(errors[:5]),
                confidence=0.7, tags=("behaviour",))
    return {"status": "signal",
            "summary": f"{len(urls)} URLs, {len(frameworks)} runtime fingerprints."}


CONTROL_TERMS = {
    "Secure Boot": ("secure boot", "secureboot"),
    "Measured boot": ("measured boot", "tpm", "pcr["),
    "Verified boot": ("verified boot", "dm-verity", "android verified boot", "avb"),
    "Rollback protection": ("rollback", "anti-rollback"),
    "Code signing": ("authenticode", "code signing", "signature", "signed"),
    "Encryption at rest": ("file-based encryption", "full-disk encryption", "encrypted"),
}


def p_binary_controls(ctx: Ctx) -> dict:
    """Map signature and boot controls without attempting to defeat them."""
    binaries = ctx.inventory()["binaries"] if ctx.local_root else []
    if not binaries:
        return {"status": "empty", "summary": "No compiled artifact to map controls from."}
    mapped = 0
    for path in binaries[:4]:
        try:
            raw = path.read_bytes()[:4_000_000]
        except OSError:
            continue
        if not raw:
            continue
        head = raw[:16]
        name = path.name
        format_name = "Container"
        controls: list[str] = []
        if head[:2] == b"MZ":
            format_name = "PE"
            info = parse_pe(raw)
            if info["signed"]:
                controls.append("Authenticode certificate table")
                ctx.facts["signed"] = True
            if info["writable"]:
                controls.append("writable section(s): " + ", ".join(info["writable"][:4]))
            if info["packed"]:
                controls.append("packed/obfuscated sections")
            if info["overlay"]:
                controls.append("appended overlay")
        elif head[:4] == b"\x7fELF":
            format_name = "ELF"
        elif head[:4] == b"hsqs":
            format_name = "SquashFS"
        elif head[:4] == b"PK\x03\x04":
            format_name = "ZIP"
        text = raw.decode("latin-1", "replace").lower()
        for label, needles in CONTROL_TERMS.items():
            if any(needle in text for needle in needles):
                controls.append(label)
        controls = list(dict.fromkeys(controls))
        if not controls:
            continue
        mapped += 1
        ctx.facts.setdefault("controls", []).extend(controls)
        ctx.add("control", f"{name} · control map",
                f"{format_name}; " + "; ".join(controls) +
                ". Static control evidence only; no bypass attempted.",
                path=name, confidence=0.85, tags=("control", "hardware"))
        ctx.edit("control", name,
                 "Treat signatures, secure boot and rollback counters as boundaries: use an authorised "
                 "signed build or owner-approved recovery path. Do not patch around a control.",
                 risk="high", confidence=0.8,
                 requires=("owner sign-off", "recovery path"))
    if not mapped:
        return {"status": "empty",
                "summary": "No signature or boot-control indicators at this target."}
    return {"status": "signal", "summary": f"{mapped} control map(s) recorded."}


def p_device_firmware(ctx: Ctx) -> dict:
    candidates = ctx.inventory()["binaries"] if ctx.local_root else []
    containers = [p for p in candidates if p.suffix.lower() in
                  {".img", ".bin", ".apk", ".zip", ".tar", ".gz", ".tgz", ".fw", ".rom"}]
    if not containers:
        return {"status": "empty", "summary": "No firmware/container image at this target."}
    findings = 0
    for path in containers[:3]:
        try:
            raw = path.read_bytes()[:6_000_000]
        except OSError:
            continue
        markers = []
        for magic, label in ((b"hsqs", "SquashFS"), (b"ANDROID!", "Android boot"),
                             (b"UBI#", "UBIFS"), (b"\x27\x05\x19\x56", "CramFS"),
                             (b"\x1f\x8b\x08", "gzip"), (b"PK\x03\x04", "ZIP")):
            offset = raw.find(magic)
            if offset >= 0:
                markers.append(f"{label}@0x{offset:x}")
        if not markers:
            continue
        findings += 1
        ctx.add("firmware", f"{path.name} · container map",
                ", ".join(markers), path=path.name, confidence=0.85, tags=("firmware",))
        if b"hsqs" in raw or b"UBI#" in raw:
            ctx.edit("firmware", path.name,
                     "Unpack the rootfs, change the smallest file, repack with the same block size; bootloader trust is the risk.",
                     risk="high", confidence=0.75, requires=("device recovery path",))
        if b"PK\x03\x04" in raw:
            try:
                with zipfile.ZipFile(path) as archive:
                    names = archive.namelist()
                interesting = [n for n in names if n.startswith(("etc/", "bin/", "www/", "lib/"))][:10]
                if interesting:
                    ctx.add("firmware", f"{path.name} · filesystem hints",
                            ", ".join(interesting), path=path.name,
                            confidence=0.85, tags=("firmware", "rootfs"))
                    ctx.edit("firmware", f"{path.name} → {interesting[0]}",
                             "Repack only the touched entries; preserve the original signature envelope.",
                             risk="high", confidence=0.7)
            except (OSError, zipfile.BadZipFile):
                pass
    if not findings:
        return {"status": "empty", "summary": "Container signatures not recognised."}
    return {"status": "signal", "summary": f"{findings} container image(s) mapped."}


def p_docs_mine(ctx: Ctx) -> dict:
    docs: list[tuple[str, str]] = []
    if ctx.local_root and ctx.local_root.is_dir():
        for pattern in ("README*", "docs/*.md", "doc/*.md", "*.md"):
            for path in sorted(ctx.local_root.glob(pattern))[:4]:
                if path.is_file():
                    docs.append((path.name, read_text(path, 200_000)))
    elif ctx.blobs:
        for name, text in ctx.blobs.items():
            if Path(name).name.lower().startswith(("readme", "docs", "install")):
                docs.append((name, text))
    docs = [(name, text) for name, text in docs if text.strip()]
    if not docs:
        return {"status": "empty", "summary": "No README or docs readable."}
    for name, text in docs[:3]:
        headings = re.findall(r"(?m)^#{1,3}\s+(.+)$", text)[:8]
        commands = re.findall(r"(?m)^\s*(?:\$ )?((?:npm|pnpm|yarn|pip|python|cargo|go|docker|make|dotnet)\b[^\n]{0,80})",
                              text)[:5]
        claims = [ln.strip() for ln in text.splitlines()
                  if re.search(r"(?i)\b(supports?|requires?|compatible|install|usage)\b", ln)][:4]
        ctx.add("docs", f"{name} · {len(text.splitlines())} lines",
                "Headings: " + (", ".join(headings) or "none") +
                ". Commands: " + ("; ".join(commands) or "none") +
                ". Claims: " + ("; ".join(c[:80] for c in claims) or "none"),
                path=name, confidence=0.8, tags=("docs",))
        ctx.source("Project documentation", name)
        if commands:
            ctx.edit("config", f"{name} → setup commands",
                     "The documented build/run path is the cheapest supported way to reproduce behaviour.",
                     risk="low", confidence=0.7)
    return {"status": "signal", "summary": f"{len(docs)} document(s) mined."}


def p_test_surface(ctx: Ctx) -> dict:
    tests: list[str] = []
    ci: list[str] = []
    if ctx.local_root and ctx.local_root.is_dir():
        for path in ctx.inventory()["files"]:
            name = path.name.lower()
            rel = str(path)
            if ("test" in name or "spec" in name) and path.suffix.lower() in SOURCE_EXT:
                tests.append(path.name)
            if ".github/workflows" in rel or name in {"gitlab-ci.yml", ".gitlab-ci.yml",
                                                     "jenkinsfile", "azure-pipelines.yml"}:
                ci.append(path.name)
    elif ctx.repo_files:
        tests = [n for n in ctx.repo_files
                 if re.search(r"(^|/)(tests?|spec)/|(test|spec)\.", n, re.I)][:8]
        ci = [n for n in ctx.repo_files if ".github/workflows/" in n or "gitlab-ci" in n][:4]
    if not tests and not ci:
        return {"status": "empty", "summary": "No verification harness found."}
    ctx.add("verification", f"{len(tests)} test file(s) · {len(ci)} CI pipeline(s)",
            "Tests: " + (", ".join(tests[:8]) or "none") + ". CI: " + (", ".join(ci[:4]) or "none"),
            confidence=0.85, tags=("tests", "ci"))
    ctx.facts["has_tests"] = bool(tests or ci)
    ctx.edit("verification", ", ".join(tests[:2] + ci[:2]) or "CI",
             "Run the existing harness before and after a change; add a black-box check when none exists.",
             risk="low", confidence=0.8)
    return {"status": "signal", "summary": f"{len(tests)} tests, {len(ci)} pipelines."}


def p_traffic_har(ctx: Ctx) -> dict:
    """Read an operator-supplied HAR capture. Names only; never values."""
    path = ctx.local_root
    if not path or not path.is_file() or path.suffix.lower() != ".har":
        return {"status": "empty", "summary": "No HAR capture at this target."}
    text = read_text(path, HAR_MAX_BYTES)
    if not text.strip():
        return {"status": "empty", "summary": "The HAR file is empty or unreadable."}
    try:
        data = json.loads(text.lstrip("\ufeff"))
    except (TypeError, ValueError):
        return {"status": "empty", "summary": "The HAR file is not valid JSON."}
    log = data.get("log") if isinstance(data, dict) else None
    entries = log.get("entries") if isinstance(log, dict) else None
    if not isinstance(entries, list) or not entries:
        return {"status": "empty", "summary": "The HAR contains no request entries."}
    creator = log.get("creator") if isinstance(log.get("creator"), dict) else {}
    hosts: Counter = Counter()
    methods: Counter = Counter()
    statuses: Counter = Counter()
    mimes: Counter = Counter()
    endpoints: Counter = Counter()
    auth_headers: set[str] = set()
    credential_params: set[str] = set()
    servers: set[str] = set()
    mutating = 0
    graphql = False
    websocket = False
    skipped = 0
    tls_protocols: set[str] = set()
    tls_ciphers: set[str] = set()
    tls_issuers: set[str] = set()
    tls_subjects: set[str] = set()
    tls_valid_to: set[str] = set()

    for entry in entries[:HAR_MAX_ENTRIES]:
        if not isinstance(entry, dict):
            continue
        request = entry.get("request") if isinstance(entry.get("request"), dict) else {}
        url = str(request.get("url") or "")
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            skipped += 1
            continue
        method = str(request.get("method") or "GET").upper()[:12]
        methods[method] += 1
        hosts[parts.netloc] += 1
        path_only = parts.path or "/"
        normalized = re.sub(r"/[0-9a-f]{8,}(?=/|$)", "/{id}", path_only, flags=re.I)
        normalized = re.sub(r"/\d+(?=/|$)", "/{n}", normalized)[:120]
        endpoints[f"{method} {parts.scheme}://{parts.netloc}{normalized}"] += 1
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            mutating += 1
        if "/graphql" in normalized.lower():
            graphql = True
        for header in request.get("headers") or []:
            if isinstance(header, dict):
                name = str(header.get("name") or "").lower()
                if name in AUTH_HEADERS:
                    auth_headers.add(name)
        for query in request.get("queryString") or []:
            if isinstance(query, dict):
                name = str(query.get("name") or "").lower()
                if name in CREDENTIAL_PARAMS:
                    credential_params.add(name)
        post = request.get("postData") if isinstance(request.get("postData"), dict) else {}
        post_text = str(post.get("text") or "")[:20000]
        if re.search(r'"query"\s*:', post_text):
            graphql = True
        response = entry.get("response") if isinstance(entry.get("response"), dict) else {}
        try:
            status = int(response.get("status") or 0)
        except (TypeError, ValueError):
            status = 0
        statuses[f"{status // 100}xx" if 100 <= status < 600 else "no response"] += 1
        content = response.get("content") if isinstance(response.get("content"), dict) else {}
        mime = str(content.get("mimeType") or "").split(";")[0].strip().lower()
        if mime:
            mimes[mime] += 1
        for header in response.get("headers") or []:
            if isinstance(header, dict):
                name = str(header.get("name") or "").lower()
                value = str(header.get("value") or "").strip()
                if name in {"server", "x-powered-by"} and value:
                    servers.add(value[:60])
        security = entry.get("_securityDetails") if isinstance(entry.get("_securityDetails"), dict) else (
            response.get("_securityDetails") if isinstance(response.get("_securityDetails"), dict) else {})
        if security:
            if str(security.get("protocol") or "").strip():
                tls_protocols.add(str(security["protocol"]).strip()[:40])
            if str(security.get("cipher") or "").strip():
                tls_ciphers.add(str(security["cipher"]).strip()[:60])
            if str(security.get("issuerName") or "").strip():
                tls_issuers.add(str(security["issuerName"]).strip()[:120])
            if str(security.get("subjectName") or "").strip():
                tls_subjects.add(str(security["subjectName"]).strip()[:120])
            if str(security.get("validTo") or "").strip():
                tls_valid_to.add(str(security["validTo"]).strip()[:40])
        if entry.get("_webSocketMessages"):
            websocket = True

    total = sum(hosts.values())
    if not total:
        return {"status": "empty", "summary": "No HTTP(S) requests in the capture."}
    ctx.facts["har"] = True
    if tls_protocols or tls_ciphers or tls_issuers:
        ctx.facts["tls"] = {
            "protocols": sorted(tls_protocols),
            "ciphers": sorted(tls_ciphers),
            "issuers": sorted(tls_issuers),
            "subjects": sorted(tls_subjects),
            "valid_to": sorted(tls_valid_to),
        }
        ctx.learn("TLS")
        ctx.add("tls", "TLS metadata",
                "Protocols: " + (", ".join(sorted(tls_protocols)) or "undisclosed") +
                "; ciphers: " + (", ".join(sorted(tls_ciphers)) or "undisclosed") +
                "; issuer(s): " + (", ".join(sorted(tls_issuers)) or "undisclosed") +
                "; subject(s): " + (", ".join(sorted(tls_subjects)) or "undisclosed") +
                "; valid-to: " + (", ".join(sorted(tls_valid_to)) or "undisclosed") + ".",
                confidence=0.9, tags=("tls", "har"))
        ctx.edit("protocol", ", ".join(sorted(tls_protocols | tls_ciphers)) or "TLS profile",
                 "Pin or verify the certificate chain at the boundary; certificate rotation and "
                 "trust-store changes need the owner.",
                 risk="medium", confidence=0.8)
    ctx.learn("HTTP API")
    ctx.learn(*servers)
    ctx.source("Captured traffic (HAR)", path.name,
               f"{total} requests, exported by {creator.get('name') or 'unknown tool'}")
    detail = (f"Methods: {', '.join(f'{name} {count}' for name, count in methods.most_common(6))}. "
              f"Status: {', '.join(f'{name} {count}' for name, count in statuses.most_common(5))}. "
              f"Content: {', '.join(f'{name} {count}' for name, count in mimes.most_common(4)) or 'undisclosed'}. "
              f"Export: {creator.get('name') or 'unknown'} {creator.get('version') or ''}.".strip())
    if skipped:
        detail += f" {skipped} non-HTTP entr{'y' if skipped == 1 else 'ies'} skipped."
    ctx.add("traffic", f"{total} requests · {len(hosts)} host(s)", detail,
            path=path.name, confidence=0.95, tags=("har", "traffic"))
    if endpoints:
        top = [name for name, _ in endpoints.most_common(3)]
        ctx.facts["endpoints"].update(name.split(" ", 1)[1] for name in top)
        ctx.add("api", f"{len(endpoints)} distinct endpoint(s)",
                "; ".join(f"{name} ×{count}" for name, count in endpoints.most_common(10)),
                path=path.name, confidence=0.9, tags=("api", "har"))
        ctx.edit("protocol", "; ".join(top[:2]),
                 "Captured contract: generate a client or mock from these calls, then change behaviour in your adapter. "
                 "Do not replay captured credentials.",
                 risk="medium", confidence=0.9, requires=("authorised integration",))
    if auth_headers or credential_params:
        names = sorted(auth_headers | credential_params)
        ctx.add("auth", "Authentication shape captured",
                ", ".join(names) + " (names only; values are never stored or replayed).",
                confidence=0.9, tags=("auth", "har"))
        ctx.edit("protocol", ", ".join(names),
                 "Auth flow is visible in the capture: reproduce it with fresh, authorised credentials. "
                 "Captured tokens are evidence, not keys.",
                 risk="high", confidence=0.85)
    if len(hosts) > 1:
        primary = hosts.most_common(1)[0][0]
        others = [host for host, _ in hosts.most_common() if host != primary][:5]
        ctx.add("hosts", "Hosts contacted",
                ", ".join(f"{host} ({count})" for host, count in hosts.most_common(6)),
                confidence=0.85, tags=("hosts", "har"))
        if others:
            ctx.edit("policy", ", ".join(others),
                     "Third-party calls: confirm terms and stability before proxying or replacing them.",
                     risk="medium", confidence=0.65)
    if mutating:
        ctx.add("behaviour", f"{mutating} state-changing call(s)",
                "POST/PUT/PATCH/DELETE were captured; they define write behaviour and need a test account before replay.",
                confidence=0.85, tags=("behaviour", "har"))
    if graphql:
        ctx.learn("GraphQL")
        ctx.add("protocol", "GraphQL operations present",
                "A query-shaped request was captured; the operation names are the seam, not the transport.",
                confidence=0.8, tags=("protocol", "graphql"))
    if websocket:
        ctx.add("protocol", "WebSocket frames captured",
                "The capture includes websocket messages; treat frame shapes as the protocol contract.",
                confidence=0.8, tags=("protocol", "realtime"))
    if servers:
        ctx.add("runtime", "Response stack hints", ", ".join(sorted(servers)),
                confidence=0.7, tags=("runtime",))
    tls_note = f" · TLS {', '.join(sorted(tls_protocols))}" if tls_protocols else ""
    return {"status": "signal",
            "summary": f"{total} requests · {len(hosts)} host(s) · {len(endpoints)} endpoints{tls_note}."}


def p_web_fetch(ctx: Ctx) -> dict:
    result = ctx.http.get(ctx.url)
    if not result.get("ok") and not result.get("status"):
        return {"status": "error", "summary": result.get("error") or "fetch failed"}
    ctx.web = result
    body = result.get("body") or ""
    title_match = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
    title = re.sub(r"\s+", " ", title_match.group(1)).strip()[:120] if title_match else "(no title)"
    headers = result.get("headers") or {}
    ctx.learn(headers.get("server", ""))
    ctx.add("page", f"HTTP {result.get('status')} · {title}",
            f"Final URL: {result.get('url')}. Server: {headers.get('server', 'undisclosed')}. "
            f"Content type: {headers.get('content-type', 'unknown')}. "
            f"{human_size(len(result.get('raw') or b''))} received.",
            path=result.get("url") or ctx.url, confidence=0.9, tags=("http",))
    if not result.get("ok"):
        return {"status": "empty",
                "summary": f"HTTP {result.get('status')}: the page refused or errored."}
    return {"status": "signal", "summary": f"HTTP {result.get('status')} · {title}"}


def p_web_headers(ctx: Ctx) -> dict:
    if not ctx.web:
        return {"status": "empty", "summary": "No response headers captured."}
    headers = ctx.web.get("headers") or {}
    if not headers:
        return {"status": "empty", "summary": "Response exposed no headers."}
    csp = headers.get("content-security-policy", "")
    csp_ro = headers.get("content-security-policy-report-only", "")
    hsts = headers.get("strict-transport-security", "")
    cors = headers.get("access-control-allow-origin", "")
    powered = headers.get("x-powered-by", "")
    cookies = headers.get("set-cookie", "")
    observations = []
    risks = []
    if csp:
        observations.append("CSP present")
        if "unsafe-inline" in csp:
            observations.append("inline script allowed")
        if "frame-ancestors" not in csp:
            observations.append("framing not restricted")
    elif csp_ro:
        observations.append("CSP report-only (not enforced)")
        risks.append("CSP is not enforced")
    else:
        observations.append("no CSP")
        risks.append("no CSP: client-side injection surface is unconstrained")
    observations.append("HSTS " + ("present" if hsts else "absent"))
    if cors:
        observations.append(f"CORS {cors}")
        if cors == "*":
            risks.append("wildcard CORS")
    if powered:
        observations.append(f"X-Powered-By {powered}")
        risks.append("stack disclosure via X-Powered-By")
    ctx.add("headers", "Security and integration headers",
            "; ".join(observations), confidence=0.9, tags=("headers", "security"))
    if cookies:
        flags = [f for f in ("httponly", "secure", "samesite") if f in cookies.lower()]
        ctx.add("headers", "Cookie policy", ", ".join(flags) or "no protective flags",
                confidence=0.85, tags=("cookies",))
    if risks:
        ctx.add("headers", f"{len(risks)} integration constraint(s)", "; ".join(risks),
                confidence=0.8, tags=("security", "constraint"))
    ctx.facts["csp"] = "strict" if csp and "unsafe-inline" not in csp else "open" if not csp else "mixed"
    ctx.edit("policy", "response headers",
             "A strict CSP means no injected client script: use a supported extension point or the owner's API instead.",
             risk="blocked" if csp and "unsafe-inline" not in csp else "low",
             confidence=0.7, note="Editing headers requires control of the origin.")
    return {"status": "signal", "summary": "; ".join(observations[:4])}


def p_web_stack(ctx: Ctx) -> dict:
    if not ctx.web:
        return {"status": "empty", "summary": "No page body to fingerprint."}
    body = ctx.web.get("body") or ""
    headers = ctx.web.get("headers") or {}
    haystack = (body[:400_000] + "\n" + "\n".join(f"{k}: {v}" for k, v in headers.items())).lower()
    tech: list[str] = []
    for name, needles in TECH_SIGNATURES.items():
        if any(needle.lower() in haystack for needle in needles):
            tech.append(name)
    scripts = re.findall(r"""<script[^>]+src=["']([^"']+)["']""", body, re.I)
    links = re.findall(r"""<link[^>]+href=["']([^"']+)["']""", body, re.I)
    meta_gen = re.findall(r"""<meta[^>]+name=["']generator["'][^>]+content=["']([^"']+)""",
                          body, re.I)
    tech.extend(g for g in meta_gen if g)
    tech = list(dict.fromkeys(tech))
    ctx.learn(*tech)
    ctx.facts["tech"].update(tech)
    if not tech and not scripts:
        return {"status": "empty", "summary": "No framework fingerprint in the page."}
    ctx.add("stack", f"{len(tech)} stack signal(s)",
            (", ".join(tech) or "no named framework") +
            f". {len(scripts)} external script(s), {len(links)} linked asset(s).",
            confidence=0.8, tags=("stack",))
    if scripts:
        ctx.add("assets", "First-party script surface",
                ", ".join(s for s in scripts if s.startswith("/"))[:300] or ", ".join(scripts[:5]),
                confidence=0.85, tags=("assets", "javascript"))
        ctx.edit("content", "client bundles",
                 "Bundles are inspectable and can be extended at the edge; server behaviour still needs the owner's API.",
                 risk="medium", confidence=0.7)
    ctx.edit("content", ctx.url,
             "Third-party origin: change behaviour through a supported integration, extension or the owner's API.",
             risk="low" if tech else "medium", confidence=0.6,
             note="No server-side source access from this probe.")
    return {"status": "signal", "summary": ", ".join(tech) or f"{len(scripts)} scripts"}


def p_web_assets(ctx: Ctx) -> dict:
    if not ctx.web:
        return {"status": "empty", "summary": "No page body."}
    body = ctx.web.get("body") or ""
    scripts = re.findall(r"""<script[^>]+src=["']([^"']+)["']""", body, re.I)
    styles = re.findall(r"""<link[^>]+href=["']([^"']+)["']""", body, re.I)
    frames = re.findall(r"""<iframe[^>]+src=["']([^"']+)["']""", body, re.I)
    maps = re.findall(r"""sourceMappingURL=([^\s*]+)""", body)
    hosts = Counter()
    for url in scripts + styles + frames:
        host = urllib.parse.urlsplit(url).netloc or "same-origin"
        hosts[host] += 1
    if not (scripts or styles or frames or maps):
        return {"status": "empty", "summary": "No assets referenced in the HTML."}
    ctx.add("assets", f"{len(scripts)} scripts · {len(styles)} styles · {len(frames)} frames",
            "Hosts: " + ", ".join(f"{h} ({c})" for h, c in hosts.most_common(6)),
            confidence=0.85, tags=("assets",))
    if maps:
        ctx.add("assets", "Source maps published",
                ", ".join(maps[:4]), confidence=0.9, tags=("source-map",))
        ctx.edit("content", maps[0],
                 "Published source map exposes original client code: the strongest reading surface on this target.",
                 risk="low", confidence=0.85)
    if frames:
        ctx.add("integration", "Third-party frames",
                ", ".join(frames[:5]), confidence=0.8, tags=("integration",))
        ctx.edit("protocol", frames[0],
                 "Framed integration point: prefer the documented embed API over DOM scraping.",
                 risk="medium", confidence=0.6)
    return {"status": "signal", "summary": f"{len(scripts)} scripts across {len(hosts)} hosts."}


PUBLIC_PATHS = (
    "/robots.txt", "/sitemap.xml", "/openapi.json", "/swagger.json",
    "/api", "/.well-known/security.txt", "/manifest.json", "/humans.txt",
)


def p_web_endpoints(ctx: Ctx) -> dict:
    if not ctx.web:
        return {"status": "empty", "summary": "No page body to read endpoints from."}
    body = ctx.web.get("body") or ""
    base = ctx.web.get("url") or ctx.url
    links = re.findall(r"""<a[^>]+href=["']([^"'#]+)["']""", body, re.I)
    forms = re.findall(r"""<form[^>]*action=["']([^"']*)["'][^>]*""", body, re.I)
    inline_calls = sorted(set(FETCH_RE.findall(body)))
    ws = sorted(set(WS_RE.findall(body)))
    hosts: set[str] = set()
    host = urllib.parse.urlsplit(base).netloc
    for link in links:
        absolute = urllib.parse.urljoin(base, link)
        if urllib.parse.urlsplit(absolute).netloc == host:
            hosts.add(absolute.split("#", 1)[0])
    api_docs: list[str] = []
    checked: list[dict] = []
    for path in PUBLIC_PATHS:
        if ctx.http.requests >= MAX_REQUESTS - 5:
            break
        probe_url = urllib.parse.urljoin(base, path)
        result = ctx.http.get(probe_url, limit=200_000)
        checked.append({"path": path, "status": result.get("status") or 0})
        if not result.get("ok"):
            continue
        text = result.get("body") or ""
        if path in {"/openapi.json", "/swagger.json"}:
            data = safe_json(text)
            paths = list((data.get("paths") or {}).keys())[:12]
            if paths:
                api_docs.append(path)
                ctx.add("api", f"OpenAPI document · {len(paths)} paths",
                        ", ".join(paths), path=probe_url, confidence=0.95,
                        tags=("api", "openapi"))
                ctx.facts["endpoints"].update(paths)
                ctx.source("API description", probe_url)
                ctx.edit("protocol", probe_url,
                         "A machine-readable contract exists: generate a client and keep edits inside your adapter.",
                         risk="low", confidence=0.9)
        elif path == "/robots.txt":
            disallow = [ln.split(":", 1)[1].strip() for ln in text.splitlines()
                        if ln.lower().startswith("disallow:") and ln.split(":", 1)[1].strip()]
            if disallow:
                ctx.add("policy", "robots.txt disallows paths",
                        ", ".join(disallow[:8]), path=probe_url, confidence=0.9,
                        tags=("policy",))
                ctx.edit("policy", probe_url,
                         "Respect the crawl policy; request an authorised integration instead of scraping these paths.",
                         risk="blocked", confidence=0.85)
        elif path == "/sitemap.xml":
            urls = re.findall(r"<loc>([^<]+)</loc>", text)[:20]
            if urls:
                ctx.add("sitemap", f"{len(urls)} indexed URLs",
                        ", ".join(urls[:6]), path=probe_url, confidence=0.9,
                        tags=("sitemap",))
                ctx.source("Sitemap", probe_url)
        elif path == "/manifest.json":
            data = safe_json(text)
            if data:
                ctx.add("manifest", "Web app manifest",
                        f"Name: {data.get('name') or data.get('short_name') or 'unknown'}. "
                        f"Start: {data.get('start_url') or '/'}. Display: {data.get('display') or 'browser'}.",
                        path=probe_url, confidence=0.9, tags=("manifest", "pwa"))
                ctx.learn("PWA")
                ctx.edit("config", probe_url,
                         "PWA metadata is a supported change point for install/launch behaviour.",
                         risk="low", confidence=0.7)
    api_bases = sorted({urllib.parse.urljoin(base, call) for call in inline_calls
                        if call.startswith("/")})[:10]
    if api_bases:
        ctx.facts["endpoints"].update(api_bases)
        ctx.add("api", f"{len(api_bases)} client request path(s)",
                ", ".join(api_bases[:8]), confidence=0.85, tags=("api",))
        ctx.edit("protocol", ", ".join(api_bases[:3]),
                 "Observed client request: the highest-value seam. Record it, then mock or reimplement against it.",
                 risk="medium", confidence=0.8)
    if forms:
        ctx.add("forms", f"{len(forms)} form action(s)",
                ", ".join(sorted(set(forms))[:8]), confidence=0.8, tags=("forms",))
    ctx.add("links", f"{len(hosts)} same-origin link(s)",
            ", ".join(sorted(hosts)[:8]), confidence=0.8, tags=("links",))
    ctx.facts["checked_paths"] = checked
    if not (api_docs or api_bases or inline_calls or ws):
        return {"status": "empty",
                "summary": "No documented API or client request found; the page is presentation-only so far."}
    return {"status": "signal",
            "summary": f"{len(api_bases)} client paths, {len(api_docs)} API doc(s), {len(forms)} form(s)."}


def p_web_docs(ctx: Ctx) -> dict:
    if not ctx.web:
        return {"status": "empty", "summary": "No page to find documentation from."}
    body = ctx.web.get("body") or ""
    base = ctx.web.get("url") or ctx.url
    host = urllib.parse.urlsplit(base).netloc
    candidates: list[str] = []
    for href, text in re.findall(r"""<a[^>]+href=["']([^"']+)["'][^>]*>(.*?)</a>""", body, re.I | re.S):
        label = re.sub(r"<[^>]+>", "", text).strip().lower()
        if re.search(r"\b(docs?|api|developer|reference|support)\b", href + " " + label):
            url = urllib.parse.urljoin(base, href)
            if urllib.parse.urlsplit(url).netloc == host and url not in candidates:
                candidates.append(url)
    if not candidates:
        return {"status": "empty", "summary": "No documentation links on the landing page."}
    learned = 0
    for url in candidates[:3]:
        if ctx.http.requests >= MAX_REQUESTS:
            break
        result = ctx.http.get(url, limit=400_000)
        if not result.get("ok"):
            continue
        text = result.get("body") or ""
        headings = re.findall(r"<h[1-3][^>]*>(.*?)</h[1-3]>", text, re.I | re.S)[:8]
        headings = [re.sub(r"<[^>]+>|\s+", " ", h).strip()[:80] for h in headings if h.strip()]
        if not headings:
            continue
        learned += 1
        ctx.add("docs", f"Documentation · {url}", ", ".join(headings[:6]),
                path=url, confidence=0.8, tags=("docs",))
        ctx.source("Documentation", url)
        ctx.edit("protocol", url,
                 "Documented behaviour is the safe integration path; build against it rather than against internals.",
                 risk="low", confidence=0.75)
    if not learned:
        return {"status": "empty", "summary": "Documentation links did not yield readable pages."}
    return {"status": "signal", "summary": f"{learned} documentation page(s) mined."}


def p_source_github(ctx: Ctx) -> dict:
    if not ctx.network:
        return {"status": "skipped", "summary": "network disabled"}
    if not ctx.repo:
        return {"status": "empty", "summary": "No GitHub repository identified for this target."}
    meta_result = ctx.http.get(f"https://api.github.com/repos/{ctx.repo}", limit=400_000)
    if not meta_result.get("ok"):
        return {"status": "empty",
                "summary": f"GitHub API did not return the repo ({meta_result.get('error') or meta_result.get('status')})."}
    meta = safe_json(meta_result.get("body") or "")
    if not meta:
        return {"status": "empty", "summary": "GitHub returned no repository metadata."}
    license_info = meta.get("license") or {}
    spdx = str(license_info.get("spdx_id") or "").strip()
    archived = bool(meta.get("archived"))
    pushed = str(meta.get("pushed_at") or "")[:10]
    branch = str(meta.get("default_branch") or "main")
    ctx.repo = meta.get("full_name") or ctx.repo
    ctx.facts["repo"] = ctx.repo
    ctx.facts["license"] = spdx
    ctx.facts["archived"] = archived
    ctx.add("repo", f"{ctx.repo} · {meta.get('stargazers_count', 0)} stars",
            f"Language: {meta.get('language') or 'unknown'}. Licence: {spdx or 'none declared'}. "
            f"Default branch: {branch}. Last push: {pushed or 'unknown'}."
            f"{' Archived.' if archived else ''} Open issues: {meta.get('open_issues_count', 0)}.",
            path=meta.get("html_url") or ctx.url, confidence=0.95, tags=("github", "source"))
    ctx.source("GitHub repository", meta.get("html_url") or f"https://github.com/{ctx.repo}",
               "primary source")
    if spdx in PERMISSIVE:
        ctx.edit("source", f"{ctx.repo} ({spdx})",
                 "Permissive licence: fork, patch and ship a compatible implementation, keeping the notice.",
                 risk="low", confidence=0.9, requires=("licence notice retained",))
    elif spdx in COPYLEFT:
        ctx.edit("source", f"{ctx.repo} ({spdx})",
                 "Copyleft licence: a fork is allowed but derivative distribution inherits the licence.",
                 risk="medium", confidence=0.85, requires=("licence review",))
    else:
        ctx.edit("source", f"{ctx.repo}",
                 "Licence unclear: treat as all rights reserved until the owner grants permission.",
                 risk="blocked", confidence=0.8)
    tree_result = ctx.http.get(
        f"https://api.github.com/repos/{ctx.repo}/git/trees/{branch}?recursive=1", limit=1_200_000)
    tree = safe_json(tree_result.get("body") or "") if tree_result.get("ok") else {}
    paths = [str(item.get("path") or "") for item in (tree.get("tree") or [])
             if item.get("type") == "blob"]
    ctx.repo_files = paths[:500]
    if paths:
        ctx.add("tree", f"{len(paths)} repository files",
                ", ".join(paths[:10]), confidence=0.9, tags=("github", "tree"))
        exts = Counter(Path(p).suffix.lower() for p in paths)
        ctx.add("tree", "Language mix",
                ", ".join(f"{e or 'none'} ({c})" for e, c in exts.most_common(6)),
                confidence=0.85, tags=("github", "languages"))
        manifests = [p for p in paths if Path(p).name in
                     {"package.json", "pyproject.toml", "requirements.txt", "go.mod",
                      "Cargo.toml", "pom.xml", "build.gradle", "composer.json"}][:3]
        sources = [p for p in paths if Path(p).suffix.lower() in SOURCE_EXT][:3]
        for path in manifests + ["README.md"] + sources:
            if path not in paths and path != "README.md":
                continue
            if ctx.http.requests >= MAX_REQUESTS:
                break
            raw_url = f"https://raw.githubusercontent.com/{ctx.repo}/{branch}/{path}"
            result = ctx.http.get(raw_url, limit=300_000)
            if result.get("ok"):
                ctx.blobs[path] = result.get("body") or ""
        if ctx.blobs:
            ctx.add("source", f"{len(ctx.blobs)} file(s) pulled for reading",
                    ", ".join(ctx.blobs.keys()), confidence=0.9, tags=("github", "source"))
    return {"status": "signal",
            "summary": f"{ctx.repo} · {spdx or 'no licence'} · {len(paths)} files."}


def p_source_registry(ctx: Ctx) -> dict:
    if not ctx.network:
        return {"status": "skipped", "summary": "network disabled"}
    packages = list(dict.fromkeys(ctx.facts.get("packages") or []))[:4]
    if not packages:
        return {"status": "empty", "summary": "No package names to resolve."}
    learned = 0
    for name in packages:
        if ctx.http.requests >= MAX_REQUESTS - 2:
            break
        result = ctx.http.get(f"https://registry.npmjs.org/{urllib.parse.quote(name)}",
                              limit=300_000)
        if result.get("ok"):
            data = safe_json(result.get("body") or "")
            latest = str((data.get("dist-tags") or {}).get("latest") or "")
            version = (data.get("versions") or {}).get(latest) or {}
            license_value = str(version.get("license") or data.get("license") or "")
            if latest:
                learned += 1
                ctx.add("dependency", f"npm · {name}@{latest}",
                        f"Licence: {license_value or 'unknown'}. "
                        f"Repository: {(version.get('repository') or {}).get('url') if isinstance(version.get('repository'), dict) else version.get('repository') or 'not declared'}.",
                        path=f"https://www.npmjs.com/package/{name}", confidence=0.9,
                        tags=("npm", "dependency"))
                ctx.source("npm registry", f"https://www.npmjs.com/package/{name}")
                ctx.edit("dependency", f"{name}@{latest}",
                         "Vendor, pin or fork the package to change this behaviour at a stable boundary.",
                         risk="low" if license_value in PERMISSIVE else "medium",
                         confidence=0.75)
                continue
        if ctx.http.requests >= MAX_REQUESTS - 1:
            break
        result = ctx.http.get(f"https://pypi.org/pypi/{urllib.parse.quote(name)}/json",
                              limit=300_000)
        if not result.get("ok"):
            continue
        data = safe_json(result.get("body") or "")
        info = data.get("info") or {}
        if not info:
            continue
        learned += 1
        ctx.add("dependency", f"PyPI · {info.get('name')} {info.get('version')}",
                f"Licence: {info.get('license') or 'unknown'}. "
                f"Home: {info.get('home_page') or info.get('project_url') or 'not declared'}.",
                path=f"https://pypi.org/project/{name}/", confidence=0.9,
                tags=("pypi", "dependency"))
        ctx.source("PyPI", f"https://pypi.org/project/{name}/")
    if not learned:
        return {"status": "empty", "summary": "Packages not found in npm or PyPI."}
    return {"status": "signal", "summary": f"{learned} package provenance record(s)."}


def p_net_local(ctx: Ctx) -> dict:
    """Read this host's WiFi state, interfaces, gateway and DNS (passive)."""
    info = discovery.local_network()
    wifi = info.get("wifi") or {}
    ips = list(info.get("local_ips") or [])
    gateway = str(info.get("gateway") or "")
    dns = list(info.get("dns") or [])
    hostname = str(info.get("hostname") or "unknown host")
    platform_name = str(info.get("platform") or "unknown platform")
    ctx.facts.update({
        "wifi": wifi, "local_ips": ips, "gateway": gateway, "dns": dns,
        "hostname": hostname, "host_platform": platform_name,
    })
    if wifi.get("ssid") or wifi.get("bssid"):
        parts = [
            f"state {wifi['state']}" if wifi.get("state") else "",
            f"signal {wifi['signal']}" if wifi.get("signal") else "",
            f"channel {wifi['channel']}" if wifi.get("channel") else "",
            f"radio {wifi['radio']}" if wifi.get("radio") else "",
            f"auth {wifi['auth']}" if wifi.get("auth") else "",
            f"BSSID {wifi['bssid']}" if wifi.get("bssid") else "",
        ]
        ctx.add("wifi", f"WiFi · {wifi.get('ssid') or 'hidden network'}",
                "; ".join(part for part in parts if part),
                confidence=0.9, tags=("wifi", "network"))
    ctx.add("network", f"This device · {hostname}",
            f"{platform_name}. IPv4: {', '.join(ips) or 'none'}. "
            f"Gateway: {gateway or 'not exposed'}. "
            f"DNS: {', '.join(dns) or 'not exposed'}.",
            confidence=0.95, tags=("host", "network"))
    if ips:
        ctx.edit("network", f"local host {ips[0]}",
                 "This host is the vantage point: change its DNS, firewall or proxy "
                 "settings before touching other devices on the LAN.",
                 risk="low", confidence=0.6)
    if gateway:
        ctx.edit("network", f"gateway {gateway}",
                 "The gateway is the control point for DHCP reservations, DNS filtering, "
                 "guest isolation and router firmware on this network.",
                 risk="low", confidence=0.75, requires=("router admin access",))
    if not (wifi or ips or gateway):
        return {"status": "empty",
                "summary": "No WiFi adapter or IPv4 interface was exposed to this process."}
    summary = [wifi.get("ssid") or "", wifi.get("signal") and f"signal {wifi['signal']}",
               gateway and f"gateway {gateway}"]
    return {"status": "signal",
            "summary": " · ".join(str(part) for part in summary if part) or "Local interfaces read."}


def p_net_devices(ctx: Ctx) -> dict:
    """Map LAN devices from the neighbour cache plus one bounded ICMP sweep."""
    facts = ctx.facts
    local_ips = list(facts.get("local_ips") or [])
    subnet = discovery.lan_subnet(ctx.target, local_ips, facts.get("gateway") or "")
    before = discovery.collect_arp()
    responded: list[str] = []
    attempted = 0
    if subnet is not None:
        facts["subnet"] = str(subnet)
        responded, attempted = discovery.ping_sweep(subnet)
        if attempted:
            time.sleep(0.12)
    after = discovery.collect_arp()
    merged: dict[str, dict] = {}
    for item in before + after:
        merged[item["ip"]] = item
    for ip in responded:
        merged.setdefault(ip, {"ip": ip, "mac": "", "state": "answered ICMP",
                               "vendor": ""})
    own = set(local_ips)
    gateway = str(facts.get("gateway") or "")
    devices: list[dict] = []
    for ip, item in merged.items():
        if ip in own:
            continue
        if subnet is not None:
            try:
                if ipaddress.ip_address(ip) not in subnet:
                    continue
            except ValueError:
                continue
        vendor = str(item.get("vendor") or "")
        devices.append({
            "ip": ip,
            "mac": str(item.get("mac") or ""),
            "state": str(item.get("state") or "seen"),
            "vendor": vendor,
            "name": "gateway" if ip == gateway else (vendor or "device"),
            "answered": ip in responded,
        })
    devices.sort(key=lambda item: tuple(int(part) for part in item["ip"].split(".")))
    devices = devices[:64]
    facts["devices"] = devices
    facts["sweep_attempted"] = attempted
    facts["sweep_responded"] = len(responded)
    if not devices:
        return {"status": "empty",
                "summary": f"No devices found in the neighbour table"
                           f"{' or sweep' if attempted else ''}."}
    for device in devices[:48]:
        detail = f"MAC {device['mac'] or 'not in neighbour table'} · {device['state']}"
        if device["vendor"]:
            detail += f" · vendor hint {device['vendor']}"
        if device["answered"]:
            detail += " · answered ICMP"
        ctx.add("device", f"{device['ip']} · {device['name']}", detail,
                path=f"lan://{device['ip']}",
                confidence=0.9 if device["mac"] else 0.65,
                tags=("device", "network"))
    unknown = [d for d in devices if not d["vendor"] or d["vendor"] == "randomised MAC"]
    if unknown:
        ctx.add("network", f"{len(unknown)} device(s) without a vendor hint",
                "Randomised MACs or an OUI outside the offline table. Check router DHCP "
                "leases before assuming what a device is.",
                confidence=0.7, tags=("device", "network"))
    ctx.edit("network", f"{len(devices)} device(s) on {subnet or 'this LAN'}",
             "Router-side controls are the safe seam: DHCP reservations, DNS filtering, "
             "guest-network isolation and firmware updates. Do not probe device ports.",
             risk="low", confidence=0.75, requires=("router admin access",))
    summary = f"{len(devices)} device(s) on {subnet or 'this LAN'}"
    if attempted:
        summary += f" · {len(responded)} answered sweep"
    return {"status": "signal", "summary": summary}


def p_net_egress(ctx: Ctx) -> dict:
    """Read the public egress identity with one lookup."""
    service = Http(authorized=True, budget=2)
    result = service.get("https://ipwho.is/", limit=120_000)
    data = safe_json(result.get("body") or "") if result.get("ok") else {}
    if data and data.get("success") is False:
        return {"status": "empty",
                "summary": f"Public IP lookup refused: {data.get('message') or 'rate limited'}"}
    ip = str(data.get("ip") or "")
    if not ip:
        return {"status": "error",
                "summary": f"Public IP lookup failed: "
                           f"{result.get('error') or result.get('status') or 'no answer'}"}
    connection = data.get("connection") or {}
    egress = {
        "ip": ip,
        "isp": str(connection.get("isp") or ""),
        "org": str(connection.get("org") or ""),
        "asn": str(connection.get("asn") or ""),
        "city": str(data.get("city") or ""),
        "region": str(data.get("region") or ""),
        "country": str(data.get("country") or ""),
        "timezone": str((data.get("timezone") or {}).get("id") or ""),
    }
    ctx.facts["public_ip"] = ip
    ctx.facts["egress"] = egress
    location = ", ".join(part for part in (egress["city"], egress["country"]) if part)
    detail = (f"ISP: {egress['isp'] or 'undisclosed'}"
              f"{' · ASN ' + egress['asn'] if egress['asn'] else ''}"
              f"{' · ' + location if location else ''}"
              f"{' · ' + egress['timezone'] if egress['timezone'] else ''}")
    ctx.add("network", f"Public identity · {ip}", detail,
            confidence=0.95, tags=("internet", "egress"))
    ctx.source("Public IP lookup", "https://ipwho.is/", "coarse egress identity only")
    ctx.edit("network", f"egress {ip}",
             "Your public identity is the ISP edge: change exposure through the router "
             "firewall and hosting DNS, not on this machine alone.",
             risk="medium", confidence=0.6, requires=("ISP/router admin",))
    return {"status": "signal", "summary": f"Public IP {ip}"
            f"{' · ' + egress['isp'] if egress['isp'] else ''}"}


def p_net_exposure(ctx: Ctx) -> dict:
    """Read DNS, TLS and HTTP edge posture for one named public host."""
    raw = str(ctx.target or "").strip()
    if _egress_target(raw):
        return {"status": "empty",
                "summary": "No host supplied; this run checks the egress identity instead."}
    parts = urllib.parse.urlsplit(raw if "://" in raw else f"//{raw}")
    host = (parts.hostname or "").strip().lower()
    if not host:
        return {"status": "empty", "summary": "No host could be read from the target."}
    scheme = parts.scheme if parts.scheme in {"http", "https"} else "https"
    port = parts.port or (443 if scheme == "https" else 80)
    netloc = parts.netloc or host
    ips, resolve_error = discovery.resolve(host)
    tls = discovery.tls_info(host, port if scheme == "https" else 443)
    url = f"{scheme}://{netloc}/"
    result = ctx.http.get(url, limit=400_000)
    headers = result.get("headers") or {}
    if ips:
        ctx.add("dns", f"{host} resolves to {len(ips)} address(es)", ", ".join(ips),
                path=host, confidence=0.95, tags=("dns", "internet"))
    if tls.get("ok"):
        days = tls.get("days_left")
        expiry = f"expires {tls.get('expires') or 'unknown'}"
        if days is not None:
            expiry += f" ({days} days)"
        ctx.add("tls", f"TLS · {tls.get('issuer') or host}",
                f"{'verified' if tls.get('verified') else 'NOT verified'}; {expiry}; "
                f"{tls.get('tls_version') or 'unknown TLS'}; cipher {tls.get('cipher') or 'unknown'}; "
                f"{len(tls.get('sans') or [])} subject alternative name(s).",
                path=host, confidence=0.95, tags=("tls", "internet"))
        if not tls.get("verified"):
            ctx.add("warning", "TLS certificate is not trusted",
                    tls.get("error") or "verification failed",
                    confidence=0.85, tags=("tls", "warning"))
    elif tls.get("error"):
        ctx.add("warning", "TLS handshake failed", tls["error"],
                confidence=0.8, tags=("tls", "warning"))
    issues: list[str] = []
    observations: list[str] = []
    if tls.get("ok"):
        if not tls.get("verified"):
            issues.append("certificate not trusted")
        if tls.get("days_left") is not None and tls["days_left"] < 30:
            issues.append(f"certificate expires in {tls['days_left']} days")
    if result.get("ok") or result.get("status"):
        final_url = str(result.get("url") or url)
        observations.append(f"HTTP {result.get('status')} at {final_url}")
        if headers.get("server"):
            observations.append(f"server {headers['server']}")
            issues.append("server stack disclosed")
        if headers.get("strict-transport-security"):
            observations.append("HSTS present")
        else:
            observations.append("HSTS absent")
            issues.append("HSTS absent")
        if headers.get("content-security-policy"):
            observations.append("CSP present")
        else:
            observations.append("CSP absent")
            issues.append("no CSP")
        if headers.get("access-control-allow-origin") == "*":
            issues.append("wildcard CORS")
        if headers.get("x-powered-by"):
            observations.append(f"X-Powered-By {headers['x-powered-by']}")
            issues.append("stack disclosed via X-Powered-By")
        ctx.add("edge", f"Edge · HTTP {result.get('status')}",
                " · ".join(observations), path=final_url,
                confidence=0.9, tags=("http", "internet"))
    else:
        ctx.add("warning", "HTTPS request failed",
                str(result.get("error") or "no response"),
                confidence=0.75, tags=("http", "warning"))
        issues.append("HTTPS did not answer")
    port80_note = ""
    if scheme == "https" and port == 443:
        port80 = ctx.http.get(f"http://{netloc}/", limit=150_000)
        if port80.get("ok") or port80.get("status"):
            final80 = str(port80.get("url") or "")
            if final80.lower().startswith("https://"):
                port80_note = f"port 80 redirects to {final80}"
            else:
                port80_note = f"port 80 serves HTTP {port80.get('status')} without redirecting"
                issues.append("HTTP on port 80 does not redirect to HTTPS")
        else:
            port80_note = "port 80 did not answer"
        observations.append(port80_note)
    if issues:
        ctx.add("exposure", f"{len(issues)} exposure observation(s)",
                "; ".join(dict.fromkeys(issues)),
                confidence=0.85, tags=("exposure", "internet"))
    ctx.facts["exposure"] = {
        "host": host,
        "ips": ips,
        "scheme": scheme,
        "port": port,
        "tls": {key: tls.get(key) for key in (
            "ok", "verified", "issuer", "subject", "expires", "days_left",
            "tls_version", "cipher", "sans", "error")},
        "http": {
            "status": result.get("status") or 0,
            "url": result.get("url") or url,
            "server": headers.get("server", ""),
            "hsts": bool(headers.get("strict-transport-security")),
            "csp": bool(headers.get("content-security-policy")),
            "cors": headers.get("access-control-allow-origin", ""),
            "powered_by": headers.get("x-powered-by", ""),
            "port80": port80_note,
        },
    }
    ctx.edit("network", f"DNS/hosting for {host}",
             "Internet exposure is owned at the DNS/hosting edge: change records, "
             "TLS and edge headers with the owner rather than probing the host's ports.",
             risk="low", confidence=0.7, requires=("DNS/hosting admin",))
    if tls.get("ok"):
        ctx.edit("network", f"TLS certificate for {host}",
                 "Renew or replace the certificate before expiry; certificate transparency "
                 "is the public record of this change.",
                 risk="low", confidence=0.8, requires=("certificate control",))
    ctx.edit("policy", f"edge headers for {host}",
             "HSTS, CSP and CORS are set at the origin or CDN: they are the safe, "
             "supported way to change browser exposure.",
             risk="medium", confidence=0.7, requires=("origin/CDN control",))
    if not ips and not tls.get("ok") and not result.get("status"):
        return {"status": "error",
                "summary": f"{host} did not resolve or answer: "
                           f"{resolve_error or result.get('error') or 'no response'}"}
    summary = f"{host} · {len(ips)} address(es)"
    if tls.get("ok"):
        summary += f" · TLS {tls.get('tls_version') or ''}".rstrip()
    if result.get("status"):
        summary += f" · HTTP {result.get('status')}"
    return {"status": "signal" if (ips or tls.get("ok") or result.get("status")) else "empty",
            "summary": summary}


PROBES: dict[str, dict] = {}


def _register(pid: str, family: str, label: str, description: str, kinds: set[str],
              run, empty_next: tuple = (), network: bool = False) -> None:
    PROBES[pid] = {
        "id": pid, "family": family, "label": label, "description": description,
        "kinds": kinds, "run": run, "empty_next": empty_next, "network": network,
    }


_register("surface.scan", "Surface", "Map the surface", "Inventory files, size, languages and binary candidates.", {"folder", "file"}, p_surface_scan, ("manifest.read", "binary.header"))
_register("manifest.read", "Surface", "Read manifests and config", "Parse package manifests, app metadata and environment contracts.", {"folder", "file", "repo"}, p_manifest_read, ("source.github", "source.registry", "binary.strings"))
_register("code.structure", "Code", "Map code structure", "Find modules, routes, imports, flags and marked change sites.", {"folder", "repo"}, p_code_structure, ("api.surface", "binary.strings"))
_register("api.surface", "Protocol", "Map request surface", "Extract hosts, client calls, websockets and auth shape from source.", {"folder", "repo"}, p_api_surface, ("web.endpoints", "binary.strings"))
_register("git.history", "History", "Read repository history", "Read remotes, recent commits and highest-churn files.", {"folder"}, p_git_history, ("source.github",))
_register("docs.mine", "Docs", "Mine documentation", "Extract headings, commands and stated claims from README/docs.", {"folder", "repo"}, p_docs_mine, ("web.docs", "source.github"))
_register("test.surface", "Verification", "Find the test harness", "Locate tests and CI so a change can be checked.", {"folder", "repo"}, p_test_surface, ("docs.mine",))
_register("binary.header", "Binary", "Parse the artifact", "Identify PE/ELF/Mach-O/APK/SquashFS and map writable or packed regions.", {"folder", "file"}, p_binary_header, ("binary.strings", "device.firmware"))
_register("binary.strings", "Binary", "Extract strings", "Pull URLs, versions, runtime fingerprints, paths and error text.", {"folder", "file"}, p_binary_strings, ("device.firmware",))
_register("binary.controls", "Controls", "Map security controls", "Signatures, secure/measured boot, rollback counters and writable/packed regions; no bypass.", {"folder", "file"}, p_binary_controls, ("binary.strings", "device.firmware"))
_register("device.firmware", "Firmware", "Map firmware containers", "Locate rootfs magic, partition offsets and repack surfaces.", {"folder", "file"}, p_device_firmware, ("binary.header",))
_register("traffic.har", "Traffic", "Read captured traffic", "Parse an operator-supplied HAR: methods, hosts, endpoints, auth shape and status classes.", {"har"}, p_traffic_har)
_register("web.fetch", "Web", "Fetch the page", "One passive request: status, redirects, headers and body.", {"website"}, p_web_fetch, ("web.headers", "web.docs"), network=True)
_register("web.headers", "Web", "Read security posture", "CSP, HSTS, CORS, cookies and stack disclosure.", {"website"}, p_web_headers, ("web.stack",), network=True)
_register("web.stack", "Web", "Fingerprint the stack", "Detect frameworks, generators and first-party scripts.", {"website"}, p_web_stack, ("web.assets",), network=True)
_register("web.assets", "Web", "Inspect assets", "Map scripts, styles, frames and published source maps.", {"website"}, p_web_assets, ("web.endpoints",), network=True)
_register("web.endpoints", "Protocol", "Discover endpoints", "Read links, forms, client calls, robots and API descriptions.", {"website"}, p_web_endpoints, ("web.docs",), network=True)
_register("web.docs", "Docs", "Mine site documentation", "Follow documented developer/API pages for stated behaviour.", {"website"}, p_web_docs, ("source.github",), network=True)
_register("source.github", "Sources", "Learn from GitHub", "Repository metadata, licence, tree and raw source for cross-checking.", {"repo", "website", "folder"}, p_source_github, ("source.registry", "web.fetch"), network=True)
_register("source.registry", "Sources", "Check package provenance", "Resolve discovered packages against npm and PyPI.", {"repo", "website", "folder"}, p_source_registry, ("source.github",), network=True)
_register("net.local", "Network", "Read WiFi and this device", "SSID, interface addresses, gateway, DNS and neighbour table; no packets sent.", {"wifi"}, p_net_local, ("net.devices",), network=True)
_register("net.devices", "Network", "Map devices on this LAN", "Bounded ICMP sweep of the private /24 plus the ARP/neighbour cache; no port scan.", {"wifi"}, p_net_devices, ("net.local",), network=True)
_register("net.egress", "Internet", "Read public identity", "Egress IP, ISP, ASN and coarse location from one public lookup.", {"internet"}, p_net_egress, (), network=True)
_register("net.exposure", "Internet", "Check internet exposure", "DNS, TLS certificate, HTTP/HTTPS headers and port 80 redirect for a named host.", {"internet"}, p_net_exposure, (), network=True)

ORDER = {
    "har": ["traffic.har"],
    "wifi": ["net.local", "net.devices"],
    "internet": ["net.egress", "net.exposure"],
    "folder": ["surface.scan", "manifest.read", "code.structure", "api.surface",
               "git.history", "docs.mine", "test.surface", "binary.header",
               "binary.controls", "binary.strings", "device.firmware"],
    "file": ["surface.scan", "manifest.read", "binary.header", "binary.controls",
             "binary.strings", "device.firmware", "docs.mine", "source.github"],
    "repo": ["source.github", "manifest.read", "code.structure", "api.surface",
             "docs.mine", "test.surface", "source.registry"],
    "website": ["web.fetch", "web.headers", "web.stack", "web.assets",
                "web.endpoints", "web.docs", "source.github", "source.registry"],
}

NEXT_HINTS = {
    "net.local": "No WiFi adapter visible: check the adapter is enabled and run ipconfig /all or ip addr directly, then retry.",
    "net.devices": "No devices seen: client isolation or ICMP filtering can hide them. Check the router's DHCP lease table, then retry.",
    "net.egress": "Public IP lookup failed: check outbound HTTPS or read the public address from the router status page.",
    "net.exposure": "The host did not resolve or answer: confirm the spelling and check DNS/hosting status before re-running.",
    "traffic.har": "HAR empty or unreadable: export again with Preserve log enabled and include XHR/fetch, then retry.",
    "surface.scan": "Nothing readable: confirm the path and permissions, then try a container-level read.",
    "manifest.read": "No manifest: read the binary import table and strings to infer the runtime.",
    "code.structure": "Structure hidden: use symbol/string extraction and module names to reconstruct the layout.",
    "api.surface": "No request sites in source: capture runtime traffic (HAR or local proxy) and diff the calls.",
    "git.history": "No history: identify the upstream from strings or a remote config, then check GitHub.",
    "docs.mine": "No docs: infer intent from UI labels, error strings and observed behaviour.",
    "test.surface": "No harness: record a baseline behaviour script before changing anything.",
    "binary.header": "Unrecognised container: carve partitions at magic offsets and check for a bootloader at 0x0.",
    "binary.strings": "Strings unhelpful: try disassembly or a runtime import trace instead of static text.",
    "device.firmware": "No rootfs signature: scan for partition tables and compressed streams at 4 KiB boundaries.",
    "web.fetch": "Refused or errored: record status, try a plain HEAD, then use a passive client capture.",
    "web.headers": "Headers absent: fingerprint from the body and timing instead.",
    "web.stack": "No framework signature: inspect bundle names and the runtime global object.",
    "web.assets": "No assets: the page may be server-rendered; go straight to request discovery.",
    "web.endpoints": "No documented API: capture the client's own requests and import the HAR.",
    "web.docs": "Docs unreadable: use the sitemap and the client bundle for named behaviour.",
    "source.github": "No public repo: search GitHub for unique strings from the target to locate upstream.",
    "source.registry": "Package unresolved: treat it as vendored and inspect the local copy.",
}


def _probe_meta(pid: str, include_run: bool = False) -> dict:
    meta = dict(PROBES[pid])
    meta["empty_next"] = list(meta["empty_next"])
    meta["kinds"] = sorted(meta["kinds"])
    if not include_run:
        meta.pop("run", None)
    return meta


def probe_catalogue() -> list[dict]:
    return [_probe_meta(pid) for pid in PROBES]


# --------------------------------------------------------------- analysis --

def _fingerprint(target: str) -> str:
    return hashlib.sha1(target.encode("utf-8")).hexdigest()[:8]


def _mode(mode: str, available: bool, detail: str, risk: str, evidence: int) -> dict:
    return {"mode": mode, "available": bool(available), "detail": detail,
            "risk": risk, "evidence": evidence}


def derive(ctx: Ctx, inv: dict) -> None:
    facts = ctx.facts
    findings = ctx.findings
    source_files = int(facts.get("source_files") or 0)
    binaries = bool(ctx.inventory()["binaries"]) if ctx.local_root else bool(facts.get("binary"))
    license_value = str(facts.get("license") or "")
    endpoints = sorted(facts.get("endpoints") or [])
    tech = sorted(facts.get("tech") or [])
    signals = sum(1 for p in inv["probes"] if p["status"] == "signal")

    modes = [
        _mode("Source", source_files > 0,
              f"{source_files} source files readable; patch the smallest module." if source_files
              else "No readable source at this target.",
              "low" if source_files else "blocked", source_files),
        _mode("Configuration", bool(facts.get("flags") or facts.get("config")),
              "Supported switches and manifests can change behaviour without touching code."
              if (facts.get("flags") or facts.get("config")) else
              "No supported configuration surface confirmed yet.",
              "low", len(facts.get("flags") or [])),
        _mode("Binary", binaries,
              "Compiled artifact: patch or hook, mindful of signature and integrity checks." if binaries
              else "No compiled artifact at this target.",
              "high" if binaries else "blocked", 1 if binaries else 0),
        _mode("Protocol", bool(endpoints),
              f"{len(endpoints)} request surface(s): reimplement or extend the client."
              if endpoints else "No protocol surface mapped yet.",
              "medium", len(endpoints)),
        _mode("Edge content", bool(ctx.web) or inv["kind"] == "website",
              "Change client-side behaviour at the edge (extension/userscript) where the origin allows it."
              if (ctx.web or inv["kind"] == "website") else "No web/client surface at this target.",
              "medium" if (ctx.web or inv["kind"] == "website") else "blocked", len(tech)),
        _mode("Firmware", str(inv.get("kind")) in {"file", "folder"} and
              any("firmware" in f["tags"] for f in findings),
              "Rootfs repack or boot-chain change; requires a recovery path." if
              any("firmware" in f["tags"] for f in findings) else
              "No firmware container confirmed.",
              "high", sum(1 for f in findings if "firmware" in f["tags"])),
        _mode("Control map", bool(facts.get("controls")),
              (f"{len(facts.get('controls') or [])} control indicator(s): " +
               ", ".join(sorted(set(facts.get("controls") or []))[:6]) +
               "; map and respect them.") if facts.get("controls") else
              "No signature or boot-control indicators confirmed.",
              "high" if facts.get("controls") else "blocked",
              len(facts.get("controls") or [])),
        _mode("Encrypted edge", bool(facts.get("tls")),
              (f"{len((facts.get('tls') or {}).get('protocols') or [])} TLS profile(s) mapped; "
               "certificate rotation and pinning are owner-controlled.") if facts.get("tls") else
              "No TLS metadata in this capture.",
              "medium", len((facts.get("tls") or {}).get("protocols") or [])),
        _mode("Fork", bool(facts.get("repo")) and license_value in PERMISSIVE,
              f"Licence {license_value}: fork and maintain a compatible implementation."
              if license_value in PERMISSIVE else
              (f"Licence {license_value or 'unknown'}: fork needs permission."
               if facts.get("repo") else "No upstream repository identified."),
              "low" if license_value in PERMISSIVE else "blocked",
              1 if facts.get("repo") else 0),
    ]
    devices = list(facts.get("devices") or [])
    if inv["kind"] == "wifi":
        modes.append(_mode(
            "Network", bool(devices or facts.get("wifi")),
            (f"{len(devices)} device(s) observed on {facts.get('subnet') or 'this LAN'}; "
             "router-side DHCP, DNS and guest isolation are the low-risk change surfaces.")
            if devices else
            ("WiFi and interface context read; no device list was available."
             if facts.get("wifi") else "No WiFi/interface context was readable."),
            "low" if devices else "medium", len(devices)))
    if inv["kind"] == "internet":
        exposure = facts.get("exposure") or {}
        modes.append(_mode(
            "Internet edge", bool(exposure or facts.get("public_ip")),
            (f"{exposure.get('host') or 'Egress'} maps to "
             f"{len(exposure.get('ips') or [])} address(es); DNS, TLS and edge headers "
             "are changed with the hosting/DNS owner.")
            if exposure else
            (f"Egress identity {facts.get('public_ip')} read; no host exposure was requested."
             if facts.get("public_ip") else "No public identity or host exposure was readable."),
            "medium", len(exposure.get("ips") or [])))
    available_modes = [m for m in modes if m["available"]]

    hints = [m["mode"] for m in available_modes]
    if findings:
        verdict = (f"{len(ctx.edits)} change surface(s) mapped across "
                   f"{len(available_modes)} access mode(s): {', '.join(hints) or 'none'}.")
    else:
        verdict = "No confirmed change surface yet; the ladder records exactly where to probe next."
    confidence = round(min(0.95, 0.25 + 0.6 * (signals / max(1, len(inv["probes"])))), 2)

    moves: list[dict] = []
    seen: set[str] = set()

    def add_move(text: str, because: str, probe: str = "") -> None:
        if text in seen or len(moves) >= 7:
            return
        seen.add(text)
        moves.append({"move": text, "because": because, "probe": probe})

    for record in inv["probes"]:
        if record["status"] in {"empty", "error"}:
            add_move(NEXT_HINTS.get(record["id"], "Try a different probe family for this signal."),
                     f"{record['label']} returned {record['status']}",
                     record["id"])
    for warning in [f for f in findings if f["kind"] == "warning"]:
        add_move("Install or expose the missing tool, then re-run this probe.",
                 warning["title"], warning["probe"])
    if facts.get("signed"):
        add_move("Signed artifact: plan a runtime hook or signature-preserving build instead of a byte patch.",
                 "Authenticode certificate table detected")
    if facts.get("repo") and license_value not in PERMISSIVE and license_value not in COPYLEFT:
        add_move("Confirm the licence and written permission before reusing any code from this repository.",
                 "Licence is unclear", "source.github")
    if facts.get("csp") == "strict":
        add_move("Strict CSP blocks injected client script: ask for an extension point or use the owner's API.",
                 "Content-Security-Policy is enforced")
    if not ctx.sources and inv["kind"] in {"repo", "website"}:
        add_move("Cross-check unique strings and bundle names against GitHub search to find the upstream project.",
                 "No primary source located")
    if not moves:
        add_move("Record a baseline of the current behaviour, then make one reversible change and compare.",
                 "No dead ends recorded")

    graph = build_graph(inv, ctx, tech, endpoints)
    inv["modes"] = modes
    inv["next_moves"] = moves
    inv["graph"] = graph
    inv["summary"] = {
        "verdict": verdict,
        "confidence": confidence,
        "kind_label": {"folder": "Local source tree", "file": "Local artifact",
                       "repo": "GitHub repository", "website": "Website",
                       "har": "Captured traffic (HAR)", "wifi": "WiFi & LAN devices",
                       "internet": "Internet exposure"}.get(inv["kind"], inv["kind"]),
        "counts": {
            "probes": len(inv["probes"]),
            "signal": signals,
            "empty": sum(1 for p in inv["probes"] if p["status"] == "empty"),
            "error": sum(1 for p in inv["probes"] if p["status"] == "error"),
            "skipped": sum(1 for p in inv["probes"] if p["status"] == "skipped"),
            "retries": sum(1 for p in inv["probes"] if p.get("retry_of")),
            "findings": len(findings),
            "edits": len(ctx.edits),
            "sources": len(ctx.sources),
            "modes": len(available_modes),
        },
        "headline": [
            {"label": "Access modes", "value": ", ".join(hints) or "none confirmed"},
            {"label": "Change surfaces", "value": str(len(ctx.edits))},
            {"label": "Evidence", "value": f"{len(findings)} findings"},
            {"label": "Next", "value": (moves[0]["move"] if moves else "")[:90]},
        ],
    }
    headline = inv["summary"]["headline"]
    wifi = facts.get("wifi") or {}
    if wifi.get("ssid"):
        headline.append({"label": "WiFi", "value": wifi["ssid"]})
    if devices:
        headline.append({"label": "LAN devices", "value": str(len(devices))})
    if facts.get("public_ip"):
        headline.append({"label": "Public IP", "value": facts["public_ip"]})
    exposure = facts.get("exposure") or {}
    if exposure.get("host"):
        headline.append({"label": "Exposed host", "value": exposure["host"]})
    inv["facts"] = {
        "languages": sorted(facts.get("languages") or []),
        "tech": tech,
        "endpoints": endpoints[:20],
        "packages": (facts.get("packages") or [])[:20],
        "repo": facts.get("repo") or "",
        "license": license_value,
        "signed": bool(facts.get("signed")),
        "controls": sorted(set(facts.get("controls") or [])),
        "tls": facts.get("tls") or {},
        "has_tests": bool(facts.get("has_tests")),
        "checked_paths": facts.get("checked_paths") or [],
        "wifi": facts.get("wifi") or {},
        "devices": (facts.get("devices") or [])[:64],
        "local_ips": facts.get("local_ips") or [],
        "gateway": facts.get("gateway") or "",
        "dns": facts.get("dns") or [],
        "subnet": facts.get("subnet") or "",
        "sweep_attempted": int(facts.get("sweep_attempted") or 0),
        "sweep_responded": int(facts.get("sweep_responded") or 0),
        "public_ip": facts.get("public_ip") or "",
        "egress": facts.get("egress") or {},
        "exposure": facts.get("exposure") or {},
    }


def build_graph(inv: dict, ctx: Ctx, tech: list[str], endpoints: list[str]) -> dict:
    nodes: list[dict] = [{"id": "target", "label": Path(ctx.target).name or ctx.target,
                          "type": "target", "detail": ctx.target}]
    ids: set[str] = {"target"}

    def add_node(label: str, kind: str, detail: str = "") -> str:
        safe = re.sub(r"[^a-z0-9]+", "-", f"{kind}-{label}".lower()).strip("-")[:60]
        node_id = safe or f"{kind}-{len(nodes)}"
        suffix = 2
        base = node_id
        while node_id in ids:
            node_id = f"{base}-{suffix}"
            suffix += 1
        ids.add(node_id)
        nodes.append({"id": node_id, "label": label[:40], "type": kind,
                      "detail": detail[:200]})
        return node_id

    edges: list[dict] = []

    def link(source: str, target: str, relation: str) -> None:
        if source in ids and target in ids and len(edges) < 90:
            edges.append({"from": source, "to": target, "relation": relation})

    for name in tech[:6]:
        node = add_node(name, "tech", "fingerprint")
        link("target", node, "runs")
    if ctx.inventory()["files"] and ctx.local_root:
        top_dirs = Counter()
        for path in ctx.inventory()["files"]:
            try:
                rel = path.relative_to(ctx.local_root)
            except ValueError:
                continue
            if len(rel.parts) > 1:
                top_dirs[rel.parts[0]] += 1
        for name, count in top_dirs.most_common(5):
            node = add_node(name, "asset", f"{count} files")
            link("target", node, "contains")
    for name in endpoints[:8]:
        node = add_node(name, "endpoint", name)
        link("target", node, "exposes")
    for device in (ctx.facts.get("devices") or [])[:8]:
        node = add_node(f"{device['ip']} {device.get('name') or ''}".strip(),
                        "asset", device.get("mac") or "LAN device")
        link("target", node, "observed on LAN")
    if ctx.facts.get("public_ip"):
        node = add_node(ctx.facts["public_ip"], "endpoint", "public egress")
        link("target", node, "exits via")
    for source in ctx.sources[:6]:
        node = add_node(source["label"], "source", source["locator"])
        link("target", node, "learned from")
    for edit in ctx.edits[:12]:
        node = add_node(edit["locator"], "edit", edit["action"])
        link("target", node, "can change")
        for evidence in edit["evidence"][:1]:
            finding = next((f for f in ctx.findings if f["id"] == evidence), None)
            if finding:
                link(node, "target", "evidenced by " + finding["id"])
    return {"nodes": nodes[:48], "edges": edges}


# ------------------------------------------------------------- report -------

def report_markdown(inv: dict) -> str:
    summary = inv.get("summary") or {}
    lines = [
        f"# REA dossier — {inv.get('target')}",
        "",
        f"- **ID:** {inv.get('id')}",
        f"- **Kind:** {summary.get('kind_label') or inv.get('kind')}",
        f"- **Created:** {inv.get('created')}",
        f"- **Scope:** {inv.get('scope') or 'not stated'}",
        f"- **Authorisation:** {'operator attested ownership / permission' if inv.get('authorized') else 'NOT AUTHORISED'}",
        f"- **Status:** {inv.get('status')}",
        "",
        "## Verdict",
        "",
        summary.get("verdict") or "No verdict recorded.",
        "",
        f"Evidence confidence: **{summary.get('confidence', 0):.0%}**."
        if isinstance(summary.get("confidence"), (int, float)) else "",
        "",
        "## Access modes",
        "",
    ]
    for mode in inv.get("modes") or []:
        mark = "available" if mode["available"] else "not available"
        lines.append(f"- **{mode['mode']}** — {mark} · risk {mode['risk']} · "
                     f"{mode['detail']}")
    facts = inv.get("facts") or {}
    if (facts.get("wifi") or facts.get("devices") or facts.get("local_ips")
            or facts.get("public_ip") or facts.get("exposure")):
        lines += ["", "## Network", ""]
        wifi = facts.get("wifi") or {}
        if wifi:
            parts = [wifi.get("state"),
                     f"signal {wifi['signal']}" if wifi.get("signal") else "",
                     f"channel {wifi['channel']}" if wifi.get("channel") else "",
                     wifi.get("radio"), wifi.get("auth")]
            lines.append(f"- **WiFi:** {wifi.get('ssid') or 'hidden/unknown'} — "
                         + (" · ".join(str(part) for part in parts if part) or "no detail"))
        if facts.get("local_ips") or facts.get("gateway"):
            lines.append(f"- **This device:** "
                         f"{', '.join(facts.get('local_ips') or []) or 'no IPv4'}; "
                         f"gateway {facts.get('gateway') or 'unknown'}; "
                         f"DNS {', '.join(facts.get('dns') or []) or 'unknown'}; "
                         f"subnet {facts.get('subnet') or 'unknown'}")
        devices = facts.get("devices") or []
        if devices:
            lines += ["", "### Devices observed", "",
                      "| IP | Name / vendor | MAC | State |", "|---|---|---|---|"]
            for device in devices[:64]:
                lines.append(f"| {device.get('ip')} | {device.get('name') or 'device'} | "
                             f"{device.get('mac') or '—'} | {device.get('state') or '—'} |")
            if facts.get("sweep_attempted"):
                lines += ["", f"ICMP sweep: {facts.get('sweep_responded', 0)} of "
                              f"{facts['sweep_attempted']} addresses answered (no port scan)."]
        egress = facts.get("egress") or {}
        if facts.get("public_ip"):
            location = ", ".join(str(part) for part in
                                 (egress.get("city"), egress.get("country")) if part)
            line = (f"- **Public identity:** {facts['public_ip']} — "
                    f"{egress.get('isp') or 'ISP unknown'}")
            if egress.get("asn"):
                line += f" · ASN {egress['asn']}"
            if location:
                line += f" · {location}"
            lines.append(line)
        exposure = facts.get("exposure") or {}
        if exposure:
            lines.append(f"- **Host exposure:** {exposure.get('host')} — "
                         f"{', '.join(exposure.get('ips') or []) or 'no address'}")
            tls = exposure.get("tls") or {}
            if tls.get("ok"):
                days = tls.get("days_left")
                lines.append(f"- **TLS:** {tls.get('issuer') or 'unknown issuer'} · "
                             f"{'verified' if tls.get('verified') else 'NOT verified'} · "
                             f"expires {tls.get('expires') or 'unknown'}"
                             + (f" ({days} days)" if days is not None else ""))
            http = exposure.get("http") or {}
            if http.get("status"):
                lines.append(f"- **HTTP edge:** {http.get('status')} · "
                             f"server {http.get('server') or 'undisclosed'} · "
                             f"HSTS {'yes' if http.get('hsts') else 'no'} · "
                             f"CSP {'yes' if http.get('csp') else 'no'}")
            if http.get("port80"):
                lines.append(f"- **Port 80:** {http['port80']}")
    lines += ["", "## Probe ladder", "",
              "| Probe | Family | Status | Result | Retry of |",
              "|---|---|---|---|---|"]
    for record in inv.get("probes") or []:
        lines.append(f"| {record['label']} | {record['family']} | {record['status']} | "
                     f"{str(record.get('summary') or '').replace('|', '/')[:140]} | "
                     f"{record.get('retry_of') or ''} |")
    lines += ["", "## Findings", ""]
    for finding in inv.get("findings") or []:
        place = f" — `{finding['path']}`" if finding.get("path") else ""
        lines.append(f"- **{finding['title']}**{place} · confidence {finding['confidence']:.0%}")
        if finding.get("detail"):
            lines.append(f"  - {finding['detail']}")
    lines += ["", "## Edit points", "",
              "| # | Category | Locator | Change | Risk | Confidence |",
              "|---|---|---|---|---|---|"]
    for edit in inv.get("edits") or []:
        lines.append(f"| {edit['id']} | {edit['category']} | `{edit['locator']}` | "
                     f"{edit['action'].replace('|', '/')} | {edit['risk']} | "
                     f"{edit['confidence']:.0%} |")
    lines += ["", "## Sources learned from", ""]
    for source in inv.get("sources") or []:
        lines.append(f"- **{source['label']}** — {source['locator']}")
    if not inv.get("sources"):
        lines.append("- No primary source located.")
    lines += ["", "## Next moves", ""]
    for move in inv.get("next_moves") or []:
        lines.append(f"- {move['move']}  \n  _because {move['because']}_")
    lines += ["", "## Guardrails", "",
              "- Authorised targets only; passive observation by default.",
              "- LAN discovery is a bounded ICMP sweep of one private /24; no port scanning.",
              "- Internet checks are DNS, TLS and HTTP GET on ports 80/443 only.",
              "- No authentication bypass, brute force or exploitation is performed.",
              "- Secrets are masked at capture time.",
              ""]
    return "\n".join(line for line in lines if line is not None)


# ---------------------------------------------------------------- runner ---

def new_investigation(target: str, authorized: bool, scope: str = "",
                      kind: str | None = None, network: bool = True,
                      sweep: bool = True, stage: str = "",
                      depth: str = "standard") -> dict:
    if authorized is not True:
        raise PermissionError(
            "REA only analyses targets you own or are authorised to assess. "
            "Confirm authorisation before running an investigation.")
    depth = str(depth or "standard").strip().lower()
    if depth not in {"quick", "standard", "deep"}:
        raise ValueError("depth must be quick, standard or deep")
    target = str(target or "").strip()
    if kind in {"wifi", "internet"}:
        if not target:
            target = kind
        classified = _network_classification(target, kind)
    else:
        classified = classify(target)
    if kind in {"folder", "file", "repo", "website", "har"}:
        if kind != classified["kind"] and not (kind == "repo" and classified["kind"] == "website"):
            raise ValueError(f"target looks like a {classified['kind']}, not a {kind}")
        classified["kind"] = kind
    inv = {
        "id": (f"REA-{datetime.now().strftime('%Y%m%d')}-"
               f"{_fingerprint(classified.get('url') or classified.get('local_path') or target)}-"
               f"{int(time.time() * 1000) % 100000:05d}"),
        "target": str(target).strip(),
        "kind": classified["kind"],
        "url": classified.get("url") or "",
        "repo": classified.get("repo") or "",
        "local_path": classified.get("local_path") or "",
        "scope": str(scope or "").strip()[:300],
        "authorized": True,
        "network": bool(network),
        "sweep": bool(sweep),
        "depth": depth,
        "stage": str(stage or "").strip()[:60],
        "status": "queued",
        "created": now_iso(),
        "started": "",
        "finished": "",
        "elapsed_ms": 0,
        "error": "",
        "probes": [],
        "findings": [],
        "edits": [],
        "sources": [],
        "modes": [],
        "next_moves": [],
        "graph": {"nodes": [], "edges": []},
        "facts": {},
        "summary": {"verdict": "Queued.", "confidence": 0.0, "counts": {}},
    }
    return inv


def run_investigation(inv: dict, network: bool = True, progress=None) -> dict:
    started = time.time()
    inv["status"] = "running"
    inv["started"] = now_iso()
    ctx = Ctx(inv, network=network)
    if inv["kind"] == "internet":
        queue = ["net.egress"] if _egress_target(inv["target"]) else ["net.exposure"]
    elif inv["kind"] == "wifi":
        queue = ["net.local", "net.devices"] if inv.get("sweep", True) else ["net.local"]
    else:
        queue = list(ORDER.get(inv["kind"], []))

    route_mode = str(inv.get("depth") or "standard")
    if route_mode == "quick":
        queue = queue[:3]
    elif route_mode == "deep":
        seen = set(queue)
        extra = [pid for pid, meta in PROBES.items()
                 if inv["kind"] in meta["kinds"] and pid not in seen]
        if inv["kind"] == "wifi" and not inv.get("sweep", True):
            extra = [pid for pid in extra if pid != "net.devices"]
        extra.sort(key=lambda pid: (PROBES[pid]["family"], pid))
        queue.extend(extra)
    depth: dict[str, int] = {pid: 0 for pid in queue}
    done: set[str] = set()
    records: list[dict] = []
    retry_of: dict[str, str] = {}

    if inv["kind"] in {"folder", "file"}:
        ctx.inventory()

    def persist() -> None:
        inv["findings"] = ctx.findings
        inv["edits"] = ctx.edits
        inv["sources"] = ctx.sources
        inv["probes"] = records
        if progress:
            progress(inv)

    while queue and len(records) < MAX_PROBES:
        pid = queue.pop(0)
        if pid in done:
            continue
        meta = PROBES.get(pid)
        if not meta or inv["kind"] not in meta["kinds"]:
            continue
        done.add(pid)
        started_probe = time.time()
        status, summary = "error", ""
        before = len(ctx.findings)
        ctx.probe = pid
        if meta["network"] and not network:
            status, summary = "skipped", "network disabled for this run"
        else:
            try:
                result = meta["run"](ctx) or {}
                status = result.get("status") or "empty"
                summary = str(result.get("summary") or "")
            except BudgetExceeded as exc:
                status, summary = "error", str(exc)
            except PermissionError as exc:
                status, summary = "error", str(exc)
            except Exception as exc:  # noqa: BLE001 - one probe must not kill the ladder
                status, summary = "error", f"{type(exc).__name__}: {exc}"
        record = {
            "id": pid,
            "label": meta["label"],
            "family": meta["family"],
            "description": meta["description"],
            "status": status,
            "summary": redact(summary)[:400],
            "findings": len(ctx.findings) - before,
            "ms": round((time.time() - started_probe) * 1000),
            "retry_of": retry_of.get(pid, ""),
            "next": list(meta["empty_next"]),
            "network": meta["network"],
        }
        records.append(record)
        if status in {"empty", "error"} and depth.get(pid, 0) < MAX_RETRY_DEPTH:
            for candidate in meta["empty_next"]:
                if candidate in done or candidate in queue:
                    continue
                if inv["kind"] not in PROBES[candidate]["kinds"]:
                    continue
                if (inv["kind"] == "wifi" and not inv.get("sweep", True)
                        and candidate == "net.devices"):
                    continue
                depth[candidate] = depth.get(pid, 0) + 1
                retry_of[candidate] = pid
                queue.insert(0, candidate)
        persist()

    inv["probes"] = records
    inv["findings"] = ctx.findings
    inv["edits"] = ctx.edits
    inv["sources"] = ctx.sources
    derive(ctx, inv)
    inv["status"] = "complete"
    inv["finished"] = now_iso()
    inv["elapsed_ms"] = round((time.time() - started) * 1000)
    persist()
    return inv


def investigate(target: str, authorized: bool = False, scope: str = "",
                kind: str | None = None, network: bool = True,
                sweep: bool = True, stage: str = "",
                depth: str = "standard") -> dict:
    inv = new_investigation(target, authorized, scope, kind, network, sweep, stage, depth)
    return run_investigation(inv, network=network)


# ------------------------------------------------------------ persistence --

_save_lock = threading.Lock()


def save(inv: dict) -> None:
    INVESTIGATIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = INVESTIGATIONS_DIR / f"{inv['id']}.json"
    tmp = path.with_suffix(".json.tmp")
    payload = json.dumps(inv, ensure_ascii=False, indent=1)
    with _save_lock:
        tmp.write_text(payload, "utf-8")
        last_error: OSError | None = None
        for attempt in range(6):
            try:
                tmp.replace(path)
                return
            except OSError as exc:  # Windows can hold the destination briefly
                last_error = exc
                time.sleep(0.02 * (attempt + 1))
        try:
            tmp.unlink()
        except OSError:
            pass
        if last_error:
            raise last_error


def load(inv_id: str) -> dict | None:
    safe = re.sub(r"[^A-Za-z0-9\-]", "", str(inv_id or ""))
    if not safe:
        return None
    path = INVESTIGATIONS_DIR / f"{safe}.json"
    with _save_lock:
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return data if isinstance(data, dict) else None


def list_investigations(limit: int = 40) -> list[dict]:
    if not INVESTIGATIONS_DIR.is_dir():
        return []
    items: list[dict] = []
    with _save_lock:
        paths = list(INVESTIGATIONS_DIR.glob("*.json"))
        for path in paths:
            try:
                data = json.loads(path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            summary = data.get("summary") or {}
            items.append({
                "id": data.get("id"),
                "target": data.get("target"),
                "kind": data.get("kind"),
                "stage": data.get("stage", ""),
                "depth": data.get("depth", "standard"),
                "status": data.get("status"),
                "created": data.get("created"),
                "verdict": summary.get("verdict", ""),
                "edits": (summary.get("counts") or {}).get("edits", 0),
            })
    items.sort(key=lambda item: str(item.get("created") or ""), reverse=True)
    return items[:limit]


# ------------------------------------------------------------------- cli ---

def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run one REA investigation.")
    parser.add_argument("target", help="GitHub repo, URL, local path, wifi or internet host")
    parser.add_argument("--scope", default="", help="scope note recorded in the dossier")
    parser.add_argument("--kind",
                        choices=("folder", "file", "repo", "website", "har", "wifi", "internet"))
    parser.add_argument("--offline", action="store_true", help="skip network probes")
    parser.add_argument("--depth", choices=("quick", "standard", "deep"), default="standard")
    parser.add_argument("--json", action="store_true", help="print the dossier as JSON")
    args = parser.parse_args(argv)
    try:
        inv = investigate(args.target, authorized=True, scope=args.scope,
                          kind=args.kind, network=not args.offline,
                          depth=args.depth)
    except (ValueError, PermissionError) as exc:
        print(f"REA: {exc}")
        return 2
    save(inv)
    if args.json:
        print(json.dumps(inv, ensure_ascii=False, indent=1))
    else:
        print(report_markdown(inv))
        print(f"Dossier saved: {INVESTIGATIONS_DIR / (inv['id'] + '.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
