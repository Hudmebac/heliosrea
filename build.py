#!/usr/bin/env python3
"""Helios — turn a folder of notes into a living knowledge galaxy.

Every run rescans the notes root and rebuilds ``viewer/galaxy.json``, the
database shared by the viewer and the assistant. For each note it derives a
title, a group (the note's top-level folder), a ~700-character excerpt, and
links to the other notes it mentions by title or through ``[[wikilinks]]``.
Relationships are written both ways so the galaxy is navigable in any
direction, and each run refreshes stats/degree so the map keeps improving as
the notes grow.

Usage:
    python build.py                  # scan notes.root from config.json
    python build.py --root F:/Play   # scan a specific folder
    python build.py --watch 30       # keep rebuilding when notes change

Node ids are the node's index in the ``nodes`` array, and every consumer
(viewer, server, future features) looks notes up by that index.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT / "config.json"
DEFAULT_OUT = PROJECT / "viewer" / "galaxy.json"

NOTE_EXTS = {".md", ".markdown", ".txt"}
SKIP_DIRS = {
    "node_modules", ".git", ".hg", ".svn", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".venv", "venv", "env", ".env", "dist",
    "build", ".next", ".nuxt", ".cache", "site-packages", ".idea", ".vscode",
    "coverage", ".tox", ".gradle", "bower_components",
}
# Titles that are too generic to prove a real relationship on a bare mention
# (a wiki-link to them is still honoured, because that is an explicit link).
GENERIC_ALIASES = {
    "readme", "todo", "notes", "plan", "index", "app", "main", "setup",
    "status", "changelog", "license", "package", "requirements", "summary",
    "report", "results", "tasks", "guide", "usage", "test", "tests", "config",
    "blocked", "review", "verification", "implementation", "overview",
}
PALETTE = [
    "#5ee7ff", "#ff6ec7", "#ffd166", "#7cff9b", "#a78bfa",
    "#ff8a5c", "#5eead4", "#f472b6", "#93c5fd", "#facc15",
    "#34d399", "#fb7185",
]
MAX_BYTES = 600_000
EXCERPT_CHARS = 700
MIN_MENTION_ALIAS = 5
STOP_TAIL = {"with", "for", "and", "the", "a", "an", "to", "of", "in", "on"}

FM_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.S)
H1_RE = re.compile(r"^#[ \t]+(.+?)[ \t]*#*[ \t]*$", re.M)
WIKI_RE = re.compile(r"\[\[([^\]\|#]+)(?:[#|][^\]]*)?\]\]")


def pretty(raw: str) -> str:
    """Turn a folder/file slug into a readable label."""
    s = re.sub(r"[_\-]+", " ", raw).strip()
    s = re.sub(r"\s+", " ", s)
    words = s.split()
    while len(words) > 1 and words[-1].lower() in STOP_TAIL:
        words.pop()
    out = " ".join(words).strip() or raw
    return out[0].upper() + out[1:]


def iter_notes(root: Path):
    """Walk the notes folder, skipping code/dependency noise and huge files."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if not d.startswith(".") and d.lower() not in SKIP_DIRS
        )
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.suffix.lower() not in NOTE_EXTS:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size == 0 or size > MAX_BYTES:
                continue
            yield path


def make_excerpt(body: str, title: str, limit: int) -> str:
    """Strip markdown down to roughly `limit` characters of readable prose."""
    text = re.sub(r"```.*?```", " ", body, flags=re.S)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = WIKI_RE.sub(lambda m: m.group(1), text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*>\s?", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*_]{3,}\s*$", " ", text, flags=re.M)
    text = re.sub(r"[*_`~]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if title and text.lower().startswith(title.lower()):
        text = text[len(title):].lstrip(" .:-\u2014")
    if len(text) > limit:
        clipped = text[:limit]
        cut = clipped.rfind(" ")
        if cut > limit * 0.6:
            clipped = clipped[:cut]
        text = clipped.rstrip(" ,;:-") + "\u2026"
    return text


def parse_note(path: Path, root: Path, text: str, excerpt_chars: int) -> dict:
    front: dict[str, str] = {}
    body = text
    match = FM_RE.match(text)
    if match:
        for line in match.group(1).splitlines():
            if ":" in line and not line.lstrip().startswith("#"):
                key, value = line.split(":", 1)
                front[key.strip().lower()] = value.strip().strip("\"'")
        body = text[match.end():]
    if not body.strip():
        body = text

    heading = H1_RE.search(body)
    title = (
        front.get("title")
        or (heading.group(1).strip() if heading else "")
        or pretty(path.stem)
    )
    title = re.sub(r"\s+", " ", title).strip()[:120]

    rel = path.relative_to(root)
    group = pretty(rel.parts[0]) if len(rel.parts) > 1 else pretty(root.name)

    aliases = {title, path.stem, pretty(path.stem)}
    wikilinks = [w.strip() for w in WIKI_RE.findall(body) if w.strip()]

    return {
        "title": title,
        "group": group,
        "excerpt": make_excerpt(body, title, excerpt_chars),
        "path": rel.as_posix(),
        "words": len(re.findall(r"\S+", body)),
        "_text": body,
        "_aliases": sorted(a for a in aliases if a.strip()),
        "_wikilinks": wikilinks,
    }


def find_links(nodes: list[dict]) -> set[tuple[int, int]]:
    """Connect notes that mention another note's title or share a [[wikilink]]."""
    alias_index: dict[str, set[int]] = {}
    for node in nodes:
        for alias in node["_aliases"]:
            key = alias.strip().lower()
            if len(key) >= 3:
                alias_index.setdefault(key, set()).add(node["id"])

    edges: set[tuple[int, int]] = set()

    # Explicit wiki-links always count.
    for node in nodes:
        for target in node["_wikilinks"]:
            for other in alias_index.get(target.strip().lower(), ()):
                if other != node["id"]:
                    edges.add((node["id"], other))

    # Implicit title mentions, matched in one pass per note.
    mentionable = {
        alias: ids
        for alias, ids in alias_index.items()
        if len(alias) >= MIN_MENTION_ALIAS and alias not in GENERIC_ALIASES
    }
    if mentionable:
        ordered = sorted(mentionable, key=len, reverse=True)
        pattern = re.compile(
            r"(?<![a-z0-9])("
            + "|".join(re.escape(alias) for alias in ordered)
            + r")(?![a-z0-9])"
        )
        for node in nodes:
            haystack = node["_text"].lower()
            for hit in pattern.finditer(haystack):
                for other in mentionable[hit.group(1)]:
                    if other != node["id"]:
                        edges.add((node["id"], other))

    # Keep every relationship navigable in both directions.
    reciprocal: set[tuple[int, int]] = set()
    for source, target in edges:
        reciprocal.add((source, target))
        reciprocal.add((target, source))
    return reciprocal


def build(root: Path, out: Path, excerpt_chars: int = EXCERPT_CHARS) -> dict:
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"notes root not found: {root}")

    nodes: list[dict] = []
    for path in iter_notes(root):
        try:
            text = path.read_text("utf-8", errors="replace")
        except OSError:
            continue
        if not text.strip():
            continue
        nodes.append(parse_note(path, root, text, excerpt_chars))

    nodes.sort(key=lambda n: (n["group"].lower(), n["title"].lower(), n["path"]))
    for index, node in enumerate(nodes):
        node["id"] = index

    edges = find_links(nodes)

    degree: Counter[int] = Counter()
    for source, target in edges:
        degree[source] += 1
        degree[target] += 1

    group_counts = Counter(node["group"] for node in nodes)
    groups = [
        {"name": name, "count": count, "color": PALETTE[i % len(PALETTE)]}
        for i, (name, count) in enumerate(group_counts.most_common())
    ]
    color_of = {group["name"]: group["color"] for group in groups}

    public_nodes = []
    for node in nodes:
        node["degree"] = degree.get(node["id"], 0)
        node["val"] = 1 + node["degree"]
        node["color"] = color_of.get(node["group"], PALETTE[0])
        public_nodes.append({
            key: node[key]
            for key in ("id", "title", "group", "excerpt", "path",
                        "words", "degree", "val", "color")
        })

    galaxy = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "root": str(root),
        "stats": {
            "notes": len(public_nodes),
            "links": len(edges),
            "groups": len(groups),
            "orphans": sum(1 for n in public_nodes if n["degree"] == 0),
            "words": sum(n["words"] for n in public_nodes),
        },
        "groups": groups,
        "nodes": public_nodes,
        "links": [{"source": s, "target": t} for s, t in sorted(edges)],
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(galaxy, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, out)
    return galaxy


def acquire_watch_lock():
    """Hold an OS-level lock so two watchers cannot fight over galaxy.json."""
    lock_path = PROJECT / "data" / "watch.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("w", encoding="utf-8")
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def signature(root: Path) -> str:
    digest = hashlib.sha1(str(root).encode("utf-8"))
    for path in iter_notes(root):
        try:
            stat = path.stat()
        except OSError:
            continue
        digest.update(f"{path.as_posix()}|{stat.st_mtime_ns}|{stat.st_size}".encode())
    return digest.hexdigest()


def report(galaxy: dict, out: Path) -> None:
    stats = galaxy["stats"]
    size_kb = out.stat().st_size / 1024 if out.exists() else 0
    print("+ Helios galaxy built")
    print(f"  notes    {stats['notes']}")
    print(f"  groups   {stats['groups']}")
    print(f"  links    {stats['links']}")
    print(f"  orphans  {stats['orphans']}")
    print(f"  words    {stats['words']:,}")
    print(f"  database {out} ({size_kb:.1f} KB)")
    if galaxy["groups"]:
        top = ", ".join(f"{g['name']} ({g['count']})" for g in galaxy["groups"][:5])
        print(f"  biggest  {top}")


def default_root() -> str:
    try:
        config = json.loads(CONFIG_PATH.read_text("utf-8"))
        configured = str(((config.get("notes") or {}).get("root") or "")).strip()
    except (OSError, json.JSONDecodeError):
        configured = ""
    if configured:
        return configured
    for candidate in ("F:/Play", "F:\\Play"):
        if Path(candidate).is_dir():
            return candidate
    return "notes"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="Build the Helios knowledge galaxy.")
    parser.add_argument("--root", default=None,
                        help="folder of notes (default: notes.root from config.json)")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="galaxy.json output path")
    parser.add_argument("--excerpt", type=int, default=EXCERPT_CHARS,
                        help="excerpt length in characters (default 700)")
    parser.add_argument("--watch", type=float, nargs="?", const=30.0, default=None,
                        metavar="SECONDS",
                        help="keep watching the notes and rebuild on change (default 30s)")
    args = parser.parse_args(argv)

    root = Path(args.root or default_root())
    out = Path(args.out)
    if not root.is_dir():
        print(f"! notes root not found: {root}", file=sys.stderr)
        print("  set notes.root in config.json or pass --root", file=sys.stderr)
        return 1

    if args.watch is None:
        report(build(root, out, args.excerpt), out)
        return 0

    interval = max(5.0, float(args.watch))
    lock = acquire_watch_lock()
    if lock is None:
        print("another Helios watcher is already running.", file=sys.stderr)
        return 0
    last = None
    try:
        while True:
            current = signature(root)
            if current != last:
                report(build(root, out, args.excerpt), out)
                last = current
                print(f"  watching {root} - rechecking every {interval:g}s (Ctrl+C to stop)\n")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nstopped watching.")
    finally:
        try:
            lock.close()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
