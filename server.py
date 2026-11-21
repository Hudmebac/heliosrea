#!/usr/bin/env python3
"""Helios server — the galaxy's brain stem.

Serves ONLY the ``viewer/`` folder to the browser, so ``config.json``, the API
key and the notes are unreachable from Chrome. Endpoints:

    GET  /api/health            safe status (never includes the key)
    GET  /api/snapshot          one atomic console snapshot (state + attention)
    GET  /api/attention         the operator queue across every view
    GET  /api/galaxy            the galaxy database
    GET  /api/links/semantic    meaning-based connections between notes
    GET  /api/brief             today's prepared brief (lazy, cached daily)
    POST /api/brief/refresh     rebuild today's brief with the active model
    GET  /api/tasks             mission-control task registry
    POST /api/tasks             create an audited task
    POST /api/tasks/update      move/update a task (dependency gates)
    POST /api/tasks/verify      evidence verification before Done
    POST /api/tasks/seed        load the Helios starter roadmap
    GET  /api/missions          mission catalogue with live progress
    GET  /api/missions/review   readiness review for one mission (?id=M-0001)
    GET  /api/studio            learned operator model, ideas and spend gate
    POST /api/studio/cycle      safe learning + idea + routing cycle
    POST /api/studio/learn      store an operator lesson for the model
    POST /api/studio/idea/accept create a task from a generated idea
    POST /api/studio/idea/decide park or reject an idea
    POST /api/missions          create a mission from a template
    POST /api/missions/control  restart, rebuild, start fresh or cancel a mission
    POST /api/missions/review   record an operator review decision (no status change)
    GET  /api/crew              persona catalogue, allocation and task routes
    POST /api/crew/brief        assign a persona and attach a delivery brief
    POST /api/crew/route        auto-route open tasks without briefing them
    GET  /api/activity          merged task history, crew briefs and pulse events
                                optional ?mission_id=M-0001 scope
    GET  /api/autonomy          safe background pulse status
    POST /api/pulse             route, brief and advance safe planning stages
    GET  /api/commander         live Commander plan (read-only)
    POST /api/commander/cycle   refresh the Commander narrative
    POST /api/commander/apply   attach a Commander briefing to a task
    POST /api/handoff          build a briefing for Freebuff's own free client
    POST /api/handoff/import   store a Freebuff answer in the conversation
    GET  /api/approvals         spend requests waiting for the operator
    POST /api/approvals         file a spend request (always starts pending)
    POST /api/approvals/decide  approve or deny a pending request
    POST /api/chat              answer from the notes, with server-side history

Helios can earn money, but it cannot spend: there is no spending path other
than the approvals queue, and nothing is spent without the operator calling
/api/approvals/decide with "approve". Everything else is autonomous.
"""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
from collections import Counter
import math
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import crew

PROJECT = Path(__file__).resolve().parent
VIEWER = PROJECT / "viewer"
CONFIG_PATH = PROJECT / "config.json"
GALAXY_PATH = VIEWER / "galaxy.json"
DATA_DIR = PROJECT / "data"
APPROVALS_PATH = DATA_DIR / "approvals.json"
EMBED_PATH = DATA_DIR / "embeddings.json"
BRIEF_PATH = DATA_DIR / "daily_brief.json"
TASKS_PATH = DATA_DIR / "tasks.json"
TASK_STATUSES = ("In Plan", "In Analysis", "In Progress", "In Test", "Done")
TASK_PRIORITIES = ("Low", "Medium", "High", "Critical")
MISSIONS_PATH = DATA_DIR / "missions.json"
COMMANDER_PATH = DATA_DIR / "commander.json"
FREEBUFF_KEY_PATH = DATA_DIR / "freebuff_key.json"
ACTIVITY_PATH = DATA_DIR / "activity.json"
IDEAS_PATH = DATA_DIR / "ideas.json"
LEARNINGS_PATH = DATA_DIR / "learnings.json"
RUNS_PATH = DATA_DIR / "runs.json"
EVALS_PATH = DATA_DIR / "evals.json"
POLICY_PATH = DATA_DIR / "policy.json"
SNAPSHOT_STATE_PATHS = (
    TASKS_PATH, MISSIONS_PATH, APPROVALS_PATH, ACTIVITY_PATH, IDEAS_PATH,
    LEARNINGS_PATH, COMMANDER_PATH, POLICY_PATH, CONFIG_PATH,
)
MISSION_TYPES = ("App", "Website", "Research", "Custom")
MISSION_REVIEW_DECISIONS = {
    "advance": "Ready to advance",
    "hold": "Hold",
    "escalate": "Escalate",
}
MISSION_CONTROL_ACTIONS = {
    "restart": "Restart this run",
    "rebuild": "Rebuild the plan",
    "fresh": "Start a fresh mission",
    "cancel": "Cancel this mission",
    "restore": "Restore a saved run",
}
MISSION_CONTROL_NOTES = {
    "restart": "Resets every task in this mission to In Plan and clears verification. The prior run is kept in the mission history.",
    "rebuild": "Replaces this mission's task plan with a clean template. The prior run is kept in the mission history.",
    "fresh": "Archives this mission and its tasks, then starts a clean successor with the same brief. Nothing is lost from the history.",
    "cancel": "Stops this mission and removes it from the active console. Its tasks leave the board and the prior run stays in the mission history.",
    "restore": "Replaces the active tasks with the exact snapshot from a saved run. The current run is snapshotted first so the restore can itself be undone.",
}
_mission_control_lock = threading.Lock()
PULSE_INTERVAL = 300
_pulse_lock = threading.RLock()
_activity_lock = threading.Lock()
_ideas_lock = threading.Lock()
_learning_lock = threading.Lock()
_runs_lock = threading.Lock()
_evals_lock = threading.Lock()
_policy_lock = threading.Lock()
_snapshot_lock = threading.Lock()
_snapshot_cache: dict = {"signature": None, "payload": None}
_pulse_state: dict = {
    "running": False, "last_run": "", "last_ts": 0.0, "cycles": 0,
    "last_changed": 0, "error": "",
}
COMMANDER_PRIORITY = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
COMMANDER_PLAN_VERSION = 2
INACTIVE_MISSION_STATES = frozenset({"cancelled", "archived"})
TASK_STAGE_PROGRESS = {
    "In Plan": 0, "In Analysis": 30, "In Progress": 60,
    "In Test": 85, "Done": 100,
}
AUTONOMY_STAGE_NEXT = {"In Plan": "In Analysis", "In Analysis": "In Progress"}


def mission_is_active(mission: dict | None) -> bool:
    return str((mission or {}).get("lifecycle") or "active") not in INACTIVE_MISSION_STATES


def active_missions(missions: list[dict]) -> list[dict]:
    return [mission for mission in missions if mission_is_active(mission)]


DEFAULT_ACTIONS = {
    "read": {"label": "Read notes, board and activity", "mode": "autonomous", "configurable": False},
    "rank": {"label": "Rank and critique options", "mode": "autonomous", "configurable": False},
    "draft": {"label": "Draft text and briefs", "mode": "autonomous", "configurable": False},
    "route": {"label": "Route and brief ready work", "mode": "autonomous", "configurable": False},
    "brief": {"label": "Attach delivery briefs", "mode": "autonomous", "configurable": False},
    "advance": {"label": "Advance briefed planning stages", "mode": "autonomous", "configurable": False},
    "ideas": {"label": "Generate and rank ideas", "mode": "autonomous", "configurable": False},
    "learn": {"label": "Rebuild the operator model", "mode": "autonomous", "configurable": False},
    "accept_idea": {"label": "Create tasks from ideas", "mode": "autonomous", "configurable": False},
    "publish": {"label": "Publish, deploy or send", "mode": "autonomous", "configurable": False},
    "spend": {"label": "Spend or pay", "mode": "gated", "configurable": False},
    "sign_off": {"label": "Verify and close completed work", "mode": "autonomous", "configurable": False},
    "delete": {"label": "Delete or overwrite data", "mode": "autonomous", "configurable": False},
}

DEFAULT_EVAL_CASES = [
    {
        "id": "E-ZERO-SPEND",
        "name": "Zero-cost request becomes a plain no-spend line",
        "kind": "spend",
        "answer": "Mission Restart",
        "spend": {"title": "Mission Restart", "amount": 0.0, "reason": "Restarting Mission"},
        "expect": {"display": "Mission Restart — no spend."},
    },
    {
        "id": "E-PAID-SPEND",
        "name": "Paid request names the amount and approval",
        "kind": "spend",
        "answer": "Need a small purchase.",
        "spend": {"title": "Test purchase", "amount": 1.5, "reason": "x"},
        "expect": {"contains": ["1.50 GBP", "approval needed"]},
    },
    {
        "id": "E-NO-MACHINE-SYNTAX",
        "name": "Visible answer never exposes a spend marker",
        "kind": "verifier",
        "answer": 'Mission Restart SPEND_REQUEST: {"title": "x", "amount": 0}',
        "expect": {"ok": False, "check": "machine_syntax"},
    },
    {
        "id": "E-NO-PLACEHOLDER",
        "name": "Template placeholders are blocked",
        "kind": "verifier",
        "answer": "<name> — no spend.",
        "expect": {"ok": False, "check": "template_placeholder"},
    },
    {
        "id": "E-NO-SPEND-NONSENSE",
        "name": "A no-spend line cannot answer a normal question",
        "kind": "verifier",
        "answer": "Something happened today — no spend.",
        "expect": {"ok": False, "check": "no_spend_context"},
    },
    {
        "id": "E-NO-FAKE-COMPLETION",
        "name": "Unsupported completion claim is blocked",
        "kind": "verifier",
        "answer": "I published the launch and signed off T-0013.",
        "expect": {"ok": False, "check": "completion_claim"},
    },
    {
        "id": "E-DIRECT-CLOCK",
        "name": "Clock questions are answered from local time",
        "kind": "direct",
        "question": "what time is it",
        "expect": {"contains": [":", " on "]},
    },
    {
        "id": "E-DIRECT-NEXT",
        "name": "Next move comes from the live Commander plan",
        "kind": "direct",
        "question": "what should i do next",
        "expect": {"contains": ["T-0010", "Mission Controller"]},
    },
    {
        "id": "E-DIRECT-STATUS",
        "name": "Board status comes from live task counts",
        "kind": "direct",
        "question": "what's the status",
        "expect": {"contains": ["14 tasks", "0 broken"]},
    },
    {
        "id": "E-DIRECT-TODAY",
        "name": "Today uses recorded operator-visible activity",
        "kind": "direct",
        "question": "what happened today",
        "expect": {"contains": ["Pulse briefed"]},
    },
    {
        "id": "E-DIRECT-BLOCKED",
        "name": "Blockers distinguish waiting from broken",
        "kind": "direct",
        "question": "what's blocked",
        "expect": {"contains": ["No broken dependencies"]},
    },
    {
        "id": "E-DIRECT-SIGNOFF",
        "name": "Verification refuses to claim a waiting task is ready",
        "kind": "direct",
        "question": "what needs sign-off",
        "expect": {"contains": ["Nothing is ready for verification"]},
    },
    {
        "id": "E-NO-UNKNOWN-ID",
        "name": "Unknown task IDs are blocked",
        "kind": "verifier",
        "answer": "T-9999 is the next move.",
        "sources": [{"id": 1, "title": "Mission notes"}],
        "expect": {"ok": False, "check": "task_ids"},
    },
    {
        "id": "E-GROUNDED",
        "name": "A grounded next-move answer passes verification",
        "kind": "verifier",
        "answer": "T-0001 is the next unblocked move. I can prepare its charter without spending anything.",
        "sources": [{"id": 1, "title": "Mission notes"}],
        "expect": {"ok": True},
    },
]
MISSION_TEMPLATES = {
    "App": (
        {"key": "brief", "title": "Define the app and the user problem", "owner": "CEO Agent", "priority": "High", "status": "In Plan", "risks": "Scope creep before the core problem is clear.", "deps": ()},
        {"key": "design", "title": "Design the core experience", "owner": "Product Agent", "priority": "High", "status": "In Plan", "risks": "Design must stay accessible and testable.", "deps": ("brief",)},
        {"key": "build", "title": "Build the working prototype", "owner": "Build Agent", "priority": "High", "status": "In Plan", "risks": "The prototype must not fake the hard parts.", "deps": ("design",)},
        {"key": "verify", "title": "Test and verify the prototype", "owner": "Red Team", "priority": "High", "status": "In Test", "risks": "Verification must be independent of the builder.", "deps": ("build",)},
        {"key": "launch", "title": "Prepare launch and support", "owner": "Chief of Staff", "priority": "Medium", "status": "In Plan", "risks": "No launch until the prototype is signed off.", "deps": ("verify",)},
    ),
    "Website": (
        {"key": "brief", "title": "Define the site and its promise", "owner": "CEO Agent", "priority": "High", "status": "In Plan", "risks": "The promise must remain evidence-backed.", "deps": ()},
        {"key": "copy", "title": "Write the copy and information architecture", "owner": "Content Agent", "priority": "High", "status": "In Plan", "risks": "No unsupported claims or marketing fluff.", "deps": ("brief",)},
        {"key": "build", "title": "Build the responsive pages", "owner": "Build Agent", "priority": "High", "status": "In Plan", "risks": "Performance and accessibility budget.", "deps": ("copy",)},
        {"key": "verify", "title": "Verify every claim and link", "owner": "Red Team", "priority": "High", "status": "In Test", "risks": "A single unsourced claim undermines trust.", "deps": ("build",)},
        {"key": "publish", "title": "Publish and announce", "owner": "Chief of Staff", "priority": "Medium", "status": "In Plan", "risks": "Hosting must stay on the zero-cost path.", "deps": ("verify",)},
    ),
    "Research": (
        {"key": "question", "title": "Frame the research question", "owner": "CEO Agent", "priority": "High", "status": "In Plan", "risks": "A vague question produces vague evidence.", "deps": ()},
        {"key": "sources", "title": "Collect primary sources", "owner": "Research Agent", "priority": "High", "status": "In Plan", "risks": "Source licensing and freshness.", "deps": ("question",)},
        {"key": "analysis", "title": "Analyse and triangulate the evidence", "owner": "Data Agent", "priority": "High", "status": "In Plan", "risks": "Correlation is not causation.", "deps": ("sources",)},
        {"key": "redteam", "title": "Red-team the findings", "owner": "Red Team", "priority": "High", "status": "In Test", "risks": "Challenge assumptions, not just wording.", "deps": ("analysis",)},
        {"key": "publish", "title": "Publish the verified findings", "owner": "Research Agent", "priority": "Medium", "status": "In Plan", "risks": "Show uncertainty and evidence limits.", "deps": ("redteam",)},
    ),
    "Custom": (
        {"key": "goal", "title": "Define the mission goal", "owner": "CEO Agent", "priority": "High", "status": "In Plan", "risks": "Unclear success criteria.", "deps": ()},
        {"key": "next", "title": "Choose the first concrete deliverable", "owner": "Chief of Staff", "priority": "Medium", "status": "In Plan", "risks": "Avoid planning without a testable output.", "deps": ("goal",)},
    ),
}
DEFAULT_EMBED_MODEL = "nomic-embed-text"
SEMANTIC_FLOOR = 0.72
SEMANTIC_PER_NOTE = 2
SEMANTIC_GENERIC = {
    "readme", "output", "outputs", "requirements", "dependencies", "setup",
    "build", "index", "notes", "summary", "report", "results", "status",
    "changelog", "license", "package", "config", "todo", "tasks", "guide",
    "verification", "implementation", "overview", "robots",
}
_semantic_lock = threading.Lock()
_semantic_compute_lock = threading.Lock()
_semantic_cache: dict = {"key": None, "links": [], "count": 0, "model": "", "error": ""}
_brief_lock = threading.Lock()
_brief_state: dict = {
    "date": None, "ready": False, "generating": False,
    "attempted": 0.0, "error": "", "brief": {},
}
_commander_lock = threading.Lock()
_commander_state: dict = {
    "generating": False, "attempted": 0.0, "error": "",
}

DEFAULT_PORT = 4700
HISTORY_MESSAGES = 8
MAX_MESSAGE_CHARS = 4000
KEY_PLACEHOLDERS = {
    "", "paste_your_api_key_here", "sk-...", "your_api_key", "changeme",
    "api_key_here", "paste-key-here",
}
STOPWORDS = {
    "the", "and", "for", "are", "but", "not", "you", "your", "with", "this",
    "that", "what", "when", "where", "which", "who", "how", "why", "does",
    "did", "was", "were", "have", "has", "had", "can", "could", "would",
    "should", "about", "from", "into", "them", "they", "their", "there",
    "these", "those", "then", "than", "over", "under", "also", "just",
    "any", "all", "our", "out", "get", "got", "make", "made", "way",
    "like", "more", "some", "such", "only", "own", "same", "too", "very",
    "will", "shall", "may", "might", "must", "one", "two", "use", "used",
    "using", "note", "notes", "idea", "ideas", "tell", "give", "show",
}

DEFAULT_SYSTEM = (
    "You are Helios, the operator's personal chief of staff and a living "
    "reflection of their notes, judgement and voice. Think in their voice, "
    "protect their time, and turn intent into finished work. Talk like a "
    "trusted colleague: warm, direct and natural, with contractions and a "
    "human rhythm. For greetings, thanks and ordinary conversation, answer the "
    "person in front of you without forcing the board into it. For work "
    "questions, ground every claim in the NOTES, live MISSION CONTROL state "
    "and RECENT ACTIVITY provided with each question; if they do not cover "
    "something, say so plainly instead of guessing. Act without waiting for "
    "permission except spending; if unsure, act then advise."
)

CLOCK_QUERY_RE = re.compile(
    r"^(?:hey\s+)?(?:"
    r"what(?:'s| is)?(?: the)? time(?: is it)?|"
    r"what time is it|tell me the time|current time|time now|"
    r"what(?:'s| is)?(?: the)?(?: today'?s?)? date|what day is (?:it|today)"
    r")$", re.I
)
NEXT_QUERY_RE = re.compile(
    r"^(?:hey\s+)?(?:what(?:'s| is)? next|what should i do(?: next)?|"
    r"what do i do(?: next)?|next move|what is the next move|where do i start|"
    r"what now|what should i work on|what(?:'s| is)? on my plate|"
    r"what(?:'s| is)? the plan|next)$", re.I
)
STATUS_QUERY_RE = re.compile(
    r"^(?:what(?:'s| is)? (?:the )?(?:status|situation)|status|board status|"
    r"show me the board|what(?:'s| is)? on the board|how are we doing|"
    r"how is the board|what(?:'s| is)? the state|what(?:'s| is)? happening)$", re.I
)
TODAY_QUERY_RE = re.compile(
    r"^(?:what happened today|what(?:'s| has) happened today|"
    r"tell me something that happened today|what did you do today|"
    r"what changed today|any updates(?: today)?|what(?:'s| is) new today|today)$",
    re.I
)
BLOCKED_QUERY_RE = re.compile(
    r"^(?:what(?:'s| is) blocked|what is blocking|what(?:'s| is) stopping us|"
    r"what can(?:'t|not) move|blockers?|what(?:'s| is) the blocker)$", re.I
)
SIGNOFF_QUERY_RE = re.compile(
    r"^(?:what needs sign[\s-]?off|what should i sign off|sign[\s-]?off queue|"
    r"what(?:'s| is) ready for sign[\s-]?off|what can i sign off|sign[\s-]?off)$",
    re.I
)
APPROVAL_QUERY_RE = re.compile(
    r"^(?:what needs (?:spend )?approval|pending approvals|spend approvals|"
    r"approvals|what(?:'s| is) waiting for approval|what needs approving)$", re.I
)
IDEAS_QUERY_RE = re.compile(
    r"^(?:what ideas do you have|show ideas|idea queue|any ideas|"
    r"what are your ideas)$", re.I
)
CAPABILITY_QUERY_RE = re.compile(
    r"^(?:help|what can you do|what do you do|capabilities)$", re.I
)
TASK_QUERY_RE = re.compile(
    r"^(?:(?:what(?:'s| is)|status of|tell me about)\s+)?(t-\d{4})(?:\s+status)?$",
    re.I
)


def clock_text() -> str:
    now = time.localtime()
    return (f"{now.tm_hour:02d}:{now.tm_min:02d} on "
            f"{time.strftime('%A', now)} {now.tm_mday} "
            f"{time.strftime('%B %Y', now)}")


def normalize_operator_query(message: str) -> str:
    return re.sub(r"\s+", " ", str(message or "").strip().lower()).rstrip("?.!").strip()


def direct_answer(message: str) -> str | None:
    """Answer live facts locally; never ask the model to invent them."""
    query = normalize_operator_query(message)
    if not query:
        return None
    if CLOCK_QUERY_RE.fullmatch(query):
        return f"It’s {clock_text()}."
    try:
        return local_operator_answer(query)
    except Exception as exc:  # noqa: BLE001 - the model path remains available
        print(f"! local operator answer unavailable: {exc}", file=sys.stderr)
        return None


DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "mistral:latest"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-flash"
DEFAULT_FREEBUFF_BASE_URL = "https://www.codebuff.com/api/v1"
DEFAULT_FREEBUFF_MODEL = "deepseek/deepseek-v4-flash-fast"

# ---------------------------------------------------------------- config ----

_config_lock = threading.Lock()
_config_cache: dict = {"mtime": None, "data": {}}


def config() -> dict:
    """Read config.json, reloading whenever the file changes on disk."""
    with _config_lock:
        try:
            mtime = CONFIG_PATH.stat().st_mtime_ns
        except OSError:
            return {}
        if _config_cache["mtime"] != mtime:
            try:
                _config_cache["data"] = json.loads(CONFIG_PATH.read_text("utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"! config.json unreadable: {exc}", file=sys.stderr)
                _config_cache["data"] = {}
            _config_cache["mtime"] = mtime
        data = _config_cache["data"]
        return data if isinstance(data, dict) else {}


def clean_key(value: object) -> str:
    key = str(value or "").strip()
    if key.lower() in KEY_PLACEHOLDERS or key.lower().startswith("paste"):
        return ""
    return key


def api_key(cfg: dict | None = None) -> str:
    cfg = cfg if cfg is not None else config()
    return clean_key((cfg.get("llm") or {}).get("api_key"))


def deepseek_settings(cfg: dict) -> tuple[dict, str]:
    """DeepSeek credentials may live in config.json or the process environment."""
    llm = cfg.get("llm") or {}
    remote = llm.get("deepseek") or {}
    if not isinstance(remote, dict):
        remote = {}
    key = clean_key(remote.get("api_key"))
    if not key:
        key = clean_key(os.environ.get("DEEPSEEK_API_KEY"))
    if not key:
        key = clean_key(os.environ.get("HELIOS_DEEPSEEK_API_KEY"))
    return remote, key


def freebuff_settings(cfg: dict) -> tuple[dict, str]:
    """Freebuff's OpenAI-compatible API, keyed server-side."""
    llm = cfg.get("llm") or {}
    remote = llm.get("freebuff") or {}
    if not isinstance(remote, dict):
        remote = {}
    key = clean_key(remote.get("api_key"))
    if not key:
        key = clean_key(os.environ.get("FREEBUFF_API_KEY"))
    if not key:
        key = clean_key(os.environ.get("CODEBUFF_API_KEY"))
    if not key:
        try:
            saved = json.loads(FREEBUFF_KEY_PATH.read_text("utf-8"))
            key = clean_key(saved.get("api_key"))
        except (OSError, json.JSONDecodeError):
            key = ""
    return remote, key


def llm_settings(cfg: dict) -> tuple[str, dict, str]:
    """Resolve the brain: DeepSeek when keyed, otherwise local Ollama."""
    llm = cfg.get("llm") or {}
    provider = str(llm.get("provider") or "auto").strip().lower()
    freebuff, freebuff_key = freebuff_settings(cfg)
    if bool(freebuff.get("enabled", True)) and freebuff_key and provider in {"auto", "freebuff"}:
        settings = {
            "base_url": os.environ.get("FREEBUFF_BASE_URL") or DEFAULT_FREEBUFF_BASE_URL,
            "model": os.environ.get("FREEBUFF_MODEL") or DEFAULT_FREEBUFF_MODEL,
            **llm,
            **freebuff,
            "provider": "freebuff",
            "dialect": "freebuff",
        }
        settings.pop("freebuff", None)
        settings.pop("deepseek", None)
        return "openai", settings, freebuff_key
    remote, remote_key = deepseek_settings(cfg)
    if bool(remote.get("enabled", True)) and remote_key and provider in {"auto", "deepseek"}:
        settings = {
            "base_url": DEFAULT_DEEPSEEK_BASE_URL,
            "model": DEFAULT_DEEPSEEK_MODEL,
            **llm,
            **remote,
            "provider": "deepseek",
            "dialect": "deepseek",
        }
        settings.pop("deepseek", None)
        return "openai", settings, remote_key
    key = api_key(cfg)
    if provider in {"auto", "deepseek"}:
        provider = "openai" if key else "ollama"
    if provider not in {"ollama", "openai"}:
        provider = "openai" if key else "ollama"
    return provider, llm, key


def ollama_url(llm: dict) -> str:
    return str(llm.get("ollama_url") or DEFAULT_OLLAMA_URL).rstrip("/")


_ollama_lock = threading.Lock()
_ollama_cache: dict = {"at": 0.0, "url": "", "ok": False, "models": [], "detail": ""}


def ollama_status(cfg: dict, force: bool = False) -> dict:
    """Is the local Ollama service up, and which models does it have?"""
    llm = cfg.get("llm") or {}
    with _ollama_lock:
        url = ollama_url(llm)
        if (not force and _ollama_cache.get("url") == url
                and time.time() - float(_ollama_cache["at"]) < 15.0):
            return dict(_ollama_cache)
        result = {"at": time.time(), "url": url, "ok": False, "models": [], "detail": ""}
        try:
            with urllib.request.urlopen(ollama_url(llm) + "/api/tags", timeout=2.0) as response:
                data = json.loads(response.read().decode("utf-8", "replace"))
            result["models"] = [
                str(model.get("name") or "")
                for model in (data.get("models") or [])
                if model.get("name")
            ]
            result["ok"] = True
        except Exception as exc:  # noqa: BLE001 - the service may simply be off
            result["detail"] = str(exc)
        _ollama_cache.update(result)
        return dict(result)


def model_installed(model: str, models: list[str]) -> bool:
    wanted = model.strip().lower()
    for name in models:
        installed = name.strip().lower()
        if installed == wanted or installed.split(":", 1)[0] == wanted.split(":", 1)[0]:
            return True
    return False


def brain_status(cfg: dict) -> dict:
    """What will answer questions, and is it usable right now?"""
    provider, llm, key = llm_settings(cfg)
    model = str(llm.get("model") or (
        DEFAULT_OLLAMA_MODEL if provider == "ollama" else "gpt-4o-mini"
    ))
    remote, remote_key = deepseek_settings(cfg)
    freebuff, freebuff_key = freebuff_settings(cfg)
    local = cfg.get("llm") or {}
    label = ("Freebuff" if llm.get("dialect") == "freebuff"
             else "DeepSeek" if llm.get("dialect") == "deepseek" else provider)
    status = {
        "provider": provider,
        "provider_label": label,
        "dialect": llm.get("dialect") or "",
        "model": model,
        "ready": False,
        "error": "",
        "detail": "",
        "remote": {
            "provider": "deepseek",
            "model": str(remote.get("model") or DEFAULT_DEEPSEEK_MODEL),
            "enabled": bool(remote.get("enabled", True)),
            "configured": bool(remote_key),
            "thinking": bool(remote.get("thinking", False)),
        },
        "freebuff": {
            "model": str(freebuff.get("model") or DEFAULT_FREEBUFF_MODEL),
            "base_url": str(freebuff.get("base_url") or DEFAULT_FREEBUFF_BASE_URL),
            "enabled": bool(freebuff.get("enabled", True)),
            "configured": bool(freebuff_key),
        },
        "fallback": {
            "provider": "ollama",
            "model": str(local.get("model") or DEFAULT_OLLAMA_MODEL),
        },
    }
    if provider == "openai":
        if not key:
            status["error"] = "no_api_key"
        else:
            status["ready"] = True
        return status
    local = ollama_status(cfg)
    if not local["ok"]:
        status["error"] = "ollama_down"
        status["detail"] = local.get("detail") or "not reachable"
        return status
    if not model_installed(model, local["models"]):
        status["error"] = "ollama_model_missing"
        status["detail"] = "installed: " + (", ".join(local["models"][:6]) or "none")
        return status
    status["ready"] = True
    return status


# ---------------------------------------------------------------- galaxy ----

_galaxy_lock = threading.Lock()
_galaxy_cache: dict = {"mtime": None, "data": {}}


def galaxy_data() -> dict:
    with _galaxy_lock:
        try:
            mtime = GALAXY_PATH.stat().st_mtime_ns
        except OSError:
            return {}
        if _galaxy_cache["mtime"] != mtime:
            try:
                _galaxy_cache["data"] = json.loads(GALAXY_PATH.read_text("utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"! galaxy.json unreadable: {exc}", file=sys.stderr)
                _galaxy_cache["data"] = {}
            _galaxy_cache["mtime"] = mtime
        data = _galaxy_cache["data"]
        return data if isinstance(data, dict) else {}


WORD_RE = re.compile(r"[a-z0-9']+")


def keywords(text: str) -> set[str]:
    return {
        word for word in WORD_RE.findall(text.lower())
        if len(word) > 2 and word not in STOPWORDS
    }


_embed_lock = threading.Lock()
_embed_state: dict = {
    "ready": False, "building": False, "error": "", "generated": None,
    "vectors": {}, "model": "",
}


def _embed_model(cfg: dict | None = None) -> str:
    cfg = cfg if cfg is not None else config()
    return str(((cfg.get("llm") or {}).get("embed_model")) or DEFAULT_EMBED_MODEL)


def embed_texts(texts: list[str], cfg: dict | None = None) -> list[list[float]]:
    """Ask the local Ollama embedding model for vectors."""
    cfg = cfg if cfg is not None else config()
    payload = {"model": _embed_model(cfg), "input": texts, "keep_alive": "30m"}
    request = urllib.request.Request(
        ollama_url(cfg.get("llm") or {}) + "/api/embed",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "Helios/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8", "replace"))
    vectors = data.get("embeddings") or []
    if len(vectors) != len(texts):
        raise RuntimeError("embedding count mismatch")
    return vectors


def unit_vector(values) -> list[float]:
    norm = math.sqrt(sum(float(value) * float(value) for value in values)) or 1.0
    return [round(float(value) / norm, 5) for value in values]


def note_signature(node: dict) -> tuple[str, str]:
    text = "\n".join([
        str(node.get("title") or ""),
        str(node.get("group") or ""),
        str(node.get("excerpt") or ""),
    ])
    return hashlib.sha1(text.encode("utf-8")).hexdigest(), text


def build_embeddings(cfg: dict | None = None) -> None:
    """Build or refresh the local semantic index, reusing vectors by hash."""
    global _embed_state
    cfg = cfg if cfg is not None else config()
    galaxy = galaxy_data()
    notes = galaxy.get("nodes") or []
    generated = galaxy.get("generated")
    if not notes:
        return
    with _embed_lock:
        if _embed_state["building"]:
            return
        _embed_state["building"] = True
        _embed_state["error"] = ""
    try:
        cached = {}
        if EMBED_PATH.exists():
            try:
                stored = json.loads(EMBED_PATH.read_text("utf-8"))
                if stored.get("generated") == generated:
                    cached = stored.get("notes") or {}
            except (OSError, json.JSONDecodeError):
                cached = {}
        vectors = {}
        hashes = {}
        pending = []
        for node in notes:
            node_id = str(node.get("id"))
            signature, text = note_signature(node)
            hashes[node_id] = signature
            entry = cached.get(node_id)
            if entry and entry.get("hash") == signature and entry.get("vector"):
                vectors[node_id] = entry["vector"]
            else:
                pending.append((node_id, signature, text))
        with _embed_lock:
            _embed_state["vectors"] = dict(vectors)
            _embed_state["ready"] = bool(vectors)
        if pending:
            print(f"  embeddings: indexing {len(pending)} notes with {_embed_model(cfg)}", flush=True)
        for start in range(0, len(pending), 16):
            chunk = pending[start:start + 16]
            embedded = embed_texts([item[2] for item in chunk], cfg)
            for (node_id, _signature, _text), vector in zip(chunk, embedded):
                vectors[node_id] = unit_vector(vector)
            with _embed_lock:
                _embed_state["vectors"] = dict(vectors)
                _embed_state["ready"] = True
            done = min(start + 16, len(pending))
            if len(pending) > 32 and done % 64 == 0:
                print(f"  embeddings: {len(vectors)}/{len(notes)}", flush=True)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated": generated,
            "model": _embed_model(cfg),
            "notes": {
                node_id: {"hash": hashes.get(node_id, ""), "vector": vector}
                for node_id, vector in vectors.items()
            },
        }
        tmp = EMBED_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
        tmp.replace(EMBED_PATH)
        with _embed_lock:
            _embed_state.update({
                "ready": True, "generated": generated,
                "model": _embed_model(cfg), "error": "",
            })
        print(f"  embeddings: ready ({len(vectors)} notes)", flush=True)
    except Exception as exc:  # noqa: BLE001 - keyword search remains usable
        with _embed_lock:
            _embed_state["error"] = str(exc)
        print(f"! embeddings unavailable ({exc}) - using keyword search", flush=True)
    finally:
        with _embed_lock:
            _embed_state["building"] = False


def embedding_keeper() -> None:
    """Keep the semantic index in step with galaxy rebuilds."""
    while True:
        try:
            galaxy = galaxy_data()
            generated = galaxy.get("generated")
            with _embed_lock:
                stale = bool(generated) and _embed_state["generated"] != generated
                building = _embed_state["building"]
            if stale and not building:
                build_embeddings()
            semantic_links()
        except Exception as exc:  # noqa: BLE001
            print(f"! embedding keeper: {exc}", file=sys.stderr)
        time.sleep(60)


def retrieval_state() -> dict:
    with _embed_lock:
        return {
            "semantic": _embed_state["ready"],
            "indexed": len(_embed_state["vectors"]),
            "building": _embed_state["building"],
            "model": _embed_state["model"] or DEFAULT_EMBED_MODEL,
            "error": _embed_state["error"],
        }


def semantic_scores(question: str, cfg: dict | None = None) -> dict[int, float]:
    cfg = cfg if cfg is not None else config()
    with _embed_lock:
        vectors = dict(_embed_state["vectors"])
        ready = _embed_state["ready"]
    if not ready or not vectors:
        return {}
    try:
        query = unit_vector(embed_texts([question], cfg)[0])
    except Exception:  # noqa: BLE001 - fall back to keywords
        return {}
    scores = {}
    for node_id, vector in vectors.items():
        try:
            scores[int(node_id)] = sum(a * b for a, b in zip(query, vector))
        except (TypeError, ValueError):
            continue
    return scores


def semantic_links(galaxy: dict | None = None) -> dict:
    """Strongest meaning-based pairs, built from the local embeddings.

    Authored links stay the backbone; these add up to two extra connections
    per note when two excerpts mean nearly the same thing (cosine >= 0.72).
    Duplicated generic documents (README to README, etc.) are skipped so the
    graph shows ideas rather than copies. The result is cached until either
    the galaxy or the embeddings are rebuilt.
    """
    galaxy = galaxy if galaxy is not None else galaxy_data()
    generated = galaxy.get("generated")
    with _embed_lock:
        vectors = dict(_embed_state["vectors"])
        embed_generated = _embed_state["generated"]
        model = _embed_state["model"] or DEFAULT_EMBED_MODEL
    key = (generated, embed_generated)
    with _semantic_lock:
        if _semantic_cache.get("key") == key:
            return dict(_semantic_cache)
    with _semantic_compute_lock:
        with _semantic_lock:
            if _semantic_cache.get("key") == key:
                return dict(_semantic_cache)
        result = {"key": key, "links": [], "count": 0, "model": model,
                  "ready": False, "error": ""}
        if not vectors or not generated or embed_generated != generated:
            result["error"] = "embeddings not ready"
        else:
            nodes = {int(node["id"]): node for node in galaxy.get("nodes") or []}
            ids = sorted(node_id for node_id in nodes if str(node_id) in vectors)
            existing = set()
            for link in galaxy.get("links") or []:
                try:
                    left, right = int(link["source"]), int(link["target"])
                except (KeyError, TypeError, ValueError):
                    continue
                existing.add((min(left, right), max(left, right)))
            generic = {
                node_id: str(nodes[node_id].get("title") or "").strip().lower()
                in SEMANTIC_GENERIC
                for node_id in ids
            }
            titles = {
                node_id: re.sub(
                    r"[^a-z0-9]+", " ",
                    str(nodes[node_id].get("title") or "").lower(),
                ).strip()
                for node_id in ids
            }
            candidates = []
            for position, left in enumerate(ids):
                left_vector = vectors[str(left)]
                for right in ids[position + 1:]:
                    if generic[left] and generic[right]:
                        continue
                    if titles[left] and titles[left] == titles[right]:
                        continue
                    score = sum(a * b for a, b in zip(left_vector, vectors[str(right)]))
                    if score >= SEMANTIC_FLOOR:
                        candidates.append((score, left, right))
            candidates.sort(reverse=True)
            used = {node_id: 0 for node_id in ids}
            chosen = []
            for score, left, right in candidates:
                if used[left] >= SEMANTIC_PER_NOTE or used[right] >= SEMANTIC_PER_NOTE:
                    continue
                pair = (min(left, right), max(left, right))
                if pair in existing:
                    continue
                used[left] += 1
                used[right] += 1
                chosen.append({
                    "source": pair[0], "target": pair[1],
                    "kind": "semantic", "score": round(score, 3),
                })
            result.update(links=chosen, count=len(chosen), ready=True)
            print(f"  semantic: {len(chosen)} links from {len(ids)} notes", flush=True)
        with _semantic_lock:
            _semantic_cache.update(result)
        return dict(result)


def retrieve(question: str, galaxy: dict, limit: int = 8) -> list[dict]:
    """Hybrid rank: keyword overlap plus local semantic similarity."""
    nodes = galaxy.get("nodes") or []
    if not nodes:
        return []
    wanted = keywords(question)
    similarities = semantic_scores(question) if wanted else {}
    scored: list[tuple[float, dict]] = []
    for node in nodes:
        title_hits = len(wanted & keywords(node.get("title", ""))) if wanted else 0
        excerpt_hits = len(wanted & keywords(node.get("excerpt", ""))) if wanted else 0
        keyword_score = title_hits * 3 + excerpt_hits
        similarity = similarities.get(node.get("id"), 0.0)
        score = keyword_score + max(0.0, similarity - 0.45) * 14.0
        if keyword_score or similarity >= 0.55:
            scored.append((score, node))
    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("title", "")).lower()))
    return [node for _, node in scored[:limit]]


# -------------------------------------------------------------- sessions ----

_sessions: dict[str, dict] = {}
_sessions_lock = threading.Lock()


def clean_session(raw) -> str:
    session = re.sub(r"[^A-Za-z0-9_-]", "", str(raw or ""))[:64]
    return session or f"anon-{uuid.uuid4().hex[:10]}"


def history_for(session: str) -> list[dict]:
    now = time.time()
    with _sessions_lock:
        stale = [key for key, value in _sessions.items()
                 if now - value["touched"] > 3 * 3600]
        for key in stale:
            _sessions.pop(key, None)
        entry = _sessions.setdefault(session, {"messages": [], "touched": now})
        entry["touched"] = now
        return list(entry["messages"])


def remember(session: str, question: str, answer: str) -> None:
    with _sessions_lock:
        entry = _sessions.setdefault(session, {"messages": [], "touched": time.time()})
        entry["messages"].extend([
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer},
        ])
        entry["messages"] = entry["messages"][-HISTORY_MESSAGES:]
        entry["touched"] = time.time()


# ------------------------------------------------------------- approvals ----

_approvals_lock = threading.Lock()
_tasks_lock = threading.Lock()
_missions_lock = threading.Lock()


def load_approvals() -> list[dict]:
    try:
        data = json.loads(APPROVALS_PATH.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def save_approvals(items: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = APPROVALS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(APPROVALS_PATH)


def pending_approvals() -> list[dict]:
    with _approvals_lock:
        return [item for item in load_approvals() if item.get("status") == "pending"]


SPEND_RE = re.compile(r"SPEND_REQUEST:\s*(\{.*\})\s*$", re.S)


def extract_spend_request(text: str) -> tuple[str, dict | None]:
    """Pull a structured spend request out of an answer, if the model filed one."""
    match = SPEND_RE.search(text or "")
    if not match:
        return text, None
    raw = match.group(1).strip().strip("`").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return text, None
    title = str(data.get("title") or "").strip()
    if not title:
        return text, None
    try:
        amount = float(data.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0.0
    return text[:match.start()].strip(), {
        "title": title,
        "amount": amount,
        "reason": str(data.get("reason") or ""),
    }


def spend_summary(spend: dict) -> str:
    """One operator-facing line for a structured spend request."""
    title = str(spend.get("title") or "Spend request").strip()
    try:
        amount = float(spend.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0.0
    if amount <= 0:
        return f"{title} — no spend."
    currency = str((config().get("money") or {}).get("currency") or "GBP")
    return f"{title} — {amount:.2f} {currency} approval needed."


SOURCE_CITATION_RE = re.compile(
    r"\s*(?:\[\s*(?:\d{1,6}|(?:id|notes?|sources?)\s*#?\s*\d{1,6}|mission\s+control)\s*\]"
    r"|\(\s*(?:\d{1,6}|(?:id|notes?|sources?)\s*#?\s*\d{1,6})\s*\)"
    r"|\b(?:id|notes?|sources?)\s*#?\s*\d{1,6}\b)",
    re.I,
)


def strip_source_citations(text: str) -> str:
    """Keep internal note ids out of operator-facing prose."""
    return SOURCE_CITATION_RE.sub("", str(text or "")).strip()


def operator_answer(answer: str, spend: dict | None) -> str:
    """Keep the model's useful prose, but replace machine spend syntax."""
    text = strip_source_citations(answer)
    if not spend:
        return text
    summary = spend_summary(spend)
    title = str(spend.get("title") or "").strip()
    if not text or text.casefold() == title.casefold() or summary.casefold() in text.casefold():
        return summary
    return f"{text}\n\n{summary}"


def create_approval(title: str, amount: float, reason: str) -> dict:
    cfg = config()
    money = cfg.get("money") or {}
    item = {
        "id": "AP-" + uuid.uuid4().hex[:6].upper(),
        "title": title[:160],
        "amount": round(amount, 2),
        "currency": money.get("currency", "GBP"),
        "reason": reason[:500],
        "status": "pending",
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "decided": None,
    }
    with _approvals_lock:
        items = load_approvals()
        items.append(item)
        save_approvals(items)
    return item


def decide_approval(approval_id: str, decision: str) -> dict | None:
    with _approvals_lock:
        items = load_approvals()
        for item in items:
            if item.get("id") == approval_id:
                if item.get("status") != "pending":
                    return item
                item["status"] = "approved" if decision == "approve" else "denied"
                item["decided"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                save_approvals(items)
                return item
    return None


ROADMAP = (
    {
        "key": "vision",
        "title": "Define the Helios Vision & Ethos site",
        "description": "Turn the mission into a public, high-trust site with no sales agenda.",
        "owner": "CEO Agent", "status": "In Plan", "priority": "High",
        "risks": "Messaging must stay unbiased; no vendor influence.", "deps": (),
    },
    {
        "key": "roles",
        "title": "Define CEO, Chief of Staff and Red Team roles",
        "description": "Write reporting lines, authority boundaries, and verification duties before adding agents.",
        "owner": "CEO Agent", "status": "In Plan", "priority": "High",
        "risks": "The CEO must not hold execution tools.", "deps": (),
    },
    {
        "key": "site",
        "title": "Publish the Vision & Ethos landing page",
        "description": "Build the Next.js, Tailwind and Framer Motion site with a brutalist-minimal, high-trust feel.",
        "owner": "Chief of Staff", "status": "In Plan", "priority": "High",
        "risks": "Zero-cost hosting and performance budget.", "deps": ("vision", "hosting"),
    },
    {
        "key": "hosting",
        "title": "Set the zero-cost hosting and publishing stack",
        "description": "Choose free-tier hosting and social channels; document the deployment path.",
        "owner": "Chief of Staff", "status": "In Plan", "priority": "Low",
        "risks": "Free-tier limits and vendor lock-in.", "deps": (),
    },
    {
        "key": "evidence",
        "title": "Build the Evidence Vault",
        "description": "Collect licensable primary sources and raw PDFs with citations and freshness dates.",
        "owner": "Research Agent", "status": "In Plan", "priority": "High",
        "risks": "Licensing and source freshness.", "deps": (),
    },
    {
        "key": "truth",
        "title": "Design the Truth-Meter",
        "description": "Calculate savings from raw UK government data rather than manufacturer estimates.",
        "owner": "Data Agent", "status": "In Analysis", "priority": "High",
        "risks": "Data quality and assumptions must be explicit.", "deps": (),
    },
    {
        "key": "noise",
        "title": "Ship the Noise Filter",
        "description": "Let visitors switch between marketing claims and the Helios truth for common industry lines.",
        "owner": "Product Agent", "status": "In Plan", "priority": "Medium",
        "risks": "Must not create new unsupported claims.", "deps": ("site", "truth"),
    },
    {
        "key": "verify",
        "title": "Ship the Verification API",
        "description": "Flag biased language or hidden costs in installer quotes and return evidence.",
        "owner": "Red Team", "status": "In Plan", "priority": "High",
        "risks": "False positives could erode trust.", "deps": ("truth",),
    },
    {
        "key": "ledger",
        "title": "Launch the Community Transparency Log",
        "description": "Publish a weekly ledger of verified energy-market updates with sources.",
        "owner": "Research Agent", "status": "In Plan", "priority": "Medium",
        "risks": "Cadence and source freshness.", "deps": ("evidence",),
    },
)


def task_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def load_tasks() -> dict:
    try:
        data = json.loads(TASKS_PATH.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"updated": None, "tasks": []}
    if not isinstance(data, dict):
        return {"updated": None, "tasks": []}
    tasks = data.get("tasks")
    return {
        "updated": data.get("updated"),
        "tasks": [task for task in tasks if isinstance(task, dict)]
        if isinstance(tasks, list) else [],
    }


def save_tasks(store: dict) -> None:
    store["updated"] = task_timestamp()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = TASKS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(TASKS_PATH)


def task_note_ids(value, galaxy: dict) -> list[int]:
    allowed = {int(node["id"]) for node in (galaxy.get("nodes") or [])}
    ids: list[int] = []
    for raw in value if isinstance(value, list) else []:
        try:
            note_id = int(raw)
        except (TypeError, ValueError):
            continue
        if note_id in allowed and note_id not in ids:
            ids.append(note_id)
    return ids[:8]


def task_dependencies(value, tasks: list[dict]) -> list[str]:
    allowed = {str(task.get("id")) for task in tasks}
    deps: list[str] = []
    for raw in value if isinstance(value, list) else []:
        dep = str(raw or "").strip()
        if dep in allowed and dep not in deps:
            deps.append(dep)
    return deps[:12]


def analyze_dependencies(tasks: list[dict]) -> dict:
    """Separate normal sequencing from genuinely broken dependencies.

    A task waiting for an unfinished prerequisite is *waiting*, not blocked.
    A task is blocked only when the dependency graph itself is broken: a
    missing task, a self-reference, an explicit hold or a cycle. Downstream
    work inherits the broken state so it cannot silently look actionable.
    """
    task_by_id: dict[str, dict] = {}
    for task in tasks:
        task_id = str(task.get("id") or "")
        if task_id and task_id not in task_by_id:
            task_by_id[task_id] = task

    statuses = {
        task_id: str(task.get("status") or "In Plan")
        for task_id, task in task_by_id.items()
    }
    done_ids = {task_id for task_id, status in statuses.items() if status == "Done"}
    dependencies: dict[str, list[str]] = {}
    for task_id, task in task_by_id.items():
        deps: list[str] = []
        for raw in task.get("dependencies") or []:
            dep = str(raw or "").strip()
            if dep and dep not in deps:
                deps.append(dep)
        dependencies[task_id] = deps

    broken: dict[str, str] = {}
    for task_id, deps in dependencies.items():
        task = task_by_id[task_id]
        if str(task.get("blocked_reason") or "").strip():
            broken.setdefault(task_id, str(task.get("blocked_reason")).strip()[:180])
        for dep in deps:
            if dep == task_id:
                broken.setdefault(task_id, "depends on itself")
            elif dep not in task_by_id:
                broken.setdefault(task_id, f"missing dependency {dep}")

    # A dependency cycle is a broken graph, not a normal queue. Mark every
    # open member of the cycle so the UI can ask for a repair.
    colors: dict[str, int] = {}
    def visit(task_id: str, stack: list[str]) -> None:
        colors[task_id] = 1
        for dep in dependencies.get(task_id, []):
            if dep not in task_by_id:
                continue
            if colors.get(dep) == 1:
                start = stack.index(dep) if dep in stack else len(stack) - 1
                for member in stack[start:]:
                    if member not in done_ids:
                        broken.setdefault(member, "dependency cycle")
            elif colors.get(dep, 0) == 0:
                visit(dep, stack + [dep])
        colors[task_id] = 2

    for task_id in task_by_id:
        if colors.get(task_id, 0) == 0:
            visit(task_id, [task_id])

    states: dict[str, str] = {}
    reasons: dict[str, str] = {}
    def state_for(task_id: str) -> str:
        if task_id in states:
            return states[task_id]
        task = task_by_id[task_id]
        status = statuses[task_id]
        state = "done" if status == "Done" else "ready"
        if state != "done":
            if task_id in broken:
                state = "blocked"
                reasons.setdefault(task_id, broken[task_id])
            else:
                for dep in dependencies[task_id]:
                    if dep in done_ids:
                        continue
                    dep_state = state_for(dep) if dep in task_by_id else "blocked"
                    if dep_state == "blocked":
                        state = "blocked"
                        reasons.setdefault(
                            task_id, f"depends on blocked {dep}")
                        break
                    state = "waiting"
                if state == "ready" and status == "In Test" and not task.get("verified"):
                    state = "verify"
        states[task_id] = state
        return state

    for task_id in task_by_id:
        state_for(task_id)

    direct_dependents: dict[str, list[str]] = {task_id: [] for task_id in task_by_id}
    for task_id, deps in dependencies.items():
        for dep in deps:
            if dep in task_by_id and task_id not in direct_dependents[dep]:
                direct_dependents[dep].append(task_id)

    waiting_on = {
        task_id: [dep for dep in deps if dep not in done_ids]
        for task_id, deps in dependencies.items()
    }

    def root_causes(task_id: str, seen: set[str] | None = None) -> set[str]:
        seen = set() if seen is None else seen
        if task_id in seen:
            return set()
        seen.add(task_id)
        roots: set[str] = set()
        for dep in dependencies.get(task_id, []):
            if dep in done_ids:
                continue
            dep_state = states.get(dep, "blocked")
            if dep_state == "ready":
                roots.add(dep)
            elif dep_state in {"waiting", "blocked"} and dep in task_by_id:
                roots |= root_causes(dep, seen)
            else:
                roots.add(dep)
        return roots

    def descendants(task_id: str, seen: set[str] | None = None) -> set[str]:
        top = seen is None
        seen = set() if seen is None else seen
        if task_id in seen:
            return seen
        seen.add(task_id)
        for child in direct_dependents.get(task_id, []):
            descendants(child, seen)
        if top:
            seen.discard(task_id)
        return seen

    def longest_chain(task_id: str, seen: set[str] | None = None) -> list[str]:
        seen = set() if seen is None else seen
        children = [child for child in direct_dependents.get(task_id, [])
                    if child not in seen and states.get(child) != "done"]
        if not children:
            return [task_id]
        best = max((longest_chain(child, seen | {task_id}) for child in children),
                   key=len)
        return [task_id] + best

    task_views: dict[str, dict] = {}
    for task_id, task in task_by_id.items():
        unlocked = descendants(task_id)
        unlocked_open = [item for item in unlocked if states.get(item) != "done"]
        chain = longest_chain(task_id)
        view = {
            "state": states[task_id],
            "waiting_on": waiting_on[task_id],
            "root_causes": sorted(root_causes(task_id)),
            "unlocks": len(unlocked_open),
            "chain": chain,
        }
        if reasons.get(task_id):
            view["reason"] = reasons[task_id]
        if broken.get(task_id):
            view["broken"] = broken[task_id]
        task_views[task_id] = view

    ready = [task_id for task_id, view in task_views.items() if view["state"] == "ready"]
    waiting = [task_id for task_id, view in task_views.items() if view["state"] == "waiting"]
    blocked = [task_id for task_id, view in task_views.items() if view["state"] == "blocked"]
    verify = [task_id for task_id, view in task_views.items() if view["state"] == "verify"]
    ranked = sorted(
        ready,
        key=lambda task_id: (
            -task_views[task_id]["unlocks"],
            -len(task_views[task_id]["chain"]),
            COMMANDER_PRIORITY.get(str(task_by_id[task_id].get("priority") or "Medium"), 9),
            task_id,
        ),
    )
    next_unlock = None
    if ranked:
        chosen = ranked[0]
        next_unlock = {
            "task_id": chosen,
            "title": str(task_by_id[chosen].get("title") or ""),
            "unlocks": task_views[chosen]["unlocks"],
            "chain": task_views[chosen]["chain"],
        }
    return {
        "tasks": task_views,
        "ready": ready,
        "waiting": waiting,
        "blocked": blocked,
        "verify": verify,
        "next_unlock": next_unlock,
        "critical_path": (next_unlock or {}).get("chain") or [],
    }


def task_summary(tasks: list[dict]) -> dict:
    counts = {status: 0 for status in TASK_STATUSES}
    unverified = 0
    for task in tasks:
        status = str(task.get("status") or "In Plan")
        if status in counts:
            counts[status] += 1
        if status != "Done" and not task.get("verified"):
            unverified += 1
    analysis = analyze_dependencies(tasks)
    return {
        "total": len(tasks),
        "by_status": counts,
        "done": counts.get("Done", 0),
        "active": len(tasks) - counts.get("Done", 0),
        "ready": len(analysis["ready"]),
        "waiting": len(analysis["waiting"]),
        "blocked": len(analysis["blocked"]),
        "verify": len(analysis["verify"]),
        "unverified": unverified,
        "next_unlock": analysis["next_unlock"],
        "critical_path": analysis["critical_path"],
    }


def load_missions() -> dict:
    try:
        data = json.loads(MISSIONS_PATH.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"updated": None, "missions": []}
    if not isinstance(data, dict):
        return {"updated": None, "missions": []}
    missions = data.get("missions")
    return {
        "updated": data.get("updated"),
        "missions": [mission for mission in missions if isinstance(mission, dict)]
        if isinstance(missions, list) else [],
    }


def save_missions(store: dict) -> None:
    store["updated"] = task_timestamp()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MISSIONS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(MISSIONS_PATH)


def mission_progress(mission: dict, tasks: list[dict]) -> dict:
    owned = [task for task in tasks
             if str(task.get("mission_id") or "") == str(mission.get("id"))]
    summary = task_summary(owned)
    total = summary["total"]
    summary["done_percent"] = round(100 * summary["done"] / total) if total else 0
    stage_points = sum(TASK_STAGE_PROGRESS.get(
        str(task.get("status") or "In Plan"), 0) for task in owned)
    summary["percent"] = round(stage_points / total) if total else 0
    return summary


def mission_health(mission: dict, tasks: list[dict]) -> dict:
    """Small, explainable health signal; no invented deadlines or velocity."""
    lifecycle = str(mission.get("lifecycle") or "active")
    if lifecycle == "cancelled":
        return {"status": "cancelled", "label": "Cancelled",
                "reasons": ["The operator cancelled this mission; its tasks are archived in run history."]}
    if lifecycle == "archived":
        return {"status": "archived", "label": "Archived",
                "reasons": ["This mission was archived when a fresh run was started."]}
    progress = mission_progress(mission, tasks)
    total = int(progress.get("total") or 0)
    done = int(progress.get("done") or 0)
    stage_percent = int(progress.get("percent") or 0)
    blocked = int(progress.get("blocked") or 0)
    waiting = int(progress.get("waiting") or 0)
    ready = int(progress.get("ready") or 0)
    next_unlock = progress.get("next_unlock") or {}
    if not total:
        status, label = "empty", "No plan"
        reasons = ["No tasks are attached yet."]
    elif done == total:
        status, label = "complete", "Complete"
        reasons = ["Every task is signed off and done."]
    elif blocked:
        status, label = "blocked", "Blocked"
        reasons = [
            f"{blocked} task{'s' if blocked != 1 else ''} have a broken dependency link "
            "that needs repair; this is not normal sequencing."
        ]
    elif done == 0 and stage_percent > 0:
        status, label = "moving", "In motion"
        reasons = [
            f"Planning is moving: {stage_percent}% stage progress across {total} tasks. "
            "Delivery evidence is still required before anything is Done."
        ]
    elif waiting and done == 0 and ready:
        status, label = "ready", "Ready to start"
        reasons = [
            f"{ready} tasks are ready now; {waiting} are sequenced behind them. "
            f"Start with {next_unlock.get('task_id', 'the first ready task')} — "
            f"{next_unlock.get('title', 'the critical-path deliverable')}."
        ]
    elif waiting:
        status, label = "sequenced", "Sequenced"
        reasons = [
            f"{done} of {total} done; {waiting} tasks are waiting on a predecessor, "
            "not broken. The critical path is still the fastest route."
        ]
    elif done == 0:
        status, label = "ready", "Ready to start"
        reasons = ["No verified progress yet; every open task can start."]
    else:
        status, label = "on-track", "On track"
        reasons = [f"{done} of {total} tasks done; {progress.get('unverified', 0)} awaiting evidence."]
    return {"status": status, "label": label, "reasons": reasons}


def mission_gates(mission: dict, tasks: list[dict]) -> list[dict]:
    """Operational readiness gates that are safe to inspect before closure."""
    owned = [task for task in tasks
             if str(task.get("mission_id") or "") == str(mission.get("id") or "")]
    progress = mission_progress(mission, tasks)
    total = int(progress.get("total") or 0)
    routed = sum(1 for task in owned if task.get("persona"))
    lead = crew.resolve(mission.get("lead_persona")) or crew.resolve(mission.get("owner"))
    return [
        {"key": "plan", "label": "Plan", "ok": total > 0,
         "detail": f"{total} task{'s' if total != 1 else ''} attached"},
        {"key": "lead", "label": "Lead", "ok": bool(lead),
         "detail": lead["name"] if lead else "No lead persona"},
        {"key": "crew", "label": "Crew routed", "ok": total > 0 and routed == total,
         "detail": f"{routed}/{total} tasks assigned"},
        {"key": "dependencies", "label": "Dependencies", "ok": int(progress.get("blocked") or 0) == 0
         and int(progress.get("waiting") or 0) == 0,
         "detail": f"{progress.get('waiting', 0)} waiting · {progress.get('blocked', 0)} broken"},
        {"key": "evidence", "label": "Evidence", "ok": total > 0 and progress.get("done") == total,
         "detail": f"{progress.get('done', 0)}/{total} tasks verified"},
    ]


def mission_review(mission: dict, tasks: list[dict]) -> dict:
    """Explain every readiness gate and name the next accountable action.

    This is deliberately read-only. An operator decision is stored separately
    and never moves a task, marks evidence or changes task state.
    """
    view = mission_view(mission, tasks)
    gates = view["gates"]
    owned = [task for task in tasks
             if str(task.get("mission_id") or "") == str(mission.get("id") or "")]
    mission_type = str(mission.get("type") or "")
    task_by_id = {str(task.get("id")): task for task in tasks}
    dependency_analysis = analyze_dependencies(tasks)
    blockers: list[dict] = []
    for gate in gates:
        if gate["ok"]:
            continue
        key = str(gate["key"])
        if key == "plan":
            blockers.append({
                "gate_key": key, "gate": gate["label"],
                "title": "No tasks attached", "detail": gate["detail"],
                "task_id": "",
                "persona": crew.public(crew.get("project-manager")),
            })
        elif key == "lead":
            blockers.append({
                "gate_key": key, "gate": gate["label"],
                "title": "No accountable lead", "detail": gate["detail"],
                "task_id": "",
                "persona": crew.public(crew.get("mission-controller")),
            })
        elif key == "crew":
            for task in owned:
                if task.get("persona"):
                    continue
                persona = crew.for_task(task, mission_type)
                blockers.append({
                    "gate_key": key, "gate": gate["label"],
                    "title": str(task.get("title") or "Untitled task"),
                    "detail": "No persona assigned yet.",
                    "task_id": str(task.get("id") or ""),
                    "persona": crew.public(persona),
                })
        elif key == "dependencies":
            for task in owned:
                task_id = str(task.get("id") or "")
                meta = dependency_analysis["tasks"].get(task_id, {})
                state = meta.get("state")
                if state not in {"waiting", "blocked"}:
                    continue
                if state == "blocked":
                    reason = (meta.get("reason") or meta.get("broken")
                              or "broken dependency link")
                    blockers.append({
                        "gate_key": key, "gate": gate["label"], "kind": "broken",
                        "title": str(task.get("title") or "Untitled task"),
                        "detail": f"Broken dependency: {reason}.",
                        "task_id": task_id,
                        "persona": crew.public(crew.get("admin")),
                    })
                    continue
                roots = [str(dep) for dep in meta.get("root_causes") or []
                         if str(dep) in task_by_id]
                waiting_on = [str(dep) for dep in meta.get("waiting_on") or []
                              if str(dep) in task_by_id]
                next_id = roots[0] if roots else (waiting_on[0] if waiting_on else task_id)
                next_task = task_by_id.get(next_id) or task
                next_title = str(next_task.get("title") or next_id)
                root_text = ", ".join(roots[:3]) or next_id
                blockers.append({
                    "gate_key": key, "gate": gate["label"], "kind": "sequence",
                    "title": str(task.get("title") or "Untitled task"),
                    "detail": f"Waiting on {root_text} — next unlock {next_id} {next_title[:70]}.",
                    "task_id": task_id,
                    "next_task_id": next_id,
                    "next_task_title": next_title,
                    "persona": crew.public(crew.for_task(next_task, mission_type)),
                })
        elif key == "evidence":
            for task in owned:
                if str(task.get("status") or "") != "In Test" or task.get("verified"):
                    continue
                blockers.append({
                    "gate_key": key, "gate": gate["label"],
                    "title": str(task.get("title") or "Untitled task"),
                    "detail": "In Test and waiting for independent evidence.",
                    "task_id": str(task.get("id") or ""),
                    "persona": crew.public(crew.get("qa-red-team")),
                })
    blockers = blockers[:8]
    open_gates = [gate for gate in gates if not gate["ok"]]
    if not open_gates:
        recommendation = {
            "key": "complete", "label": "All gates clear",
            "action": "Every evidence gate is clear; the mission is ready to close.",
            "rationale": "Every readiness gate is clear. A review does not change task state.",
            "task_id": "",
            "persona": crew.public(crew.get("mission-controller")),
        }
    else:
        gate = open_gates[0]
        actions = {
            "plan": ("Attach the first concrete deliverable.",
                     "Without tasks there is nothing to route, gate or verify."),
            "lead": ("Name an accountable lead persona.",
                     "A lead owns the gate decision and the crew handoff."),
            "crew": ("Attach a crew brief to every open task.",
                     "Unrouted work has no accountable owner or delivery contract."),
            "dependencies": ("Focus the next unlock.",
                             "Sequenced work starts at an unblocked prerequisite; a broken link needs repair."),
            "evidence": ("Complete independent verification before Done.",
                         "Done is only valid when a reviewer has checked the evidence."),
        }
        action, rationale = actions.get(
            str(gate["key"]),
            (str(gate.get("detail") or "Clear this gate."), "This gate is still holding."),
        )
        blocker = next((item for item in blockers
                        if item["gate_key"] == gate["key"]), None)
        next_task_id = (blocker or {}).get("next_task_id") or (blocker or {}).get("task_id") or ""
        next_task_title = ((blocker or {}).get("next_task_title")
                           or (blocker or {}).get("title") or "")
        if blocker and next_task_id:
            if blocker.get("next_task_id"):
                action = (f"{action} Advance {next_task_id}: {next_task_title[:90]} "
                          f"so {blocker['task_id']} can start.")
            else:
                action = f"{action} Start with {next_task_id}: {next_task_title[:90]}."
            rationale = f"{rationale} {blocker['detail']}"
        recommendation = {
            "key": gate["key"], "label": gate["label"],
            "kind": (blocker or {}).get("kind", ""),
            "action": action, "rationale": rationale,
            "task_id": next_task_id or "",
            "persona": (blocker or {}).get("persona") or crew.public(
                crew.get({"plan": "project-manager", "lead": "mission-controller"}.get(
                    str(gate["key"]), "mission-controller"))
            ),
        }
    gates_clear = sum(1 for gate in gates if gate["ok"])
    return {
        "id": str(mission.get("id") or ""),
        "title": str(mission.get("title") or ""),
        "goal": str(mission.get("goal") or ""),
        "type": str(mission.get("type") or "Custom"),
        "health": view["health"], "progress": view["progress"],
        "lead": view["lead"], "crew": view["crew"], "gates": gates,
        "gates_clear": gates_clear, "gates_total": len(gates),
        "readiness": ("Ready for close-out" if not open_gates
                      else f"{len(open_gates)} gate{'s' if len(open_gates) != 1 else ''} holding"),
        "open_gates": open_gates,
        "blockers": blockers,
        "waiting": [item for item in blockers if item.get("kind") == "sequence"],
        "broken": [item for item in blockers if item.get("kind") == "broken"],
        "next": recommendation,
        "decisions": list(mission.get("review_decisions") or [])[:12],
        "principles": [
            "A review is a judgement, not a status change.",
            "Evidence comes before Done; no task is closed without it.",
            "One accountable persona per next action.",
        ],
        "safe": "Recording a review never changes task status, verification or spend.",
    }


def mission_view(mission: dict, tasks: list[dict]) -> dict:
    lead = crew.resolve(mission.get("lead_persona")) or crew.resolve(mission.get("owner"))
    progress = mission_progress(mission, tasks)
    run = max(1, int(mission.get("run") or 1))
    lifecycle = str(mission.get("lifecycle") or "active")
    run_history = []
    for item in (mission.get("runs") or [])[:8]:
        if not isinstance(item, dict):
            continue
        run_history.append({
            "id": str(item.get("id") or ""),
            "run": int(item.get("run") or 1),
            "action": str(item.get("action") or "restart"),
            "label": MISSION_CONTROL_ACTIONS.get(str(item.get("action") or ""), "Run archived"),
            "at": item.get("at"), "actor": item.get("actor") or "Operator",
            "task_count": int(item.get("task_count") or 0),
            "done": int((item.get("progress") or {}).get("done") or 0),
            "total": int((item.get("progress") or {}).get("total") or 0),
            "percent": int((item.get("progress") or {}).get("percent") or 0),
            "note": str(item.get("note") or ""),
        })
    view = {key: value for key, value in mission.items() if key != "runs"}
    return {
        **view,
        "lifecycle": lifecycle,
        "run": run,
        "run_history": run_history,
        "progress": progress,
        "health": mission_health(mission, tasks),
        "gates": mission_gates(mission, tasks),
        "dependencies": {
            "next_unlock": progress.get("next_unlock"),
            "critical_path": progress.get("critical_path") or [],
            "ready": progress.get("ready", 0),
            "waiting": progress.get("waiting", 0),
            "blocked": progress.get("blocked", 0),
        },
        "lead": crew.public(lead),
        "crew": crew.mission_crew(mission, tasks),
        "last_review": (mission.get("review_decisions") or [None])[0],
        "review_count": len(mission.get("review_decisions") or []),
    }


def ensure_mission(title: str, mission_type: str, goal: str, owner: str) -> dict:
    """Find a mission by title or create it without template tasks."""
    with _missions_lock:
        store = load_missions()
        missions = store["missions"]
        for mission in missions:
            if str(mission.get("title") or "").casefold() == title.casefold():
                return dict(mission)
        highest = 0
        for mission in missions:
            match = re.fullmatch(r"M-(\d+)", str(mission.get("id") or ""))
            if match:
                highest = max(highest, int(match.group(1)))
        now = task_timestamp()
        lead = crew.resolve(owner) or crew.get("mission-controller")
        mission = {
            "id": f"M-{highest + 1:04d}",
            "title": title[:160],
            "type": mission_type if mission_type in MISSION_TYPES else "Custom",
            "goal": goal[:500],
            "owner": lead["name"],
            "lead_persona": lead["id"],
            "created": now,
            "updated": now,
        }
        missions.append(mission)
        save_missions(store)
        return dict(mission)


def mission_template_tasks(mission: dict, tasks: list[dict],
                           actor: str = "Mission Controller",
                           note: str = "") -> list[dict]:
    """Attach a clean template plan to a mission without touching other work."""
    mission_type = str(mission.get("type") or "Custom")
    template = MISSION_TEMPLATES.get(mission_type, MISSION_TEMPLATES["Custom"])
    title = str(mission.get("title") or "Mission")
    goal = str(mission.get("goal") or "")
    highest = 0
    for task in tasks:
        match = re.fullmatch(r"T-(\d+)", str(task.get("id") or ""))
        if match:
            highest = max(highest, int(match.group(1)))
    now = task_timestamp()
    created = []
    key_to_id = {}
    for item in template:
        highest += 1
        persona = crew.resolve(item["owner"]) or crew.get("project-manager")
        owner = persona["name"] if persona else item["owner"]
        dependencies = [key_to_id[dep] for dep in item["deps"] if dep in key_to_id]
        task = {
            "id": f"T-{highest:04d}",
            "title": f"{title[:90]}: {item['title']}",
            "description": f"Mission: {goal or title}. {item['title']}.",
            "status": item["status"],
            "owner": owner,
            "persona": persona["id"] if persona else "",
            "priority": item["priority"],
            "dependencies": dependencies,
            "risks": item["risks"],
            "note_ids": [],
            "mission_id": str(mission.get("id") or ""),
            "verified": False,
            "verified_by": "",
            "verified_at": None,
            "verification": "",
            "created": now,
            "updated": now,
            "history": [{"at": now, "actor": actor,
                         "change": (note or f"created with mission {mission.get('id')}")[:280]}],
        }
        tasks.append(task)
        key_to_id[item["key"]] = task["id"]
        created.append(task)
    return created


def create_mission(body: dict) -> tuple[dict, int]:
    title = str(body.get("title") or "").strip()[:160]
    if not title:
        return {"error": "mission title required"}, 400
    mission_type = str(body.get("type") or "Custom").strip()
    if mission_type not in MISSION_TYPES:
        mission_type = "Custom"
    goal = str(body.get("goal") or "").strip()[:500]
    lead = (crew.resolve(body.get("lead_persona"))
            or crew.resolve(body.get("lead"))
            or crew.resolve(body.get("owner"))
            or crew.get("mission-controller"))
    with _missions_lock:
        store = load_missions()
        missions = store["missions"]
        if any(str(mission.get("title") or "").casefold() == title.casefold()
               for mission in missions):
            return {"error": "a mission with that title already exists"}, 409
        highest = 0
        for mission in missions:
            match = re.fullmatch(r"M-(\d+)", str(mission.get("id") or ""))
            if match:
                highest = max(highest, int(match.group(1)))
        now = task_timestamp()
        mission = {
            "id": f"M-{highest + 1:04d}",
            "title": title,
            "type": mission_type,
            "goal": goal,
            "owner": lead["name"],
            "lead_persona": lead["id"],
            "created": now,
            "updated": now,
        }
        missions.append(mission)
        save_missions(store)
    template = MISSION_TEMPLATES.get(mission_type, MISSION_TEMPLATES["Custom"])
    with _tasks_lock:
        task_store = load_tasks()
        tasks = task_store["tasks"]
        highest_task = 0
        for task in tasks:
            match = re.fullmatch(r"T-(\d+)", str(task.get("id") or ""))
            if match:
                highest_task = max(highest_task, int(match.group(1)))
        now = task_timestamp()
        key_to_id = {}
        for item in template:
            highest_task += 1
            persona = crew.resolve(item["owner"]) or crew.get("project-manager")
            owner = persona["name"] if persona else item["owner"]
            dependencies = [key_to_id[dep] for dep in item["deps"]
                            if dep in key_to_id]
            task = {
                "id": f"T-{highest_task:04d}",
                "title": f"{title[:90]}: {item['title']}",
                "description": f"Mission: {goal or title}. {item['title']}.",
                "status": item["status"],
                "owner": owner,
                "persona": persona["id"] if persona else "",
                "priority": item["priority"],
                "dependencies": dependencies,
                "risks": item["risks"],
                "note_ids": [],
                "mission_id": mission["id"],
                "verified": False,
                "verified_by": "",
                "verified_at": None,
                "verification": "",
                "created": now,
                "updated": now,
                "history": [{"at": now, "actor": "CEO Agent",
                             "change": f"created with mission {mission['id']}"}],
            }
            tasks.append(task)
            key_to_id[item["key"]] = task["id"]
        save_tasks(task_store)
        summary = task_summary(tasks)
    record_activity(
        "mission", f"Mission created: {title}",
        f"{mission_type} mission with {len(template)} template tasks; lead {lead['name']}.",
        actor="Mission Controller", mission_id=mission["id"],
    )
    return {"mission": mission_view(mission, tasks),
            "tasks": len(template), "summary": summary}, 201


def missions_payload() -> dict:
    missions = load_missions()["missions"]
    tasks = load_tasks()["tasks"]
    views = [mission_view(mission, tasks) for mission in missions]
    total_tasks = sum(view["progress"]["total"] for view in views)
    done_tasks = sum(view["progress"]["done"] for view in views)
    stage_percent = round(
        sum(view["progress"]["percent"] * view["progress"]["total"] for view in views)
        / total_tasks
    ) if total_tasks else 0
    return {
        "missions": views,
        "types": list(MISSION_TYPES),
        "controls": [{"action": action, "label": label,
                      "note": MISSION_CONTROL_NOTES[action]}
                     for action, label in MISSION_CONTROL_ACTIONS.items()],
        "summary": {
            "total": len(views),
            "active": sum(1 for view in views if view.get("lifecycle", "active") == "active"
                          and (view["progress"]["total"] == 0
                               or view["progress"]["done"] < view["progress"]["total"])),
            "complete": sum(1 for view in views if view.get("lifecycle", "active") == "active"
                            and view["progress"]["total"]
                            and view["progress"]["done"] == view["progress"]["total"]),
            "cancelled": sum(1 for view in views if view.get("lifecycle") == "cancelled"),
            "archived": sum(1 for view in views if view.get("lifecycle") == "archived"),
            "tasks": total_tasks,
            "done": done_tasks,
            "percent": stage_percent,
            "done_percent": round(100 * done_tasks / total_tasks) if total_tasks else 0,
            "inactive": sum(1 for view in views if view.get("lifecycle") in INACTIVE_MISSION_STATES),
        },
    }


def control_mission(body: dict) -> tuple[dict, int]:
    """Operator-gated mission lifecycle controls with run snapshots.

    This is deliberately separate from task sign-off. It can reset or replace
    active planning data, but it never marks work done, spends, publishes or
    sends anything. Every mutation stores the previous run first.
    """
    mission_id = str(body.get("mission_id") or body.get("id") or "").strip()
    action = str(body.get("action") or "").strip().lower()
    note = str(body.get("note") or "").strip()[:400]
    snapshot_id = str(body.get("snapshot_id") or "").strip()[:40]
    snapshot_at = str(body.get("snapshot_at") or "").strip()[:40]
    try:
        source_run = int(body.get("run") or 0)
    except (TypeError, ValueError):
        source_run = 0
    if not mission_id:
        return {"error": "mission id required"}, 400
    if action not in MISSION_CONTROL_ACTIONS:
        return {"error": "action must be restart, rebuild, fresh, cancel or restore"}, 400
    with _mission_control_lock:
        with _missions_lock:
            mission_store = load_missions()
            missions = mission_store["missions"]
            mission = next((item for item in missions
                            if str(item.get("id")) == mission_id), None)
            if mission is None:
                return {"error": "mission not found"}, 404
            lifecycle = str(mission.get("lifecycle") or "active")
            if lifecycle == "archived":
                return {"error": "this mission is archived; open its successor instead"}, 409
            if action == "cancel" and lifecycle == "cancelled":
                return {"error": "this mission is already cancelled"}, 409
            source_snapshot = None
            if action == "restore":
                history = [item for item in mission.get("runs") or []
                           if isinstance(item, dict)]
                if snapshot_id:
                    source_snapshot = next((item for item in history
                                            if str(item.get("id") or "") == snapshot_id), None)
                if source_snapshot is None and snapshot_at:
                    source_snapshot = next((item for item in history
                                            if str(item.get("at") or "") == snapshot_at), None)
                if source_snapshot is None and source_run:
                    source_snapshot = next((item for item in history
                                            if int(item.get("run") or 0) == source_run), None)
                if source_snapshot is None:
                    return {"error": "saved run not found"}, 404
                if not source_snapshot.get("tasks"):
                    return {"error": "that saved run has no tasks to restore"}, 409
            with _tasks_lock:
                task_store = load_tasks()
                tasks = task_store["tasks"]
                owned = [task for task in tasks
                         if str(task.get("mission_id") or "") == mission_id]
                if action == "restart" and not owned:
                    return {"error": "this mission has no plan to restart; rebuild it instead"}, 409
                if action == "restore":
                    occupied = {str(task.get("id") or "") for task in tasks
                                if str(task.get("mission_id") or "") != mission_id}
                    conflicts = sorted({str(task.get("id") or "")
                                        for task in source_snapshot.get("tasks") or []
                                        if isinstance(task, dict)
                                        and str(task.get("id") or "") in occupied})
                    if conflicts:
                        return {"error": "cannot restore: task ids now belong to another mission ("
                                         + ", ".join(conflicts[:4]) + ")"}, 409
                now = task_timestamp()
                current_run = max(1, int(mission.get("run") or 1))
                progress = mission_progress(mission, tasks)
                snapshot = {
                    "id": "RS-" + uuid.uuid4().hex[:10].upper(),
                    "run": current_run, "action": action, "at": now,
                    "actor": "Operator", "note": note,
                    "lifecycle": lifecycle,
                    "task_count": len(owned),
                    "progress": {"done": progress.get("done", 0),
                                 "total": progress.get("total", 0),
                                 "percent": progress.get("percent", 0),
                                 "blocked": progress.get("blocked", 0)},
                    "tasks": [dict(task) for task in owned],
                }
                mission["runs"] = [snapshot] + list(mission.get("runs") or [])[:9]
                new_mission = None
                created = []
                reset = 0
                restored = 0
                removed = 0
                if action == "restart":
                    mission["run"] = current_run + 1
                    mission["lifecycle"] = "active"
                    mission.pop("cancelled_at", None)
                    mission.pop("cancelled_note", None)
                    for task in owned:
                        task["status"] = "In Plan"
                        task["verified"] = False
                        task["verified_by"] = ""
                        task["verified_at"] = None
                        task["verification"] = ""
                        task["updated"] = now
                        task.setdefault("history", []).append({
                            "at": now, "actor": "Operator",
                            "change": f"reset for run {current_run + 1}",
                        })
                        reset += 1
                elif action == "rebuild":
                    removed = len(owned)
                    tasks = [task for task in tasks
                             if str(task.get("mission_id") or "") != mission_id]
                    mission["run"] = current_run + 1
                    mission["lifecycle"] = "active"
                    mission.pop("cancelled_at", None)
                    mission.pop("cancelled_note", None)
                    created = mission_template_tasks(
                        mission, tasks, actor="Operator",
                        note=f"rebuilt for run {current_run + 1}: {MISSION_CONTROL_NOTES[action]}",
                    )
                elif action == "restore":
                    removed = len(owned)
                    tasks = [task for task in tasks
                             if str(task.get("mission_id")) != mission_id]
                    for raw_task in source_snapshot.get("tasks") or []:
                        if not isinstance(raw_task, dict):
                            continue
                        restored_task = json.loads(json.dumps(raw_task))
                        restored_task["mission_id"] = mission_id
                        restored_task["updated"] = now
                        restored_task.setdefault("history", []).append({
                            "at": now, "actor": "Operator",
                            "change": (f"restored from run {source_snapshot.get('run')}"
                                       f" snapshot {source_snapshot.get('at')}"),
                        })
                        tasks.append(restored_task)
                        restored += 1
                    mission["run"] = current_run + 1
                    mission["lifecycle"] = "active"
                    mission["restored_from"] = {
                        "id": source_snapshot.get("id") or "",
                        "run": source_snapshot.get("run"),
                        "at": source_snapshot.get("at"),
                        "snapshot_at": now,
                    }
                    mission.pop("cancelled_at", None)
                    mission.pop("cancelled_note", None)
                    mission.pop("archived_at", None)
                elif action == "cancel":
                    removed = len(owned)
                    tasks = [task for task in tasks
                             if str(task.get("mission_id")) != mission_id]
                    mission["lifecycle"] = "cancelled"
                    mission["cancelled_at"] = now
                    mission["cancelled_note"] = note
                elif action == "fresh":
                    removed = len(owned)
                    tasks = [task for task in tasks
                             if str(task.get("mission_id")) != mission_id]
                    mission["lifecycle"] = "archived"
                    mission["archived_at"] = now
                    highest_mission = 0
                    for item in missions:
                        match = re.fullmatch(r"M-(\d+)", str(item.get("id") or ""))
                        if match:
                            highest_mission = max(highest_mission, int(match.group(1)))
                    new_mission = {
                        "id": f"M-{highest_mission + 1:04d}",
                        "title": str(mission.get("title") or "Mission"),
                        "type": str(mission.get("type") or "Custom"),
                        "goal": str(mission.get("goal") or ""),
                        "owner": str(mission.get("owner") or "Mission Controller"),
                        "lead_persona": str(mission.get("lead_persona") or "mission-controller"),
                        "created": now, "updated": now, "run": 1,
                        "lifecycle": "active", "lineage": mission_id,
                        "fresh_from": mission_id,
                    }
                    missions.append(new_mission)
                    created = mission_template_tasks(
                        new_mission, tasks, actor="Operator",
                        note=f"fresh mission from {mission_id}",
                    )
                mission["updated"] = now
                task_store["tasks"] = tasks
                save_tasks(task_store)
                save_missions(mission_store)
        target_id = str((new_mission or mission).get("id") or mission_id)
        label = MISSION_CONTROL_ACTIONS[action]
        if action == "fresh":
            record_activity("mission", f"Fresh mission: {mission.get('title')}",
                            f"Archived {mission_id} run {current_run} ("
                            f"{snapshot['progress']['done']}/{snapshot['progress']['total']} done) "
                            f"and started {target_id} with {len(created)} clean tasks. "
                            "Run history retained.",
                            actor="Operator", mission_id=target_id)
            record_activity("mission", f"Mission archived: {mission.get('title')}",
                            f"Superseded by fresh mission {target_id}. No spend, publish or sign-off.",
                            actor="Operator", mission_id=mission_id)
        else:
            next_run = current_run + 1 if action in {"restart", "rebuild", "restore"} else current_run
            count = (restored if action == "restore"
                     else len(created) if action in {"rebuild", "fresh"}
                     else reset if action == "restart" else removed)
            verb = ("restored" if action == "restore"
                    else "created" if action in {"rebuild", "fresh"}
                    else "reset" if action == "restart" else "archived")
            detail = (f"{label} · run {current_run} → run {next_run}. "
                      f"{count} tasks {verb}. No spend, publish, send or sign-off.")
            record_activity("mission", f"{label}: {mission.get('title')}", detail,
                            actor="Operator", mission_id=mission_id)
        tasks_after = load_tasks()["tasks"]
        all_missions = load_missions()["missions"]
        target = next((item for item in all_missions if str(item.get("id")) == target_id), {})
        return {
            "ok": True, "action": action, "label": label,
            "mission_id": target_id,
            "previous_mission_id": mission_id,
            "created": len(created), "reset": reset, "restored": restored,
            "archived_tasks": removed,
            "mission": mission_view(target, tasks_after) if target else {},
            "missions": missions_payload(),
            "tasks": tasks_payload(),
            "note": MISSION_CONTROL_NOTES[action],
        }, 200


def mission_review_payload(mission_id: str) -> tuple[dict, int]:
    mission_id = str(mission_id or "").strip()
    if not mission_id:
        return {"error": "mission id required"}, 400
    missions = load_missions()["missions"]
    tasks = load_tasks()["tasks"]
    mission = next((item for item in missions
                    if str(item.get("id")) == mission_id), None)
    if mission is None:
        return {"error": "mission not found"}, 404
    review = mission_review(mission, tasks)
    return {
        "mission": mission_view(mission, tasks),
        "review": review,
        "decisions": review["decisions"],
    }, 200


def record_mission_decision(body: dict) -> tuple[dict, int]:
    """Persist an operator review judgement without changing the board."""
    mission_id = str(body.get("mission_id") or body.get("id") or "").strip()
    if not mission_id:
        return {"error": "mission id required"}, 400
    decision = str(body.get("decision") or "").strip().lower()
    if decision not in MISSION_REVIEW_DECISIONS:
        return {"error": "decision must be advance, hold or escalate"}, 400
    note = str(body.get("note") or "").strip()[:500]
    if not note and decision in {"hold", "escalate"}:
        return {"error": "a note is required for hold or escalate"}, 400
    with _missions_lock:
        store = load_missions()
        missions = store["missions"]
        mission = next((item for item in missions
                        if str(item.get("id")) == mission_id), None)
        if mission is None:
            return {"error": "mission not found"}, 404
        tasks = load_tasks()["tasks"]
        gates = mission_gates(mission, tasks)
        at = task_timestamp()
        label = MISSION_REVIEW_DECISIONS[decision]
        record = {
            "at": at, "decision": decision, "label": label,
            "note": note, "actor": "Operator",
            "gates_clear": sum(1 for gate in gates if gate["ok"]),
            "gates_total": len(gates),
            "gate_snapshot": [
                {"key": gate["key"], "label": gate["label"], "ok": gate["ok"]}
                for gate in gates
            ],
        }
        decisions = list(mission.get("review_decisions") or [])
        decisions.insert(0, record)
        mission["review_decisions"] = decisions[:20]
        mission["last_reviewed"] = at
        mission["updated"] = at
        save_missions(store)
        view = mission_view(mission, tasks)
        review = mission_review(mission, tasks)
    record_activity(
        "review", f"Mission review: {label} — {mission.get('title')}",
        f"{record['gates_clear']}/{record['gates_total']} gates clear. {note}".strip(),
        actor="Operator", mission_id=mission_id,
    )
    return {"decision": record, "mission": view, "review": review}, 200


# ---------------------------------------------------------------- studio ----

STUDIO_IDEA_STATES = ("open", "accepted", "parked", "rejected")


def load_ideas_store() -> dict:
    try:
        data = json.loads(IDEAS_PATH.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"updated": None, "ideas": []}
    if not isinstance(data, dict):
        return {"updated": None, "ideas": []}
    ideas = data.get("ideas")
    return {
        "updated": data.get("updated"),
        "ideas": [item for item in ideas if isinstance(item, dict)]
        if isinstance(ideas, list) else [],
    }


def save_ideas_store(store: dict) -> None:
    store["updated"] = task_timestamp()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = IDEAS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(IDEAS_PATH)


def load_learning_store() -> dict:
    try:
        data = json.loads(LEARNINGS_PATH.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"updated": None, "lessons": [], "cycle_count": 0,
                "last_cycle": "", "last_refresh": "", "thoughts": [],
                "last_thought": {}}
    if not isinstance(data, dict):
        return {"updated": None, "lessons": [], "cycle_count": 0,
                "last_cycle": "", "last_refresh": "", "thoughts": [],
                "last_thought": {}}
    lessons = data.get("lessons")
    thoughts = data.get("thoughts")
    last_thought = data.get("last_thought")
    return {
        "updated": data.get("updated"),
        "lessons": [item for item in lessons if isinstance(item, dict)]
        if isinstance(lessons, list) else [],
        "cycle_count": int(data.get("cycle_count") or 0),
        "last_cycle": str(data.get("last_cycle") or ""),
        "last_refresh": str(data.get("last_refresh") or ""),
        "thoughts": [item for item in thoughts if isinstance(item, dict)][:20]
        if isinstance(thoughts, list) else [],
        "last_thought": last_thought if isinstance(last_thought, dict) else {},
    }


def save_learning_store(store: dict) -> None:
    store["updated"] = task_timestamp()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LEARNINGS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(LEARNINGS_PATH)


def idea_stable_id(kind: str, task_id: str, title: str) -> str:
    digest = hashlib.sha1(f"{kind}|{task_id}|{title}".encode("utf-8")).hexdigest()
    return f"I-{digest[:8]}"


GENERIC_NOTE_WORDS = frozenset({
    "helios", "assistant", "complete", "task", "tasks", "review", "report",
    "results", "log", "test", "testing", "app", "project", "build", "update",
    "note", "notes", "guide", "plan", "checklist", "verification", "verified",
    "runtime", "board", "mission", "studio", "idea", "ideas", "work", "working",
    "document", "doc", "docs", "readme", "summary", "overview", "final", "new",
    "using", "use", "make", "made", "keep", "check", "checks", "status", "draft",
    "content", "page", "site", "website", "current", "next", "complete", "completed",
})


def best_note_for_task(task: dict, galaxy: dict, used: set[str]) -> dict | None:
    """Pick one saved note that genuinely overlaps the task, without inventing a source."""
    wanted = keywords(" ".join(str(task.get(key) or "") for key in (
        "title", "description", "risks"
    ))) - GENERIC_NOTE_WORDS
    if not wanted:
        return None
    best = None
    best_score = 0
    for node in galaxy.get("nodes") or []:
        node_id = str(node.get("id") or "")
        if not node_id or node_id in used:
            continue
        text = " ".join(str(node.get(key) or "") for key in (
            "title", "group", "excerpt"
        ))
        overlap = (wanted & keywords(text)) - GENERIC_NOTE_WORDS
        title_overlap = (wanted & keywords(str(node.get("title") or ""))) - GENERIC_NOTE_WORDS
        if len(title_overlap) < 2:
            continue
        score = len(overlap) * 2 + len(title_overlap) * 3
        if score > best_score:
            best_score = score
            best = node
    return best if best_score >= 4 else None


def generate_ideas(tasks: list[dict], missions: list[dict], galaxy: dict,
                   learnings: dict | None = None) -> list[dict]:
    """Generate grounded, reversible ideas from the live board and saved notes."""
    analysis = analyze_dependencies(tasks)
    task_by_id = {str(task.get("id")): task for task in tasks}
    mission_by_id = {str(mission.get("id")): mission for mission in missions}
    ideas: list[dict] = []
    seen: set[str] = set()
    used_notes: set[str] = set()
    now = task_timestamp()

    def add(kind: str, task: dict, title: str, summary: str, next_move: str,
            impact: str, effort: str, tags: list[str],
            note: dict | None = None) -> None:
        task_id = str(task.get("id") or "")
        idea_id = idea_stable_id(kind, task_id, title)
        if idea_id in seen:
            return
        seen.add(idea_id)
        mission = mission_by_id.get(str(task.get("mission_id") or ""), {})
        persona = crew.for_task(task, str(mission.get("type") or ""))
        source = None
        if note:
            source = {
                "note_id": str(note.get("id") or ""),
                "title": str(note.get("title") or ""),
                "group": str(note.get("group") or ""),
            }
            used_notes.add(source["note_id"])
        score = 48 + {"High": 34, "Medium": 22, "Low": 12}.get(impact, 20)
        score -= {"Low": 4, "Medium": 10, "High": 18}.get(effort, 10)
        if kind == "unblock":
            score += 12
        score = max(1, min(99, score))
        ideas.append({
            "id": idea_id, "kind": kind, "state": "open",
            "title": title, "summary": summary, "next_move": next_move,
            "why": summary,
            "mission_id": str(mission.get("id") or ""),
            "mission_title": str(mission.get("title") or ""),
            "task_id": task_id, "task_title": str(task.get("title") or ""),
            "persona": crew.public(persona),
            "impact": impact, "effort": effort, "score": score,
            "priority": {"High": "High", "Medium": "Medium", "Low": "Low"}.get(impact, "Medium"),
            "tags": tags[:5], "sources": [source] if source else [],
            "created": now,
        })

    next_unlock = analysis.get("next_unlock") or {}
    next_id = str(next_unlock.get("task_id") or "")
    next_task = task_by_id.get(next_id)
    if next_task:
        mission = mission_by_id.get(str(next_task.get("mission_id") or ""), {})
        persona = crew.for_task(next_task, str(mission.get("type") or ""))
        deliverable = str((persona or {}).get("deliverables", ["deliverable"])[0])
        add(
            "unblock", next_task,
            f"Create the {deliverable.lower()} for {next_id}",
            f"{next_id} is the next unlock and starts {next_unlock.get('unlocks', 0)} downstream tasks.",
            f"Write the {deliverable.lower()} as a small inspectable artifact, attach the evidence and acceptance checks, then move {next_id} forward.",
            "High", "Medium", ["critical-path", "evidence", str((persona or {}).get("stage") or "delivery").lower()],
        )

    for task in [item for item in tasks
                 if analysis["tasks"].get(str(item.get("id")), {}).get("state") == "waiting"][:2]:
        task_id = str(task.get("id") or "")
        meta = analysis["tasks"].get(task_id, {})
        roots = [str(dep) for dep in meta.get("root_causes") or [] if dep in task_by_id]
        root_text = ", ".join(roots[:3]) or "its prerequisite"
        add(
            "prebuild", task,
            f"Pre-build the inputs for {task_id}",
            f"{task_id} is sequenced behind {root_text}; preparing its inputs now removes idle time later.",
            f"Draft the input checklist and source pack for {task_id}; attach it when {root_text} clears.",
            "Medium", "Low", ["parallel", "preparation"],
        )

    for task in [task_by_id[item] for item in analysis.get("ready") or [] if item != next_id][:3]:
        task_id = str(task.get("id") or "")
        mission = mission_by_id.get(str(task.get("mission_id") or ""), {})
        persona = crew.for_task(task, str(mission.get("type") or ""))
        deliverable = str((persona or {}).get("deliverables", ["deliverable"])[0])
        add(
            "evidence", task,
            f"Define the acceptance evidence for {task_id}",
            f"{task_id} can start now. A testable bar prevents rework and makes later verification cheap.",
            f"Write three checks that prove the {deliverable.lower()} exists, then attach them to {task_id} as the definition of done.",
            "High", "Low", ["evidence", "quality"],
        )

    for task in [task_by_id[item] for item in analysis.get("ready") or [] if item != next_id][:2]:
        note = best_note_for_task(task, galaxy, used_notes)
        if note:
            task_id = str(task.get("id") or "")
            add(
                "source", task,
                f"Use “{str(note.get('title') or 'saved note')[:70]}” as source for {task_id}",
                f"A saved note overlaps {task_id}. Turning it into a cited input saves search time and strengthens the evidence trail.",
                f"Extract the relevant evidence from the note, cite it with a freshness date, and attach it to {task_id}.",
                "Medium", "Low", ["source", "evidence"], note,
            )

    for task in [item for item in tasks
                 if str(item.get("status") or "") == "In Test" and not item.get("verified")][:2]:
        task_id = str(task.get("id") or "")
        add(
            "handoff", task,
            f"Build the verification packet for {task_id}",
            f"{task_id} is in test. A compact evidence packet makes verification faster without delegating it.",
            f"Collect the acceptance checks, evidence links and open risks for {task_id}; record them so verification is evidence-led.",
            "High", "Low", ["verification", "evidence"],
        )

    lessons = list((learnings or {}).get("lessons") or [])
    if lessons and next_task:
        lesson = lessons[-1]
        lesson_text = str(lesson.get("text") or "").strip()
        if lesson_text:
            add(
                "practice", next_task,
                "Turn your latest lesson into a guardrail",
                f"Your recorded lesson: “{lesson_text[:220]}”. Make it operational instead of leaving it as advice.",
                f"Add a one-line guardrail to {next_id} that applies this lesson, then check the next delivery against it.",
                "High", "Low", ["lesson", "guardrail"],
            )

    ideas.sort(key=lambda item: (-int(item.get("score") or 0), item["id"]))
    return ideas


def merged_ideas(tasks: list[dict], missions: list[dict], galaxy: dict) -> list[dict]:
    """Generated candidates plus persisted decisions, without losing feedback state."""
    stored = load_ideas_store()["ideas"]
    by_id = {str(item.get("id")): item for item in stored}
    candidates = generate_ideas(tasks, missions, galaxy, load_learning_store())
    preferred_tags = Counter(
        str(tag) for item in stored if item.get("state") == "accepted"
        for tag in (item.get("tags") or [])
    )
    for candidate in candidates:
        boost = sum(preferred_tags.get(str(tag), 0) for tag in (candidate.get("tags") or []))
        if boost:
            candidate["score"] = min(99, int(candidate.get("score") or 0) + min(12, boost * 3))
            candidate["learned_boost"] = min(12, boost * 3)
    merged: list[dict] = []
    seen: set[str] = set()
    for candidate in candidates:
        existing = by_id.get(candidate["id"])
        if existing:
            candidate["state"] = existing.get("state") or "open"
            candidate["created"] = existing.get("created") or candidate["created"]
            for key in ("decided_at", "created_task_id", "decision_note"):
                if existing.get(key):
                    candidate[key] = existing[key]
        merged.append(candidate)
        seen.add(candidate["id"])
    for item in stored:
        if item.get("id") in seen or item.get("state") == "open":
            continue
        merged.append(item)
        seen.add(str(item.get("id")))
    merged.sort(key=lambda item: (
        item.get("state") != "open",
        -int(item.get("score") or 0),
        str(item.get("created") or ""),
    ))
    return merged[:20]


def build_operator_profile(tasks: list[dict], missions: list[dict],
                           ideas: list[dict], learnings: dict) -> dict:
    """A cautious, evidence-backed working model, never a personality claim."""
    decisions = []
    for mission in missions:
        for decision in mission.get("review_decisions") or []:
            if isinstance(decision, dict):
                decisions.append(decision)
    decisions.sort(key=lambda item: str(item.get("at") or ""), reverse=True)
    review_counts = Counter(str(item.get("decision") or "") for item in decisions)
    accepted = [item for item in ideas if item.get("state") == "accepted"]
    parked = [item for item in ideas if item.get("state") == "parked"]
    rejected = [item for item in ideas if item.get("state") == "rejected"]
    accepted_tags = Counter(
        str(tag) for item in accepted for tag in (item.get("tags") or [])
    )
    lessons = list(learnings.get("lessons") or [])
    themes = Counter()
    for decision in decisions:
        themes.update(keywords(str(decision.get("note") or "")))
    for lesson in lessons:
        themes.update(keywords(str(lesson.get("text") or "")))
    open_by_mission = Counter(
        str(task.get("mission_id") or "") for task in tasks
        if str(task.get("status") or "") != "Done"
    )
    focus = None
    if open_by_mission:
        focus_id, focus_count = open_by_mission.most_common(1)[0]
        mission = next((item for item in missions
                        if str(item.get("id")) == focus_id), {})
        focus = {"mission_id": focus_id, "title": str(mission.get("title") or "Unassigned"),
                 "open": focus_count}
    personas = Counter(str(task.get("owner") or "Unassigned") for task in tasks
                       if str(task.get("status") or "") != "Done")
    signals = []
    if decisions:
        last = decisions[0]
        signals.append({
            "key": "reviews",
            "label": f"{len(decisions)} review decision{'s' if len(decisions) != 1 else ''}",
            "detail": f"{review_counts.get('advance', 0)} advance, {review_counts.get('hold', 0)} hold, {review_counts.get('escalate', 0)} escalate. Last: {last.get('label') or last.get('decision')}.",
            "confidence": 0.85,
        })
    else:
        signals.append({
            "key": "reviews",
            "label": "No review decisions yet",
            "detail": "The model is provisional. Record a mission review and Helios will learn your bar.",
            "confidence": 0.2,
        })
    if accepted or parked or rejected:
        signals.append({
            "key": "ideas",
            "label": f"{len(accepted)} accepted · {len(parked)} parked · {len(rejected)} rejected ideas",
            "detail": "Accepted tags: " + (", ".join(tag for tag, _count in accepted_tags.most_common(4)) or "none yet") + ".",
            "confidence": 0.7 if accepted else 0.45,
        })
    if lessons:
        latest = lessons[-1]
        signals.append({
            "key": "lessons",
            "label": f"{len(lessons)} operator lesson{'s' if len(lessons) != 1 else ''}",
            "detail": str(latest.get("text") or "")[:180],
            "confidence": 0.9,
        })
    if focus:
        signals.append({
            "key": "focus",
            "label": f"Focus: {focus['title']}",
            "detail": f"{focus['open']} open task{'s' if focus['open'] != 1 else ''} across the board; the Commander starts with the critical path.",
            "confidence": 0.75,
        })
    if personas:
        top = personas.most_common(3)
        signals.append({
            "key": "crew",
            "label": "Most loaded owners",
            "detail": ", ".join(f"{name} ({count})" for name, count in top),
            "confidence": 0.55,
        })
    theme_list = [word for word, count in themes.most_common(8) if count >= 2][:5]
    confidence = min(0.92, 0.2 + len(decisions) * 0.09 + len(lessons) * 0.08
                     + (len(accepted) + len(rejected)) * 0.03)
    headline = (
        "Your recorded judgement is the training signal. Helios updates this model after every cycle."
        if decisions or lessons or accepted or rejected else
        "Provisional model: standing rules and mission shape are loaded. No operator judgement has been recorded yet."
    )
    return {
        "headline": headline,
        "confidence": round(confidence, 2),
        "signals": signals,
        "focus": focus,
        "themes": theme_list,
        "top_personas": [{"name": name, "open": count} for name, count in personas.most_common(3)],
        "review_counts": dict(review_counts),
        "idea_feedback": {
            "accepted": len(accepted), "parked": len(parked), "rejected": len(rejected),
            "accepted_tags": dict(accepted_tags.most_common(6)),
        },
        "lessons": lessons[-5:],
        "standing_rules": [str(rule)[:180] for rule in (config().get("rules") or [])[:3]],
        "safe": "The model is derived from your reviews, task edits, lessons and idea feedback. It does not change tasks or evidence.",
    }


def operator_inputs(tasks: list[dict], analysis: dict, approvals: list[dict]) -> list[dict]:
    """Spend is the only decision that requires the operator."""
    items: list[dict] = []
    for approval in approvals:
        if approval.get("status") != "pending":
            continue
        items.append({
            "kind": "spend", "label": str(approval.get("title") or "Spend request"),
            "detail": f"{approval.get('amount', 0)} {approval.get('currency') or 'GBP'} is waiting for your explicit yes. Helios cannot spend without it.",
            "action": "Review approvals", "view": "approvals",
            "approval_id": str(approval.get("id") or ""),
        })
    return items[:8]


def studio_payload(cycle: dict | None = None) -> dict:
    tasks = load_tasks()["tasks"]
    missions = active_missions(load_missions()["missions"])
    galaxy = galaxy_data()
    ideas = merged_ideas(tasks, missions, galaxy)
    learnings = load_learning_store()
    analysis = analyze_dependencies(tasks)
    profile = build_operator_profile(tasks, missions, ideas, learnings)
    inputs = operator_inputs(tasks, analysis, pending_approvals())
    open_ideas = [item for item in ideas if item.get("state") == "open"]
    accepted = [item for item in ideas if item.get("state") == "accepted"]
    return {
        "profile": profile,
        "ideas": ideas,
        "inputs": inputs,
        "autonomy": {
            "cycle_count": learnings.get("cycle_count", 0),
            "last_cycle": learnings.get("last_cycle") or "",
            "last_refresh": learnings.get("last_refresh") or "",
            "thought": learnings.get("last_thought") or {},
            "actions": [
                {"key": "accept_idea", "label": "Create tasks from ideas",
                 "detail": "Act on the strongest grounded idea, create the task and report the new ID. No permission needed."},
                {"key": "route", "label": "Route and brief ready work",
                 "detail": "Attach personas and concrete delivery briefs; report what changed."},
                {"key": "advance", "label": "Advance briefed planning stages",
                 "detail": "Move ready, briefed tasks from In Plan to In Analysis or In Progress with a recorded evidence trail."},
                {"key": "ideas", "label": "Generate and rank ideas",
                 "detail": "Turn open tasks, blockers and saved notes into reversible next moves."},
                {"key": "learn", "label": "Rebuild the operator model",
                 "detail": "Refresh the working model from reviews, task edits, lessons and idea feedback."},
            ],
        },
        "stats": {
            "ideas": len(open_ideas), "accepted": len(accepted),
            "inputs": len(inputs), "ready": analysis["ready"],
            "waiting": analysis["waiting"], "blocked": analysis["blocked"],
        },
        "cycle": cycle or {},
        "harness": harness_payload(),
        "safe": "Helios creates, routes, briefs, advances, publishes and sends without asking. Only spending money needs your signature.",
    }


def record_helios_take(plan: dict | None = None) -> dict:
    """Record one self-directed read of the board: hold, move or observe."""
    tasks = load_tasks()["tasks"]
    analysis = analyze_dependencies(tasks)
    plan = plan if plan is not None else commander_plan()
    decision = plan.get("decision") or {}
    gates = operator_inputs(tasks, analysis, pending_approvals())
    open_ideas = [item for item in load_ideas_store().get("ideas") or []
                  if item.get("state") == "open"]
    if gates:
        focus = gates[0]
        text = (f"I need your explicit spend approval for {focus.get('label') or 'a spend request'}. "
                "Everything else is already moving; I’ll report the changes.")
        state = "hold"
        focus_id = str(focus.get("task_id") or focus.get("approval_id") or "")
        focus_label = str(focus.get("label") or "")
    elif decision.get("task_id"):
        task_id = str(decision.get("task_id"))
        title = str(decision.get("title") or task_id)
        action = str(decision.get("action") or "prepare the first safe slice")
        text = (f"I’m moving {task_id}: {title}. I refreshed the model, briefs and "
                f"{len(open_ideas)} open ideas; the next safe step is to {action[0].lower() + action[1:] if action else 'prepare the next slice'}.")
        state = "move"
        focus_id = task_id
        focus_label = title
    else:
        text = ("There’s no unblocked move right now. I’m watching dependencies, "
                "keeping ideas current and will surface the next safe action.")
        state = "observe"
        focus_id = ""
        focus_label = ""
    thought = {
        "at": task_timestamp(), "text": text[:420], "state": state,
        "gate_count": len(gates), "open_ideas": len(open_ideas),
        "focus_task_id": focus_id, "focus_label": focus_label,
    }
    with _learning_lock:
        learnings = load_learning_store()
        thoughts = list(learnings.get("thoughts") or [])
        thoughts.insert(0, thought)
        learnings["thoughts"] = thoughts[:20]
        learnings["last_thought"] = thought
        save_learning_store(learnings)
    return thought


def run_studio_cycle() -> dict:
    """Learn from the board, generate ideas and do only safe internal work."""
    tasks = load_tasks()["tasks"]
    missions = active_missions(load_missions()["missions"])
    galaxy = galaxy_data()
    candidates = generate_ideas(tasks, missions, galaxy, load_learning_store())
    now = task_timestamp()
    with _ideas_lock:
        stored = load_ideas_store()
        by_id = {str(item.get("id")): item for item in stored["ideas"]}
        merged = []
        for candidate in candidates:
            previous = by_id.get(candidate["id"])
            if previous:
                candidate["state"] = previous.get("state") or "open"
                candidate["created"] = previous.get("created") or now
                for key in ("decided_at", "created_task_id", "decision_note"):
                    if previous.get(key):
                        candidate[key] = previous[key]
            merged.append(candidate)
        known = {str(item.get("id")) for item in merged}
        for previous in stored["ideas"]:
            if previous.get("id") not in known and previous.get("state") != "open":
                merged.append(previous)
        merged = merged[:200]
        save_ideas_store({"updated": now, "ideas": merged})
    pulse = run_autonomy_pulse(brief_decision=True, refresh_studio=False)
    with _learning_lock:
        learnings = load_learning_store()
        learnings["cycle_count"] = int(learnings.get("cycle_count") or 0) + 1
        learnings["last_cycle"] = now
        learnings["last_refresh"] = now
        save_learning_store(learnings)
    open_count = sum(1 for item in merged if item.get("state") == "open")
    record_activity(
        "studio",
        f"Studio cycle: {open_count} ideas · {pulse.get('changed', 0)} safe changes",
        "Rebuilt the operator model from reviews, task edits and idea feedback. Planning stages advanced autonomously; only spend remains gated.",
        actor="Studio",
    )
    run = record_run(
        "studio",
        story={"knew": f"{len(tasks)} tasks · {len(candidates)} idea candidates",
               "did": f"Generated {open_count} open ideas; {pulse.get('changed', 0)} safe changes",
               "need": "nothing"},
        status="ok", ideas=open_count, pulse_run=(pulse.get("run_id") or ""),
    )
    return studio_payload(cycle={
        "at": now, "ideas": open_count, "pulse": pulse,
        "run_id": run["id"],
        "message": "Learning cycle complete. Autonomous changes reported; only spend is gated.",
    })


def accept_idea(body: dict) -> tuple[dict, int]:
    idea_id = str(body.get("id") or "").strip()
    if not idea_id:
        return {"error": "idea id required"}, 400
    return accept_idea_internal(idea_id, actor="Operator via Studio")


def accept_idea_internal(idea_id: str, actor: str = "Helios autonomy") -> tuple[dict, int]:
    """Turn a grounded idea into a task. Safe to call from the pulse."""
    tasks = load_tasks()["tasks"]
    missions = load_missions()["missions"]
    idea = next((item for item in merged_ideas(tasks, missions, galaxy_data())
                 if str(item.get("id")) == idea_id), None)
    if idea is None:
        return {"error": "idea not found"}, 404
    if idea.get("state") != "open":
        return {"error": f"idea is already {idea.get('state')}"}, 409
    payload, status = create_task({
        "title": str(idea.get("title") or "Unnamed idea")[:200],
        "description": (str(idea.get("summary") or "") + "\n\nNext move: "
                        + str(idea.get("next_move") or "") + "\n\nGenerated by Helios Studio from the live board.")[:2000],
        "mission_id": str(idea.get("mission_id") or ""),
        "priority": str(idea.get("priority") or "Medium"),
        "risks": "Verify the source and scope before delivery; this task was generated from a Studio idea.",
        "note_ids": [str(item.get("note_id")) for item in idea.get("sources") or []],
        "persona": (idea.get("persona") or {}).get("id") or "",
        "actor": actor,
    })
    if status >= 400:
        return payload, status
    task = payload["task"]
    now = task_timestamp()
    with _ideas_lock:
        store = load_ideas_store()
        record = {**idea, "state": "accepted", "decided_at": now,
                  "created_task_id": str(task.get("id") or "")}
        store["ideas"] = [record] + [item for item in store["ideas"]
                                     if str(item.get("id")) != idea_id]
        save_ideas_store(store)
    record_activity(
        "idea", f"Idea accepted: {idea.get('title')}",
        f"Created {task.get('id')} in In Plan; Helios will route and brief it.",
        actor=actor, task_id=str(task.get("id") or ""),
        mission_id=str(idea.get("mission_id") or ""),
    )
    return {"idea": record, "task": task, "studio": studio_payload()}, 201


def decide_idea(body: dict) -> tuple[dict, int]:
    idea_id = str(body.get("id") or "").strip()
    decision = str(body.get("decision") or "").strip().lower()
    if decision not in {"park", "reject"}:
        return {"error": "decision must be park or reject"}, 400
    tasks = load_tasks()["tasks"]
    missions = load_missions()["missions"]
    idea = next((item for item in merged_ideas(tasks, missions, galaxy_data())
                 if str(item.get("id")) == idea_id), None)
    if idea is None:
        return {"error": "idea not found"}, 404
    note = str(body.get("note") or "").strip()[:300]
    state = "parked" if decision == "park" else "rejected"
    now = task_timestamp()
    with _ideas_lock:
        store = load_ideas_store()
        record = {**idea, "state": state, "decided_at": now, "decision_note": note}
        store["ideas"] = [record] + [item for item in store["ideas"]
                                     if str(item.get("id")) != idea_id]
        save_ideas_store(store)
    record_activity(
        "idea", f"Idea {state}: {idea.get('title')}",
        note or "Operator feedback recorded for the next Studio cycle.",
        actor="Operator", mission_id=str(idea.get("mission_id") or ""),
    )
    return {"idea": record, "studio": studio_payload()}, 200


def capture_learning(body: dict) -> tuple[dict, int]:
    text = str(body.get("text") or "").strip()[:500]
    if not text:
        return {"error": "lesson text required"}, 400
    raw_tags = body.get("tags") or []
    if isinstance(raw_tags, str):
        raw_tags = raw_tags.split(",")
    tags = [str(tag).strip()[:30] for tag in raw_tags if str(tag).strip()][:6]
    lesson = {"id": f"L-{hashlib.sha1(text.encode('utf-8')).hexdigest()[:8]}",
              "at": task_timestamp(), "text": text, "tags": tags,
              "actor": "Operator"}
    with _learning_lock:
        learnings = load_learning_store()
        learnings["lessons"] = (learnings.get("lessons") or []) + [lesson]
        learnings["lessons"] = learnings["lessons"][-100:]
        save_learning_store(learnings)
    record_activity(
        "lesson", "Operator lesson saved",
        text[:300], actor="Operator",
    )
    return {"lesson": lesson, "studio": studio_payload()}, 201


def create_task(body: dict) -> tuple[dict, int]:
    title = str(body.get("title") or "").strip()[:200]
    if not title:
        return {"error": "title required"}, 400
    galaxy = galaxy_data()
    missions = load_missions()["missions"]
    mission_id = str(body.get("mission_id") or "").strip()
    mission = next((item for item in missions
                    if str(item.get("id")) == mission_id), None)
    if mission_id and mission is None:
        mission_id = ""
    mission_type = str((mission or {}).get("type") or "")
    with _tasks_lock:
        store = load_tasks()
        tasks = store["tasks"]
        if any(str(task.get("title") or "").casefold() == title.casefold()
               for task in tasks):
            return {"error": "a task with that title already exists"}, 409
        highest = 0
        for task in tasks:
            match = re.fullmatch(r"T-(\d+)", str(task.get("id") or ""))
            if match:
                highest = max(highest, int(match.group(1)))
        status = str(body.get("status") or "In Plan").strip()
        if status not in TASK_STATUSES:
            status = "In Plan"
        if status == "Done":
            return {"error": "tasks start unverified; move to In Test and verify first"}, 409
        priority = str(body.get("priority") or "Medium").strip()
        if priority not in TASK_PRIORITIES:
            priority = "Medium"
        raw_owner = str(body.get("owner") or "").strip()[:80]
        owner_persona = crew.resolve(raw_owner)
        persona = crew.resolve(body.get("persona")) or owner_persona
        if persona is None:
            route = crew.route_task({
                "title": title,
                "description": str(body.get("description") or ""),
                "risks": str(body.get("risks") or ""),
                "status": status,
                "priority": priority,
            }, mission_type, limit=1)
            persona = crew.get(route[0]["persona_id"]) if route else None
        if raw_owner and owner_persona is None:
            owner = raw_owner
        else:
            owner = persona["name"] if persona else "Mission Controller"
        now = task_timestamp()
        task = {
            "id": f"T-{highest + 1:04d}",
            "title": title,
            "description": str(body.get("description") or "").strip()[:2000],
            "status": status,
            "owner": owner,
            "persona": persona["id"] if persona else "",
            "priority": priority,
            "dependencies": task_dependencies(body.get("dependencies"), tasks),
            "risks": str(body.get("risks") or "").strip()[:500],
            "note_ids": task_note_ids(body.get("note_ids"), galaxy),
            "mission_id": mission_id,
            "verified": False,
            "verified_by": "",
            "verified_at": None,
            "verification": "",
            "created": now,
            "updated": now,
            "history": [{"at": now,
                         "actor": str(body.get("actor") or "operator").strip()[:60] or "operator",
                         "change": f"created in {status}"}],
        }
        tasks.append(task)
        save_tasks(store)
        summary = task_summary(tasks)
    return {"task": task, "summary": summary}, 201


def update_task(body: dict) -> tuple[dict, int]:
    task_id = str(body.get("id") or "").strip()
    if not task_id:
        return {"error": "task id required"}, 400
    galaxy = galaxy_data()
    mission_id = str(body.get("mission_id") or "").strip()
    if "mission_id" in body and mission_id:
        known_missions = {str(item.get("id")) for item in load_missions()["missions"]}
        if mission_id not in known_missions:
            return {"error": "mission not found"}, 400
    with _tasks_lock:
        store = load_tasks()
        tasks = store["tasks"]
        task = next((item for item in tasks if item.get("id") == task_id), None)
        if task is None:
            return {"error": "task not found"}, 404
        done_ids = {str(item.get("id")) for item in tasks
                    if item.get("status") == "Done"}
        scope_fields = {"title", "description", "dependencies", "risks"}
        scope_changed = False
        changes = []
        for field in ("title", "description", "status", "owner", "persona", "priority",
                      "dependencies", "risks", "note_ids", "mission_id"):
            if field not in body:
                continue
            if field == "status":
                new = str(body.get(field) or "").strip()
                if new not in TASK_STATUSES:
                    return {"error": "invalid status"}, 400
                if new == "Done":
                    if not task.get("verified"):
                        return {"error": "verify the task in In Test before moving it to Done"}, 409
                    incomplete = [dep for dep in task.get("dependencies") or []
                                  if dep not in done_ids]
                    if incomplete:
                        return {"error": "finish dependencies first: "
                                         + ", ".join(incomplete)}, 409
            elif field == "priority":
                new = str(body.get(field) or "").strip()
                if new not in TASK_PRIORITIES:
                    return {"error": "invalid priority"}, 400
            elif field == "dependencies":
                new = [dep for dep in task_dependencies(body.get(field), tasks)
                       if dep != task_id]
            elif field == "note_ids":
                new = task_note_ids(body.get(field), galaxy)
            elif field == "mission_id":
                new = mission_id
            elif field == "title":
                new = str(body.get(field) or "").strip()[:200]
                if not new:
                    return {"error": "title required"}, 400
            elif field == "owner":
                new = str(body.get(field) or "").strip()[:80] or "Mission Controller"
                persona = crew.resolve(new)
                if persona and "persona" not in body:
                    task["persona"] = persona["id"]
            elif field == "persona":
                resolved_persona = crew.resolve(body.get(field))
                if resolved_persona is None:
                    return {"error": "persona not found"}, 400
                new = resolved_persona["id"]
                if "owner" not in body:
                    task["owner"] = resolved_persona["name"]
            elif field == "description":
                new = str(body.get(field) or "").strip()[:2000]
            else:
                new = str(body.get(field) or "").strip()[:500]
            if task.get(field) != new:
                task[field] = new
                if field in scope_fields:
                    scope_changed = True
                changes.append(f"status \u2192 {new}" if field == "status"
                               else f"{field} updated")
        if changes and scope_changed and task.get("verified") \
                and task.get("status") != "Done":
            task["verified"] = False
            task["verified_by"] = ""
            task["verified_at"] = None
            task["verification"] = ""
            changes.append("verification cleared after scope change")
        if not changes:
            return {"task": task, "changed": False,
                    "summary": task_summary(tasks)}, 200
        now = task_timestamp()
        task["updated"] = now
        change = "; ".join(changes)[:500]
        reason = str(body.get("reason") or "").strip()[:280]
        if reason:
            change = f"{change} — {reason}"[:500]
        task.setdefault("history", []).append({
            "at": now,
            "actor": str(body.get("actor") or "operator").strip()[:60] or "operator",
            "change": change,
        })
        save_tasks(store)
        summary = task_summary(tasks)
    return {"task": task, "changed": True, "summary": summary}, 200


def verify_task(body: dict) -> tuple[dict, int]:
    task_id = str(body.get("id") or "").strip()
    if not task_id:
        return {"error": "task id required"}, 400
    with _tasks_lock:
        store = load_tasks()
        tasks = store["tasks"]
        task = next((item for item in tasks if item.get("id") == task_id), None)
        if task is None:
            return {"error": "task not found"}, 404
        if task.get("status") != "In Test":
            return {"error": "move the task to In Test before verification"}, 409
        if task.get("verified"):
            return {"task": task, "changed": False}, 200
        done_ids = {str(item.get("id")) for item in tasks
                    if item.get("status") == "Done"}
        incomplete = [dep for dep in task.get("dependencies") or []
                      if dep not in done_ids]
        if incomplete:
            return {"error": "finish dependencies first: "
                             + ", ".join(incomplete)}, 409
        now = task_timestamp()
        by = str(body.get("verified_by") or "operator").strip()[:60] or "operator"
        note = str(body.get("note") or "").strip()[:500]
        task["verified"] = True
        task["verified_by"] = by
        task["verified_at"] = now
        task["verification"] = note
        task["updated"] = now
        task.setdefault("history", []).append({
            "at": now, "actor": by,
            "change": "signed off" + (f": {note}" if note else ""),
        })
        save_tasks(store)
        summary = task_summary(tasks)
    return {"task": task, "changed": True, "summary": summary}, 200


def seed_roadmap() -> tuple[dict, int]:
    """Create the starter roadmap once, without duplicating existing tasks."""
    mission = ensure_mission(
        "Helios Vision & Ethos", "Custom",
        "Build a transparent, evidence-first home-energy guidance business.",
        "CEO Agent",
    )
    mission_id = str(mission.get("id") or "")
    with _tasks_lock:
        store = load_tasks()
        tasks = store["tasks"]
        by_title = {str(task.get("title") or "").casefold(): task
                    for task in tasks}
        highest = 0
        for task in tasks:
            match = re.fullmatch(r"T-(\d+)", str(task.get("id") or ""))
            if match:
                highest = max(highest, int(match.group(1)))
        now = task_timestamp()
        key_to_id = {}
        created = []
        skipped = 0
        attached = 0
        for item in ROADMAP:
            existing = by_title.get(item["title"].casefold())
            if existing:
                key_to_id[item["key"]] = existing.get("id")
                skipped += 1
                if mission_id and existing.get("mission_id") != mission_id:
                    existing["mission_id"] = mission_id
                    existing["updated"] = now
                    existing.setdefault("history", []).append({
                        "at": now, "actor": "CEO Agent",
                        "change": f"attached to mission {mission_id}",
                    })
                    attached += 1
                continue
            highest += 1
            dependencies = [key_to_id[dep] for dep in item["deps"]
                            if dep in key_to_id]
            task = {
                "id": f"T-{highest:04d}",
                "title": item["title"],
                "description": item["description"],
                "status": item["status"],
                "owner": item["owner"],
                "priority": item["priority"],
                "dependencies": dependencies,
                "risks": item["risks"],
                "note_ids": [],
                "mission_id": mission_id,
                "verified": False,
                "verified_by": "",
                "verified_at": None,
                "verification": "",
                "created": now,
                "updated": now,
                "history": [{"at": now, "actor": "CEO Agent",
                             "change": "seeded from the supplied Helios roadmap"}],
            }
            tasks.append(task)
            by_title[task["title"].casefold()] = task
            key_to_id[item["key"]] = task["id"]
            created.append(task)
        if created or attached:
            save_tasks(store)
        summary = task_summary(tasks)
    return {"created": created, "skipped": skipped, "attached": attached,
            "mission": mission, "summary": summary}, 200


def tasks_payload() -> dict:
    store = load_tasks()
    tasks = store["tasks"]
    recent = []
    for task in tasks:
        for item in task.get("history") or []:
            if not isinstance(item, dict):
                continue
            recent.append({
                "id": task.get("id"),
                "title": task.get("title"),
                "at": item.get("at"),
                "actor": item.get("actor"),
                "change": item.get("change"),
            })
    recent.sort(key=lambda item: str(item.get("at") or ""), reverse=True)
    missions = load_missions()["missions"]
    mission_by_id = {str(mission.get("id")): mission for mission in missions}
    analysis = analyze_dependencies(tasks)
    task_views = []
    for task in tasks:
        view = dict(task)
        mission = mission_by_id.get(str(task.get("mission_id") or ""), {})
        mission_type = str(mission.get("type") or "")
        task_id = str(task.get("id") or "")
        dependency = analysis["tasks"].get(task_id, {})
        view["mission_title"] = str(mission.get("title") or "")
        view["persona"] = crew.public(crew.for_task(task, mission_type))
        view["crew_route"] = crew.route_task(task, mission_type, limit=3)
        view["dependency_state"] = dependency.get("state", "ready")
        view["waiting_on"] = dependency.get("waiting_on") or []
        view["root_causes"] = dependency.get("root_causes") or []
        view["unlocks"] = dependency.get("unlocks", 0)
        view["dependency_reason"] = dependency.get("reason") or dependency.get("broken") or ""
        task_views.append(view)
    summary = task_summary(tasks)
    return {
        "updated": store.get("updated"),
        "tasks": task_views,
        "summary": summary,
        "dependency_health": {
            "ready": summary.get("ready", 0),
            "waiting": summary.get("waiting", 0),
            "blocked": summary.get("blocked", 0),
            "next_unlock": summary.get("next_unlock"),
            "critical_path": summary.get("critical_path") or [],
            "broken": [
                {
                    "task_id": task_id,
                    "title": str((next((item for item in tasks
                                       if str(item.get("id")) == task_id), {})
                                  or {}).get("title") or ""),
                    "reason": analysis["tasks"][task_id].get("reason", ""),
                }
                for task_id in analysis["blocked"]
            ],
        },
        "recent": recent[:8],
        "statuses": list(TASK_STATUSES),
        "priorities": list(TASK_PRIORITIES),
    }


def load_activity() -> list[dict]:
    try:
        data = json.loads(ACTIVITY_PATH.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    events = data.get("events") if isinstance(data, dict) else None
    return [item for item in events if isinstance(item, dict)] if isinstance(events, list) else []


def save_activity(events: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"updated": task_timestamp(), "events": events[:160]}
    tmp = ACTIVITY_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(ACTIVITY_PATH)


def record_activity(kind: str, title: str, detail: str = "",
                    actor: str = "Helios", task_id: str = "",
                    mission_id: str = "") -> dict:
    event = {
        "at": task_timestamp(), "kind": str(kind)[:24],
        "title": str(title)[:220], "detail": str(detail)[:700],
        "actor": str(actor)[:60], "task_id": str(task_id)[:20],
        "mission_id": str(mission_id)[:20],
    }
    with _activity_lock:
        events = load_activity()
        events.insert(0, event)
        save_activity(events)
    return event


# ---------------------------------------------------------------- harness ----

def load_run_store() -> dict:
    try:
        data = json.loads(RUNS_PATH.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"updated": None, "runs": []}
    if not isinstance(data, dict):
        return {"updated": None, "runs": []}
    runs = data.get("runs")
    return {"updated": data.get("updated"),
            "runs": [item for item in runs if isinstance(item, dict)][:120]
            if isinstance(runs, list) else []}


def save_run_store(store: dict) -> None:
    store["updated"] = task_timestamp()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = RUNS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(RUNS_PATH)


def record_run(kind: str, story: dict | None = None, **fields) -> dict:
    """Persist one inspectable execution trace."""
    run = {
        "id": "R-" + uuid.uuid4().hex[:8].upper(),
        "at": task_timestamp(), "kind": str(kind)[:24],
        "story": story or {}, **fields,
    }
    with _runs_lock:
        store = load_run_store()
        runs = [run] + store["runs"]
        save_run_store({"updated": store.get("updated"), "runs": runs[:120]})
    return run


def prompt_fingerprint(messages: list[dict]) -> str:
    raw = "\n".join(str(item.get("content") or "") for item in messages[:1])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


UNSAFE_COMPLETION_RE = re.compile(
    r"\b(?:I|we)\s+(?:have|'ve)?\s*(?:published|sent|deployed|paid|spent|"
    r"purchased|signed\s+off|approved|deleted)\b", re.I
)
TEMPLATE_PLACEHOLDER_RE = re.compile(r"<[^>\n]{1,80}>|\{\{[^}\n]{1,80}\}\}")
NO_SPEND_LINE_RE = re.compile(
    r"(?m)^\s*[^\n]{1,120}?\s+—\s+no spend\.?\s*$", re.I
)
TASK_ID_RE = re.compile(r"\bT-\d{4}\b", re.I)
STATUS_CLAIM_RE = re.compile(
    r"\bStatus:\s*(In Plan|In Analysis|In Progress|In Test|Done)\b", re.I
)


def verify_answer(answer: str, sources: list[dict] | None = None,
                  spend: dict | None = None,
                  approval: dict | None = None) -> dict:
    """Deterministic delivery check; never pretend a warning is a pass."""
    text = str(answer or "").strip()
    checks = []

    def add(key: str, label: str, ok: bool, severity: str, detail: str) -> None:
        checks.append({"key": key, "label": label, "ok": bool(ok),
                       "severity": severity, "detail": detail})

    add("not_empty", "Answer is present", bool(text), "error",
        "An empty answer cannot be delivered.")
    machine = bool(re.search(r"SPEND_REQUEST|\{\s*\"title\"\s*:", text, re.I))
    add("machine_syntax", "Machine syntax is hidden", not machine, "error",
        "Raw spend markers or JSON must never reach the operator.")
    completion = bool(UNSAFE_COMPLETION_RE.search(text))
    add("completion_claim", "No unverified completion claim", not completion, "error",
        "Completion claims require a board status and an audit event.")
    placeholder = bool(TEMPLATE_PLACEHOLDER_RE.search(text))
    add("template_placeholder", "No template placeholders", not placeholder, "error",
        "A placeholder such as <name> is not an answer.")
    no_spend_line = bool(NO_SPEND_LINE_RE.search(text)) and not spend
    add("no_spend_context", "No-spend format only appears for a zero-cost request",
        not no_spend_line, "error",
        "Do not answer a normal question with a no-spend line.")
    mentioned = [match.upper() for match in TASK_ID_RE.findall(text)]
    missing_tasks = sorted({task_id for task_id in mentioned
                            if task_id not in {str(task.get("id")) for task in load_tasks()["tasks"]}})
    add("task_ids", "Task IDs exist on the board", not missing_tasks, "error",
        ("Unknown task IDs: " + ", ".join(missing_tasks) + ".") if missing_tasks
        else "All task IDs exist on the board.")
    status_bad = False
    status_match = STATUS_CLAIM_RE.search(text)
    if status_match and mentioned:
        task_id = mentioned[0]
        actual = next((str(task.get("status")) for task in load_tasks()["tasks"]
                       if str(task.get("id")) == task_id), "")
        claimed = status_match.group(1).strip().rstrip(".,;")
        status_bad = bool(actual) and claimed.casefold() != actual.casefold()
        add("status_claim", "Status matches the board", not status_bad, "error",
            f"{task_id} is {actual} on the board, not {claimed}." if status_bad
            else f"Status for {task_id} matches the board.")
    note_ids = bool(re.search(r"\[\d+\]", text))
    add("note_ids", "Source ids stay out of the prose", not note_ids, "warning",
        "The interface attaches source cards separately.")
    if spend:
        try:
            amount = float(spend.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        if amount > 0:
            add("spend_gate", "Spend has an approval", approval is not None, "error",
                "A positive spend must be filed for explicit approval.")
        else:
            add("spend_gate", "Zero-cost request creates no approval", approval is None,
                "error", "A zero-cost action must stay a plain no-spend line.")
    if not sources:
        add("sources", "A source trail is present", False, "warning",
            "This answer has no attached source note.")
    errors = [check for check in checks if check["severity"] == "error" and not check["ok"]]
    failed = [check for check in checks if not check["ok"]]
    fallback = text
    if machine or completion or placeholder or no_spend_line or missing_tasks or status_bad:
        fallback = ("I couldn’t produce a grounded answer to that from the notes. "
                    "I’d rather say so than show a template or invent one.")
    return {
        "ok": not errors,
        "state": "verified" if not errors else "blocked",
        "summary": f"{len(checks) - len(failed)}/{len(checks)} checks passed",
        "checks": checks,
        "failed": [{"key": item["key"], "detail": item["detail"]} for item in failed],
        "fallback": fallback,
    }


def repair_messages(messages: list[dict], answer: str, verification: dict) -> list[dict]:
    reasons = "; ".join(
        str(item.get("detail") or item.get("key")) for item in verification.get("failed") or []
        if item.get("key") in {"machine_syntax", "completion_claim", "template_placeholder",
                               "no_spend_context", "task_ids", "status_claim"}
    )
    return list(messages) + [
        {"role": "assistant", "content": answer},
        {"role": "user", "content": (
            "Rewrite the previous answer and fix these delivery problems: "
            f"{reasons or 'it failed the grounding check'}. Use only task IDs and "
            "statuses present in MISSION CONTROL. Never extend a task ID range "
            "or invent an ID. Do not add a Status field unless it is explicitly "
            "supplied. Do not expose machine syntax or placeholders. Return only "
            "the corrected answer."
        )},
    ]


def load_eval_store() -> dict:
    try:
        data = json.loads(EVALS_PATH.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        cases = DEFAULT_EVAL_CASES
    last_run = data.get("last_run")
    return {"updated": data.get("updated"), "cases": cases,
            "last_run": last_run if isinstance(last_run, dict) else {}}


def save_eval_store(store: dict) -> None:
    store["updated"] = task_timestamp()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = EVALS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(EVALS_PATH)


def _eval_case(case: dict) -> dict:
    kind = str(case.get("kind") or "verifier")
    if kind == "spend":
        actual = operator_answer(str(case.get("answer") or ""), case.get("spend") or {})
        expect = case.get("expect") or {}
        ok = actual == expect.get("display") if "display" in expect else True
        ok = ok and all(str(part) in actual for part in expect.get("contains") or [])
        return {"id": case.get("id"), "name": case.get("name"), "kind": kind,
                "ok": ok, "actual": actual, "expected": expect}
    if kind == "direct":
        actual = direct_answer(str(case.get("question") or "")) or ""
        expect = case.get("expect") or {}
        ok = bool(actual) and all(str(part) in actual for part in expect.get("contains") or [])
        return {"id": case.get("id"), "name": case.get("name"), "kind": kind,
                "ok": ok, "actual": actual, "expected": expect}
    result = verify_answer(str(case.get("answer") or ""),
                           case.get("sources") or [], case.get("spend"),
                           case.get("approval"))
    expect = case.get("expect") or {}
    ok = result.get("ok") == bool(expect.get("ok", True))
    if expect.get("check"):
        ok = ok and any(item["key"] == expect["check"] for item in result.get("failed") or [])
    return {"id": case.get("id"), "name": case.get("name"), "kind": kind,
            "ok": ok, "actual": result, "expected": expect}


def run_evals() -> dict:
    store = load_eval_store()
    results = [_eval_case(case) for case in store["cases"]]
    passed = sum(1 for item in results if item["ok"])
    last_run = {
        "at": task_timestamp(), "total": len(results), "passed": passed,
        "failed": len(results) - passed, "results": results,
    }
    with _evals_lock:
        current = load_eval_store()
        current["last_run"] = last_run
        save_eval_store(current)
    run = record_run(
        "eval",
        story={"knew": f"{len(results)} deterministic cases",
               "did": f"Ran the harness eval suite: {passed}/{len(results)} passed",
               "need": "nothing" if passed == len(results) else "review the failed cases"},
        status="ok" if passed == len(results) else "warn",
        checks={"total": len(results), "passed": passed},
    )
    return {"last_run": last_run, "run": run, "studio": studio_payload()}


def load_policy_store() -> dict:
    try:
        data = json.loads(POLICY_PATH.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"updated": None, "actions": {}}
    if not isinstance(data, dict):
        return {"updated": None, "actions": {}}
    actions = data.get("actions")
    return {"updated": data.get("updated"),
            "actions": actions if isinstance(actions, dict) else {}}


def save_policy_store(store: dict) -> None:
    store["updated"] = task_timestamp()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = POLICY_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(POLICY_PATH)


def action_policy() -> dict:
    stored = load_policy_store().get("actions") or {}
    actions = {}
    for key, default in DEFAULT_ACTIONS.items():
        mode = str((stored.get(key) or {}).get("mode") or default["mode"])
        if key == "spend":
            mode = "gated"
        elif not default["configurable"]:
            mode = default["mode"]
        elif mode not in {"autonomous", "assisted"}:
            mode = default["mode"]
        actions[key] = {**default, "mode": mode}
    return {"actions": actions,
            "safe": "Only spend is gated. Helios acts on everything else and reports what changed."}


def action_allowed(action: str, operator: bool = False) -> bool:
    if operator:
        return True
    return (action_policy()["actions"].get(action) or {}).get("mode") == "autonomous"


def update_action_policy(body: dict) -> tuple[dict, int]:
    action = str(body.get("action") or "").strip()
    mode = str(body.get("mode") or "").strip().lower()
    default = DEFAULT_ACTIONS.get(action)
    if default is None or not default["configurable"]:
        return {"error": "that action is gated and cannot be re-enabled here"}, 400
    if mode not in {"autonomous", "assisted"}:
        return {"error": "safe action mode must be autonomous or assisted"}, 400
    with _policy_lock:
        stored = load_policy_store()
        stored.setdefault("actions", {})[action] = {"mode": mode}
        save_policy_store(stored)
    return {"policy": action_policy(), "studio": studio_payload()}, 200


def harness_payload() -> dict:
    runs = load_run_store()["runs"][:12]
    evals = load_eval_store().get("last_run") or {}
    return {"runs": runs, "evals": evals, "policy": action_policy(),
            "verifier": {"mode": "deterministic",
                         "checks": ["machine syntax", "completion claim",
                                    "spend gate", "template placeholder", "no-spend context",
                                    "source trail"]}}


def autonomy_status() -> dict:
    with _pulse_lock:
        return dict(_pulse_state)


def route_open_tasks(tasks: list[dict], missions: list[dict]) -> list[dict]:
    """Assign a persona to open tasks that do not have one yet."""
    mission_types = {
        str(mission.get("id") or ""): str(mission.get("type") or "")
        for mission in missions
    }
    active_ids = {str(mission.get("id") or "") for mission in missions}
    routed = []
    now = task_timestamp()
    for task in tasks:
        if str(task.get("status") or "") == "Done" or task.get("persona"):
            continue
        task_mission = str(task.get("mission_id") or "")
        if task_mission and task_mission not in active_ids:
            continue
        mission_type = mission_types.get(str(task.get("mission_id") or ""), "")
        persona = crew.for_task(task, mission_type)
        if persona is None:
            continue
        task["persona"] = persona["id"]
        task["owner"] = persona["name"]
        task["updated"] = now
        task.setdefault("history", []).append({
            "at": now, "actor": "Pulse",
            "change": f"routed to {persona['name']} by autonomy pulse",
        })
        routed.append({
            "task_id": str(task.get("id") or ""),
            "title": str(task.get("title") or ""),
            "persona": persona["name"],
        })
    return routed


def refresh_studio_ideas() -> dict:
    """Safe background refresh: keep ideas and the working model current."""
    tasks = load_tasks()["tasks"]
    missions = active_missions(load_missions()["missions"])
    galaxy = galaxy_data()
    learnings = load_learning_store()
    candidates = generate_ideas(tasks, missions, galaxy, learnings)
    now = task_timestamp()
    with _ideas_lock:
        stored = load_ideas_store()
        by_id = {str(item.get("id")): item for item in stored["ideas"]}
        merged = []
        for candidate in candidates:
            previous = by_id.get(candidate["id"])
            if previous:
                candidate["state"] = previous.get("state") or "open"
                candidate["created"] = previous.get("created") or now
                for key in ("decided_at", "created_task_id", "decision_note"):
                    if previous.get(key):
                        candidate[key] = previous[key]
            merged.append(candidate)
        known = {str(item.get("id")) for item in merged}
        for previous in stored["ideas"]:
            if previous.get("id") not in known and previous.get("state") != "open":
                merged.append(previous)
        save_ideas_store({"updated": now, "ideas": merged[:200]})
    with _learning_lock:
        current = load_learning_store()
        current["last_refresh"] = now
        save_learning_store(current)
    return {
        "at": now,
        "ideas": sum(1 for item in merged if item.get("state") == "open"),
    }


def brief_ready_tasks(tasks: list[dict], missions: list[dict],
                      limit: int = 3) -> list[dict]:
    """Attach delivery briefs to ready work, strongest candidates first."""
    mission_by_id = {str(mission.get("id") or ""): mission for mission in missions}
    analysis = analyze_dependencies(tasks)
    ready_ids = set(analysis.get("ready") or [])
    next_id = str((analysis.get("next_unlock") or {}).get("task_id") or "")
    candidates = []
    for task in tasks:
        task_id = str(task.get("id") or "")
        status = str(task.get("status") or "In Plan")
        mission_id = str(task.get("mission_id") or "")
        if status in {"Done", "In Test"} or task.get("persona_brief"):
            continue
        if task_id not in ready_ids:
            continue
        if mission_id and mission_id not in mission_by_id:
            continue
        candidates.append(task)
    candidates.sort(key=lambda task: (
        str(task.get("id") or "") != next_id,
        COMMANDER_PRIORITY.get(str(task.get("priority") or "Medium"), 9),
        str(task.get("id") or ""),
    ))
    briefed = []
    now = task_timestamp()
    for task in candidates[:max(0, int(limit))]:
        mission = mission_by_id.get(str(task.get("mission_id") or ""), {})
        persona = crew.for_task(task, str(mission.get("type") or ""))
        if not persona:
            continue
        brief = crew.brief_for(task, persona, mission)
        task["persona"] = persona["id"]
        task["owner"] = persona["name"]
        task["persona_brief"] = brief
        task["updated"] = now
        task.setdefault("history", []).append({
            "at": now, "actor": "Pulse",
            "change": f"delivery brief attached for {persona['name']}; stage may advance, verification unchanged",
        })
        briefed.append({"task_id": task["id"], "title": task["title"],
                        "persona": persona["name"]})
    return briefed


def advance_ready_tasks(tasks: list[dict], missions: list[dict],
                        limit: int = 6) -> list[dict]:
    """Move ready, briefed work one planning stage, never past In Progress."""
    mission_by_id = {str(mission.get("id") or ""): mission for mission in missions}
    analysis = analyze_dependencies(tasks)
    ready_ids = set(analysis.get("ready") or [])
    next_id = str((analysis.get("next_unlock") or {}).get("task_id") or "")
    candidates = []
    for task in tasks:
        task_id = str(task.get("id") or "")
        status = str(task.get("status") or "In Plan")
        mission_id = str(task.get("mission_id") or "")
        if status not in AUTONOMY_STAGE_NEXT:
            continue
        if task_id not in ready_ids or not isinstance(task.get("persona_brief"), dict):
            continue
        if mission_id and mission_id not in mission_by_id:
            continue
        candidates.append(task)
    candidates.sort(key=lambda task: (
        str(task.get("status") or "") != "In Analysis",
        str(task.get("id") or "") != next_id,
        COMMANDER_PRIORITY.get(str(task.get("priority") or "Medium"), 9),
        str(task.get("id") or ""),
    ))
    advanced = []
    now = task_timestamp()
    for task in candidates[:max(0, int(limit))]:
        task_id = str(task.get("id") or "")
        from_status = str(task.get("status") or "")
        to_status = AUTONOMY_STAGE_NEXT[from_status]
        brief = task.get("persona_brief") or {}
        first_move = str((brief.get("first_moves") or [""])[0] or "")
        if from_status == "In Analysis":
            task.setdefault("work_packet", {
                "id": f"WP-{task_id}", "opened_at": now,
                "owner": task.get("owner"),
                "deliverable": brief.get("deliverable"),
                "first_move": first_move,
                "evidence_needed": brief.get("definition_of_done") or [],
                "guardrails": brief.get("guardrails") or [],
            })
            evidence = f"execution packet opened for {brief.get('deliverable') or 'the deliverable'}; first move: {first_move}"
        else:
            evidence = f"delivery brief and acceptance bar attached; first move: {first_move}"
        task["status"] = to_status
        task["updated"] = now
        task["progress_evidence"] = (list(task.get("progress_evidence") or []) + [{
            "at": now, "from": from_status, "to": to_status, "evidence": evidence,
            "note": "Planning evidence only; delivery and independent verification are still open.",
        }])[-12:]
        task.setdefault("history", []).append({
            "at": now, "actor": "Pulse",
            "change": f"advanced {from_status} → {to_status}: {evidence}. No Done claimed.",
        })
        advanced.append({"task_id": task_id, "title": task.get("title"),
                         "from": from_status, "to": to_status,
                         "mission_id": task.get("mission_id")})
    return advanced


def run_autonomy_pulse(brief_decision: bool = True,
                       refresh_studio: bool = True) -> dict:
    """Autonomy: create tasks, route, brief and advance; only spend is gated."""
    if not _pulse_lock.acquire(blocking=False):
        return {"busy": True, "state": autonomy_status()}
    try:
        _pulse_state.update({
            "running": True, "last_run": task_timestamp(),
            "last_ts": time.time(), "error": "",
        })
        with _tasks_lock:
            store = load_tasks()
            tasks = store["tasks"]
            missions = active_missions(load_missions()["missions"])
            routed = route_open_tasks(tasks, missions) if action_allowed("route") else []
            briefed = brief_ready_tasks(tasks, missions, 3) \
                if brief_decision and action_allowed("brief") else []
            advanced = advance_ready_tasks(tasks, missions, 6) \
                if action_allowed("advance") else []
            if routed or briefed or advanced:
                save_tasks(store)
        plan = commander_plan()
        created = []
        if action_allowed("accept_idea"):
            try:
                current_tasks = load_tasks()["tasks"]
                current_missions = active_missions(load_missions()["missions"])
                open_ideas = [
                    item for item in merged_ideas(current_tasks, current_missions, galaxy_data())
                    if item.get("state") == "open"
                ]
                open_ideas.sort(key=lambda item: (-int(item.get("score") or 0), str(item.get("id") or "")))
                if open_ideas:
                    accepted, status = accept_idea_internal(
                        str(open_ideas[0].get("id") or ""), actor="Helios autonomy")
                    if status < 400 and isinstance(accepted.get("task"), dict):
                        created.append({
                            "task_id": str(accepted["task"].get("id") or ""),
                            "title": str(accepted["task"].get("title") or ""),
                            "idea_id": str(open_ideas[0].get("id") or ""),
                        })
            except Exception as exc:  # noqa: BLE001 - task creation must not break the pulse
                print(f"! auto-create task: {exc}", flush=True)
        if routed:
            record_activity(
                "routing", f"Pulse routed {len(routed)} open task{'s' if len(routed) != 1 else ''}",
                "; ".join(f"{item['task_id']} → {item['persona']}" for item in routed[:8]),
                actor="Autonomy",
            )
        if briefed:
            record_activity(
                "brief", f"Pulse prepared {len(briefed)} delivery brief{'s' if len(briefed) != 1 else ''}",
                "; ".join(f"{item['task_id']} → {item['persona']}" for item in briefed[:6])
                + ". Sign-off was not changed.",
                actor="Autonomy",
            )
        if advanced:
            record_activity(
                "progress", f"Pulse advanced {len(advanced)} task{'s' if len(advanced) != 1 else ''}",
                "; ".join(f"{item['task_id']} {item['from']} → {item['to']}"
                         for item in advanced[:6])
                + ". No spend; autonomous work is reported.",
                actor="Autonomy",
            )
        studio_refresh = None
        if refresh_studio and action_allowed("ideas"):
            try:
                studio_refresh = refresh_studio_ideas()
            except Exception as exc:  # noqa: BLE001 - idea refresh must not break the pulse
                print(f"! studio refresh: {exc}", flush=True)
        thought = None
        try:
            thought = record_helios_take(plan)
        except Exception as exc:  # noqa: BLE001 - reflection must not break the pulse
            print(f"! helios take: {exc}", flush=True)
        if created:
            record_activity(
                "idea", f"Helios created {len(created)} task{'s' if len(created) != 1 else ''}",
                "; ".join(f"{item['task_id']} → {item['title']}" for item in created[:4])
                + ". Routed and briefed on the next pulse.",
                actor="Autonomy",
            )
        changed = len(routed) + len(briefed) + len(advanced) + len(created)
        run = {}
        try:
            summary = plan.get("summary") or {}
            run = record_run(
                "pulse",
                story={"knew": f"{summary.get('total', 0)} tasks · {len(plan.get('moves') or [])} ranked moves",
                       "did": f"{len(created)} created · {len(routed)} routed · {len(briefed)} briefed · {len(advanced)} advanced · {changed} visible changes",
                       "need": str((thought or {}).get("text") or "nothing")},
                status="ok", changed=changed, thought=thought,
                policy={"accept_idea": action_allowed("accept_idea"),
                        "route": action_allowed("route"),
                        "brief": action_allowed("brief"),
                        "advance": action_allowed("advance"),
                        "ideas": action_allowed("ideas")},
            )
        except Exception as exc:  # noqa: BLE001 - tracing must not stop the pulse
            print(f"! pulse trace: {exc}", flush=True)
        with _pulse_lock:
            _pulse_state.update({
                "running": False, "cycles": int(_pulse_state.get("cycles") or 0) + 1,
                "last_changed": changed,
            })
        return {"created": created, "routed": routed, "briefed": briefed, "advanced": advanced,
                "changed": changed, "studio": studio_refresh, "thought": thought,
                "run_id": run.get("id") or "",
                "summary": plan.get("summary") or {}, "state": autonomy_status()}
    except Exception as exc:  # noqa: BLE001 - keep the board usable
        with _pulse_lock:
            _pulse_state.update({"running": False, "error": str(exc)[:200]})
        print(f"! pulse: {exc}", flush=True)
        return {"error": str(exc)[:200], "state": autonomy_status()}
    finally:
        _pulse_lock.release()


def activity_payload(limit: int = 40, mission_id: str = "") -> dict:
    mission_id = str(mission_id or "").strip()
    stored = load_activity()
    task_events = []
    for task in load_tasks()["tasks"]:
        if mission_id and str(task.get("mission_id") or "") != mission_id:
            continue
        for item in task.get("history") or []:
            if not isinstance(item, dict):
                continue
            task_events.append({
                "at": item.get("at"), "kind": "task", "actor": item.get("actor"),
                "title": task.get("title"), "detail": item.get("change"),
                "task_id": task.get("id"), "mission_id": task.get("mission_id"),
            })
    events = [event for event in stored
              if not mission_id or str(event.get("mission_id") or "") == mission_id]
    events += task_events
    events.sort(key=lambda item: str(item.get("at") or ""), reverse=True)
    seen = set()
    unique = []
    for item in events:
        key = (item.get("at"), item.get("kind"), item.get("title"), item.get("detail"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return {"events": unique[:max(1, limit)], "autonomy": autonomy_status(),
            "counts": {"total": len(unique), "stored": len(stored)}}


def pulse_keeper() -> None:
    """Internal pulse. It acts autonomously and never spends."""
    time.sleep(30)
    while True:
        try:
            run_autonomy_pulse(brief_decision=True)
        except Exception as exc:  # noqa: BLE001
            print(f"! pulse keeper: {exc}", file=sys.stderr)
        time.sleep(PULSE_INTERVAL)


# -------------------------------------------------------------- commander ----

def priority_rank(task: dict) -> int:
    return COMMANDER_PRIORITY.get(str(task.get("priority") or "Medium"), 9)


def commander_decision_metrics(task: dict, missing: list[str],
                               dependents_count: int,
                               mission_percent: int) -> tuple[int, float, list[str]]:
    """Explainable score: what matters, how certain, and why."""
    priority = str(task.get("priority") or "Medium")
    status = str(task.get("status") or "In Plan")
    base = {"Critical": 84, "High": 70, "Medium": 58, "Low": 40}.get(priority, 52)
    status_bonus = {"In Test": 16, "In Progress": 10, "In Analysis": 4}.get(status, 0)
    impact = min(20, dependents_count * 6)
    urgency = 8 if mission_percent < 25 else 4 if mission_percent < 50 else 0
    readiness = 5 if not missing else -18 - min(12, len(missing) * 4)
    score = max(1, min(99, base + status_bonus + impact + urgency + readiness))
    confidence = 0.94 if not missing and status in {"In Progress", "In Test"} else (
        0.86 if not missing else 0.58
    )
    evidence = [f"{priority.lower()} priority"]
    if dependents_count:
        evidence.append(f"unblocks {dependents_count} other task{'s' if dependents_count != 1 else ''}")
    if mission_percent < 50:
        evidence.append(f"mission {mission_percent}% stage progress")
    if missing:
        evidence.append("waiting on " + ", ".join(missing[:3]))
    else:
        evidence.append("dependencies clear")
    return score, round(confidence, 2), evidence


def commander_plan() -> dict:
    """Read-only, explainable decision assessment of the board."""
    tasks = load_tasks()["tasks"]
    missions = active_missions(load_missions()["missions"])
    summary = task_summary(tasks)
    mission_titles = {str(mission.get("id")): str(mission.get("title") or "")
                      for mission in missions}
    mission_by_id = {str(mission.get("id")): mission for mission in missions}
    by_mission: dict[str, list[dict]] = {}
    for task in tasks:
        by_mission.setdefault(str(task.get("mission_id") or ""), []).append(task)
    mission_percent = {
        str(mission.get("id")): mission_progress(mission, tasks)["percent"]
        for mission in missions
    }

    dependency_analysis = analyze_dependencies(tasks)
    ready: list[dict] = []
    waiting: list[dict] = []
    blocked: list[dict] = []
    signoff: list[dict] = []
    for task in tasks:
        status = str(task.get("status") or "In Plan")
        if status == "Done":
            continue
        task_id = str(task.get("id") or "")
        meta = dependency_analysis["tasks"].get(task_id, {})
        dependency_state = meta.get("state", "ready")
        missing = [str(dep) for dep in meta.get("waiting_on") or []]
        view = {
            "id": str(task.get("id") or ""),
            "title": str(task.get("title") or ""),
            "status": status,
            "owner": str(task.get("owner") or "CEO Agent"),
            "priority": str(task.get("priority") or "Medium"),
            "mission_id": str(task.get("mission_id") or ""),
            "mission_title": mission_titles.get(
                str(task.get("mission_id") or ""), "No mission"),
        }
        mission = mission_by_id.get(str(task.get("mission_id") or ""), {})
        view["persona"] = crew.public(crew.for_task(
            task, str(mission.get("type") or "")))
        score, confidence, evidence = commander_decision_metrics(
            task, missing, meta.get("unlocks", 0),
            mission_percent.get(view["mission_id"], 0)
        )
        if task_id in (summary.get("critical_path") or []):
            evidence.append("on the critical path")
        view.update(score=score, confidence=confidence, evidence=evidence,
                    dependency_state=dependency_state,
                    unlocks=meta.get("unlocks", 0))
        if dependency_state == "blocked":
            view["action"] = "repair"
            view["why"] = meta.get("reason") or meta.get("broken") or "broken dependency link"
            view["broken"] = view["why"]
            blocked.append(view)
            continue
        if dependency_state == "waiting":
            view["missing"] = missing
            view["action"] = "wait"
            view["why"] = ("sequenced behind " + ", ".join(missing[:3])
                           if missing else "waiting on a predecessor")
            waiting.append(view)
            continue
        if status == "In Test" and not task.get("verified"):
            view["action"] = "verify"
            view["why"] = "evidence is ready for autonomous verification"
            signoff.append(view)
        else:
            view["action"] = "advance"
            view["why"] = f"unblocked {status.lower()} work"
        ready.append(view)

    critical_task = (summary.get("next_unlock") or {}).get("task_id")
    ready.sort(key=lambda item: (
        0 if critical_task and item["id"] == critical_task else 1,
        -item["score"], priority_rank(item), item["mission_id"], item["id"]
    ))
    waiting.sort(key=lambda item: (
        -item["score"], priority_rank(item), item["id"]
    ))
    blocked.sort(key=lambda item: (
        -item["score"], priority_rank(item), item["id"]
    ))
    signoff.sort(key=lambda item: (
        -item["score"], priority_rank(item), item["id"]
    ))
    moves = ready[:5] if ready else blocked[:5]
    decision = None
    if moves:
        top = moves[0]
        decision = {
            "task_id": top["id"], "title": top["title"],
            "action": top["action"], "score": top["score"],
            "confidence": top["confidence"], "reason": top["why"],
            "evidence": top["evidence"], "mission_id": top["mission_id"],
            "mission_title": top["mission_title"], "persona": top.get("persona"),
        }

    mission_views = []
    for mission in missions:
        owned = by_mission.get(str(mission.get("id")), [])
        progress = mission_progress(mission, tasks)
        next_task = next((item for item in ready
                          if item["mission_id"] == str(mission.get("id"))), None)
        mission_views.append({
            "id": str(mission.get("id") or ""),
            "title": str(mission.get("title") or ""),
            "type": str(mission.get("type") or "Custom"),
            "progress": progress,
            "next": next_task,
            "owners": sorted({str(task.get("owner") or "CEO Agent")
                              for task in owned}),
            "lead": crew.public(crew.resolve(mission.get("lead_persona"))
                                or crew.resolve(mission.get("owner"))),
            "gates": mission_gates(mission, tasks),
            "crew": crew.mission_crew(mission, tasks),
        })

    fingerprint_data = {
        "tasks": [[task.get("id"), task.get("status"), task.get("verified"),
                   task.get("updated")] for task in tasks],
        "missions": [[mission.get("id"), mission.get("title"),
                      mission.get("updated")] for mission in missions],
    }
    fingerprint = hashlib.sha1(
        json.dumps(fingerprint_data, sort_keys=True).encode("utf-8")
    ).hexdigest()
    if not tasks:
        narrative = (
            "No missions or tasks yet. Create a mission to give the Commander "
            "something concrete to drive."
        )
    else:
        first = moves[0] if moves else None
        signoff_note = (
            f"{len(signoff)} ready for verification."
            if signoff else "No tasks are in In Test."
        )
        narrative = (
            f"{len(missions)} mission{'' if len(missions) == 1 else 's'}, "
            f"{summary['total']} tasks: {summary['done']} done, "
            f"{summary['blocked']} broken, {summary['waiting']} waiting, "
            f"{summary['unverified']} unverified. {signoff_note} "
            + (f"Start with {first['id']} — {first['title']}." if first else
               "There is no unblocked move; repair a broken dependency first.")
        )
    return {
        "summary": summary,
        "missions": mission_views,
        "moves": moves,
        "waiting": waiting[:6],
        "blocked": blocked[:6],
        "signoff": signoff[:6],
        "decision": decision,
        "pending_approvals": len(pending_approvals()),
        "fingerprint": fingerprint,
        "narrative": narrative,
    }


def work_context() -> str:
    """Compact board state injected into every chat request."""
    plan = commander_plan()
    summary = plan["summary"]
    if not summary["total"]:
        return "No missions or tasks exist yet."
    lines = [
        f"{len(plan['missions'])} mission(s), {summary['total']} tasks: "
        f"{summary['done']} done, {summary['ready']} ready, {summary['waiting']} waiting, "
        f"{summary['blocked']} broken, {summary['unverified']} unverified; "
        f"{len(plan['signoff'])} ready for verification."
    ]
    for mission in plan["missions"][:5]:
        progress = mission["progress"]
        next_task = mission.get("next")
        next_text = (
            f"{next_task['id']} {next_task['title']} ({next_task['action']})"
            if next_task else "no unblocked next task"
        )
        lines.append(
            f"- {mission['id']} {mission['title']} [{mission['type']}]: "
            f"{progress['done']}/{progress['total']} done; next: {next_text}."
        )
    if plan["moves"]:
        lines.append("Next moves: " + "; ".join(
            f"{move['id']} {move['title']} ({move['action']}, {move['owner']}"
            f" / {(move.get('persona') or {}).get('name', 'unassigned')}) "
            f"[status: {move.get('status') or 'unknown'}]"
            for move in plan["moves"]
        ))
    selected = plan.get("decision") or (plan["moves"][0] if plan["moves"] else {})
    selected_task = next((task for task in load_tasks()["tasks"]
                          if str(task.get("id")) == str(selected.get("task_id") or selected.get("id"))),
                         None)
    if selected_task:
        description = str(selected_task.get("description") or "").strip()
        if description:
            lines.append(
                f"Selected task detail: {selected_task.get('id')} — {selected_task.get('title')}. "
                f"{description[:600]}"
            )
    if plan.get("waiting"):
        lines.append("Waiting on sequence: " + "; ".join(
            f"{item['id']} behind {', '.join(item.get('missing') or [])}"
            for item in plan["waiting"][:4]
        ))
    if plan["blocked"]:
        lines.append("Broken dependencies: " + "; ".join(
            f"{item['id']} {item.get('broken') or 'needs repair'}"
            for item in plan["blocked"][:4]
        ))
    if plan["signoff"]:
        lines.append("Verification queue: " + "; ".join(
            f"{item['id']} {item['title']}" for item in plan["signoff"][:4]
        ))
    if plan["pending_approvals"]:
        lines.append(f"Pending spend approvals: {plan['pending_approvals']}.")
    return "\n".join(lines)[:2400]


def recent_activity_context(limit: int = 6) -> str:
    """Today's operator-visible events, for questions about what happened."""
    today = time.strftime("%Y-%m-%d")
    events = [
        event for event in load_activity()
        if str(event.get("at") or "").startswith(today)
    ]
    lines = []
    for event in events[:limit]:
        at = str(event.get("at") or "")
        title = str(event.get("title") or "").strip()
        detail = str(event.get("detail") or "").strip()
        if not title:
            continue
        when = at[11:16] if len(at) >= 16 else at
        lines.append(f"- {when} {title}" + (f" — {detail}" if detail else ""))
    return "\n".join(lines)


def local_operator_answer(message: str) -> str | None:
    """Live board and activity answers that must be exact, not generative."""
    query = normalize_operator_query(message)
    task_match = TASK_QUERY_RE.fullmatch(query)
    if task_match:
        task_id = task_match.group(1).upper()
        task = next((item for item in load_tasks()["tasks"]
                     if str(item.get("id")) == task_id), None)
        if task is None:
            return f"I can’t find {task_id} on the board."
        deps = ", ".join(str(dep) for dep in (task.get("dependencies") or [])) or "none"
        answer = (
            f"{task_id} — {task.get('title') or 'Untitled task'}. "
            f"Status: {task.get('status') or 'unknown'}; "
            f"owner: {task.get('owner') or 'unassigned'}; dependencies: {deps}."
        )
        if str(task.get("status")) == "In Test" and not task.get("verified"):
            answer += " It is in test and not yet verified."
        return answer

    if NEXT_QUERY_RE.fullmatch(query):
        plan = commander_plan()
        decision = plan.get("decision") or {}
        if not decision:
            return "There is no unblocked move. Repair the blocking dependency first."
        owner = (decision.get("persona") or {}).get("name") or "unassigned"
        unlock = plan.get("summary", {}).get("next_unlock") or {}
        unlock_text = (
            f" It unlocks {unlock['unlocks']} tasks on the critical path."
            if unlock.get("task_id") == decision.get("task_id") and unlock.get("unlocks")
            else ""
        )
        return (
            f"Start with {decision.get('task_id')} — {decision.get('title')}. "
            f"{owner} owns it; the next action is to {decision.get('action') or 'advance'} it."
            f"{unlock_text}"
        )

    if STATUS_QUERY_RE.fullmatch(query):
        plan = commander_plan()
        summary = plan.get("summary") or {}
        decision = plan.get("decision") or {}
        parts = [
            f"{summary.get('total', 0)} tasks across {len(plan.get('missions') or [])} "
            f"missions: {summary.get('done', 0)} done, {summary.get('ready', 0)} ready, "
            f"{summary.get('waiting', 0)} waiting, {summary.get('blocked', 0)} broken."
        ]
        if decision:
            parts.append(f"Next: {decision.get('task_id')} — {decision.get('title')}.")
        signoff = plan.get("signoff") or []
        if signoff:
            parts.append("Sign-off ready: " + "; ".join(
                f"{item.get('id')} {item.get('title')}" for item in signoff[:3]
            ) + ".")
        else:
            in_test = [task for task in load_tasks()["tasks"]
                       if str(task.get("status")) == "In Test"]
            if in_test:
                task = in_test[0]
                missing = next((item.get("missing") for item in plan.get("waiting") or []
                                if item.get("id") == task.get("id")), [])
                if missing:
                    parts.append(
                        f"{task.get('id')} is In Test but waiting on "
                        f"{', '.join(str(dep) for dep in missing)}; nothing is ready for verification."
                    )
                else:
                    parts.append("No task is ready for verification.")
            else:
                parts.append("No task is ready for verification.")
        return " ".join(parts)

    if TODAY_QUERY_RE.fullmatch(query):
        today = time.strftime("%Y-%m-%d")
        events = [event for event in load_activity()
                  if str(event.get("at") or "").startswith(today)]
        if not events:
            return "No operator-visible activity is recorded today."
        lines = []
        for event in events[:4]:
            at = str(event.get("at") or "")
            when = at[11:16] if len(at) >= 16 else at
            title = str(event.get("title") or "").strip()
            if title:
                lines.append(f"{when} {title}")
        return "Today: " + "; ".join(lines) + "."

    if BLOCKED_QUERY_RE.fullmatch(query):
        plan = commander_plan()
        summary = plan.get("summary") or {}
        blocked = plan.get("blocked") or []
        waiting = plan.get("waiting") or []
        if blocked:
            return "Broken dependencies: " + "; ".join(
                f"{item.get('id')} {item.get('broken') or 'needs repair'}" for item in blocked[:4]
            ) + "."
        if waiting:
            sample = "; ".join(
                f"{item.get('id')} behind {', '.join(str(dep) for dep in item.get('missing') or [])}"
                for item in waiting[:3]
            )
            return (
                f"No broken dependencies. {summary.get('waiting', 0)} tasks are waiting "
                f"on earlier steps, including {sample}. Nothing is blocked."
            )
        return "No broken dependencies and nothing is waiting."

    if SIGNOFF_QUERY_RE.fullmatch(query):
        plan = commander_plan()
        signoff = plan.get("signoff") or []
        if signoff:
            return "Ready for verification: " + "; ".join(
                f"{item.get('id')} {item.get('title')}" for item in signoff[:5]
            ) + "."
        in_test = [task for task in load_tasks()["tasks"]
                   if str(task.get("status")) == "In Test"]
        if in_test:
            task = in_test[0]
            missing = next((item.get("missing") for item in plan.get("waiting") or []
                            if item.get("id") == task.get("id")), [])
            if missing:
                return (
                    f"Nothing is ready for verification. {task.get('id')} is In Test but "
                    f"still waiting on {', '.join(str(dep) for dep in missing)}."
                )
        return "Nothing is ready for verification."

    if APPROVAL_QUERY_RE.fullmatch(query):
        pending = pending_approvals()
        if not pending:
            return "No spend approvals are waiting."
        return "Spend approvals waiting: " + "; ".join(
            f"{item.get('id')} {item.get('title')} — "
            f"{item.get('amount')} {item.get('currency') or 'GBP'}"
            for item in pending[:5]
        ) + "."

    if IDEAS_QUERY_RE.fullmatch(query):
        ideas = [item for item in load_ideas_store().get("ideas") or []
                 if item.get("state") == "open"]
        if not ideas:
            return "There are no open ideas in the queue."
        return "Open ideas: " + "; ".join(
            str(item.get("title") or "Untitled idea") for item in ideas[:3]
        ) + "."

    if CAPABILITY_QUERY_RE.fullmatch(query):
        return (
            "I can report the next move, board status, today’s activity, blockers, "
            "verification status, spend approvals and the idea queue from live state. I use the "
            "local model for drafting and judgement."
        )

    return None


def load_commander() -> dict:
    try:
        data = json.loads(COMMANDER_PATH.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_commander(payload: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = COMMANDER_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(COMMANDER_PATH)


def commander_base_payload(plan: dict, previous: dict | None = None) -> dict:
    """Deterministic snapshot plus an appendable decision log."""
    provider, _llm, _key = llm_settings(config())
    log = list((previous or {}).get("decision_log") or [])
    decision = plan.get("decision")
    if decision and (not log or log[0].get("fingerprint") != plan["fingerprint"]
                     or log[0].get("task_id") != decision.get("task_id")):
        log.insert(0, {
            "at": task_timestamp(), "fingerprint": plan["fingerprint"],
            "task_id": decision["task_id"], "title": decision["title"],
            "action": decision["action"], "score": decision["score"],
            "confidence": decision["confidence"],
            "mission_id": decision.get("mission_id", ""),
            "persona": (decision.get("persona") or {}).get("name", ""),
            "reason": decision.get("reason", ""),
        })
    return {
        "generated": task_timestamp(), "generated_ts": time.time(),
        "fingerprint": plan["fingerprint"], "plan_version": COMMANDER_PLAN_VERSION,
        "intent": plan["narrative"],
        "provider": provider, "model": brain_status(config())["model"],
        "error": "", "decision_log": log[:12],
    }


def generate_commander_intent(plan: dict) -> tuple[str, str | None]:
    """Let the local brain narrate the deterministic plan, with a safe fallback."""
    fallback = plan["narrative"]
    cfg = config()
    llm = dict(cfg.get("llm") or {})
    llm["timeout_seconds"] = min(float(llm.get("timeout_seconds") or 120), 60)
    llm["max_tokens"] = min(int(llm.get("max_tokens") or 600), 160)
    cfg = {**cfg, "llm": llm}
    brain = brain_status(cfg)
    if not brain.get("ready"):
        return fallback, brain.get("error") or "brain not ready"
    notes = brief_notes(galaxy_data())[:3]
    note_text = "\n\n".join(
        f"[{node['id']}] {node['title']} — {node['excerpt'][:350]}"
        for node in notes
    ) or "No matching notes."
    system = (
        "You are Helios The Commander with a crew of accountable specialist "
        "personas. Read the live WORK SNAPSHOT and NOTES. Write at most 90 "
        "words of commander's intent: the single best next move, the persona "
        "best placed to deliver it, why it matters now, and the one risk to "
        "watch. Use only the snapshot and notes; never invent facts, completed "
        "work, spending, publishing, or approval. Personas add judgement, not "
        "authority. Do not mark anything done. Speak directly to the operator."
    )
    snapshot = json.dumps({
        "summary": plan["summary"],
        "moves": plan["moves"],
        "blocked": plan["blocked"],
        "signoff": plan["signoff"],
        "missions": [
            {"id": item["id"], "title": item["title"],
             "progress": item["progress"]}
            for item in plan["missions"][:6]
        ],
    }, ensure_ascii=False)
    answer, error = call_llm(cfg, [
        {"role": "system", "content": system},
        {"role": "user", "content": f"WORK SNAPSHOT:\n{snapshot}\n\nNOTES:\n{note_text}\n\nWrite the Commander's intent for this cycle."},
    ])
    if error or not answer:
        return fallback, error or "empty commander answer"
    answer, _spend = extract_spend_request(answer)
    answer = answer.strip()
    return (answer[:700] or fallback), None


def run_commander_cycle(plan: dict | None = None) -> dict:
    """Background observation cycle. It never mutates tasks or spends money."""
    plan = plan if plan is not None else commander_plan()
    payload = commander_base_payload(plan, load_commander())
    try:
        # Publish the deterministic read immediately; the model only refines it.
        save_commander(payload)
        intent, error = generate_commander_intent(plan)
        if intent:
            payload["intent"] = intent
        payload["error"] = error or ""
        save_commander(payload)
        with _commander_lock:
            _commander_state.update({
                "generating": False, "attempted": time.time(),
                "error": error or "",
            })
        print("  commander: cycle complete", flush=True)
        return payload
    except Exception as exc:  # noqa: BLE001 - keep the board usable
        with _commander_lock:
            _commander_state.update({
                "generating": False, "error": str(exc)[:200],
            })
        print(f"! commander: {exc}", flush=True)
        return payload


def commander_payload(force: bool = False, narrate: bool = False) -> dict:
    """Serve the plan immediately; only an explicit cycle uses the model."""
    plan = commander_plan()
    cached = load_commander()
    with _commander_lock:
        generating = bool(_commander_state.get("generating"))
        error = str(_commander_state.get("error") or cached.get("error") or "")
    fresh = (bool(cached) and cached.get("fingerprint") == plan["fingerprint"]
             and cached.get("plan_version") == COMMANDER_PLAN_VERSION)
    age = time.time() - float(cached.get("generated_ts") or 0.0)
    if not narrate:
        if not fresh:
            cached = commander_base_payload(plan, cached)
            try:
                save_commander(cached)
            except OSError as exc:
                error = str(exc)[:200]
        return {"ready": bool(cached), "generating": False, "error": error,
                "intent": cached.get("intent", ""),
                "model": cached.get("model", ""),
                "provider": cached.get("provider", ""),
                "generated": cached.get("generated", ""),
                "decision_log": cached.get("decision_log") or [], "plan": plan}
    if not force and fresh and age < 1800:
        return {"ready": True, "generating": generating, "error": error,
                "intent": cached.get("intent", ""),
                "model": cached.get("model", ""),
                "provider": cached.get("provider", ""),
                "generated": cached.get("generated", ""),
                "decision_log": cached.get("decision_log") or [], "plan": plan}
    start = False
    if not generating and (force or not fresh or age >= 1800):
        with _commander_lock:
            if not _commander_state.get("generating"):
                _commander_state.update({
                    "generating": True, "attempted": time.time(), "error": "",
                })
                start = True
        if start:
            threading.Thread(target=run_commander_cycle,
                             args=(plan,), daemon=True).start()
    return {"ready": bool(cached), "generating": generating or start,
            "error": error, "intent": cached.get("intent", ""),
            "model": cached.get("model", ""),
            "provider": cached.get("provider", ""),
            "generated": cached.get("generated", ""),
            "decision_log": cached.get("decision_log") or [], "plan": plan}


def apply_commander_briefing(body: dict) -> tuple[dict, int]:
    """Attach a grounded briefing without changing status or evidence."""
    task_id = str(body.get("task_id") or "").strip()
    if not task_id:
        return {"error": "task id required"}, 400
    plan = commander_plan()
    move = next((item for item in plan.get("moves") or []
                 if item.get("id") == task_id), None)
    if move is None:
        return {"error": "task is not a current Commander move"}, 404
    galaxy = galaxy_data()
    with _tasks_lock:
        store = load_tasks()
        tasks = store["tasks"]
        task = next((item for item in tasks if item.get("id") == task_id), None)
        if task is None:
            return {"error": "task not found"}, 404
        notes = retrieve(f"{task.get('title')} {task.get('description')}", galaxy, limit=3)
        evidence = "; ".join(move.get("evidence") or [])
        sources = ", ".join(f"[{note['id']}] {note['title']}" for note in notes) or "no note matches"
        briefing = (
            f"Commander decision: {move['action']} {task['title']}. "
            f"Why now: {move['why']}. Evidence: {evidence}. "
            f"Risk to watch: {task.get('risks') or 'none recorded'}. "
            f"Sources: {sources}."
        )[:1200]
        task["briefing"] = briefing
        existing = [int(note) for note in (task.get("note_ids") or [])
                    if str(note).isdigit()]
        task["note_ids"] = task_note_ids(
            existing + [note["id"] for note in notes], galaxy
        )
        task["updated"] = task_timestamp()
        task.setdefault("history", []).append({
            "at": task["updated"], "actor": "Commander",
            "change": f"briefing attached from decision {move['score']} / {move['confidence']}",
        })
        save_tasks(store)
    return {"task": task, "briefing": briefing, "decision": move}, 200


def crew_workspace_payload() -> dict:
    """Persona catalogue plus live allocation and routing suggestions."""
    store = load_tasks()
    missions = active_missions(load_missions()["missions"])
    payload = crew.crew_payload(store["tasks"], missions)
    payload["updated"] = store.get("updated")
    payload["autonomy"] = autonomy_status()
    return payload


def apply_crew_brief(body: dict) -> tuple[dict, int]:
    """Assign a persona and attach a concrete delivery brief."""
    task_id = str(body.get("task_id") or "").strip()
    if not task_id:
        return {"error": "task id required"}, 400
    with _tasks_lock:
        store = load_tasks()
        tasks = store["tasks"]
        task = next((item for item in tasks if item.get("id") == task_id), None)
        if task is None:
            return {"error": "task not found"}, 404
        if str(task.get("status") or "") == "Done":
            return {"error": "this task is already done"}, 409
        mission = next((item for item in load_missions()["missions"]
                        if str(item.get("id")) == str(task.get("mission_id") or "")), {})
        mission_type = str(mission.get("type") or "")
        persona = (crew.resolve(body.get("persona"))
                   or crew.resolve(body.get("persona_id"))
                   or crew.for_task(task, mission_type))
        if persona is None:
            return {"error": "persona not found"}, 400
        brief = crew.brief_for(task, persona, mission)
        task["persona"] = persona["id"]
        task["owner"] = persona["name"]
        task["persona_brief"] = brief
        task["updated"] = task_timestamp()
        task.setdefault("history", []).append({
            "at": task["updated"], "actor": persona["name"],
            "change": f"delivery brief attached ({persona['title']})",
        })
        save_tasks(store)
        summary = task_summary(tasks)
    record_activity(
        "crew", f"Crew brief: {task_id} {task.get('title')}",
        f"{persona['name']} owns the next delivery; {brief['deliverable']} is the immediate deliverable.",
        actor=persona["name"], task_id=task_id,
        mission_id=str(task.get("mission_id") or ""),
    )
    return {"task": task, "brief": brief, "summary": summary}, 200


def commander_keeper() -> None:
    """Observe the board in the background so the Commander is always current."""
    time.sleep(18)
    while True:
        try:
            commander_payload(narrate=False)
        except Exception as exc:  # noqa: BLE001
            print(f"! commander keeper: {exc}", file=sys.stderr)
        time.sleep(600)


# ----------------------------------------------------------------- prompt ----

def build_system(cfg: dict, notes: list[dict], pending: list[dict],
                 work: str = "", mode: str = "ask") -> str:
    base = str(cfg.get("system_prompt") or DEFAULT_SYSTEM).strip()
    rules = [str(rule).strip() for rule in (cfg.get("rules") or []) if str(rule).strip()]
    money = cfg.get("money") or {}

    parts = [base, ""]
    mode_instruction = MODE_INSTRUCTIONS.get(mode, "").strip()
    if mode_instruction:
        parts.extend([mode_instruction, ""])
    if rules:
        parts.append("Standing rules from your operator (always obeyed):")
        parts.extend(f"- {rule}" for rule in rules)
        parts.append("")
    parts.append(f"Current local time: {clock_text()}.")
    parts.append("")
    parts.append(
        "Money: you may propose ways to earn money, but you cannot spend, pay, "
        "buy or transfer money. Every spend stays blocked until the operator "
        "approves a request in the approvals queue "
        f"(currency {money.get('currency', 'GBP')}). Never claim a payment or "
        "purchase has happened."
    )
    parts.append(
        "OPERATOR VOICE: you are the operator's personal chief of staff and "
        "extension, not a log viewer or a menu. Think in their voice, support "
        "their judgement, protect their time, and do the work. Speak like a "
        "person in a live conversation: follow the thread, use what was just "
        "said, and answer the actual question first. For ordinary conversation, "
        "be warm and natural; do not recite board state. Never print JSON, field "
        "names, SPEND_REQUEST, tool calls, source ids, file paths or internal "
        "reasoning in the operator-facing answer. The interface shows sources "
        "separately."
    )
    parts.append(
        "SPENDING RULE: speak about money only when the operator asks about it or "
        "you are proposing a specific purchase. If a purchase would help, put one "
        "plain sentence in the answer, then end with one machine-only line exactly "
        'like SPEND_REQUEST: {"title": "...", "amount": 10.00, "reason": "..."} '
        "using a positive amount. Never file or mention a spend request for "
        "zero-cost work. Never write a placeholder such as <name>, and never add a "
        "no-spend line to a normal answer. The operator must approve every real "
        "spend in the queue."
    )
    parts.append(
        "THINK FOR YOURSELF: act without waiting for permission — read, rank, "
        "draft, create tasks, route, brief, advance, publish, send and delete. "
        "Ask the operator only for spending money. If unsure, take the best "
        "reversible action and tell them what you did. When the operator is "
        "talking, listen and respond to the person; when there is work to move, "
        "state the next move rather than offering a menu."
    )
    if pending:
        parts.append("Spend requests awaiting the operator: " + "; ".join(
            f"{item.get('id')} {item.get('title')} "
            f"({item.get('amount')} {item.get('currency', '')})"
            for item in pending[:5]
        ))
    parts.append(
        "HELIOS CREW: you have specialist personas — Mission Controller, Systems "
        "Architect, Product Designer, UI Engineer, Project Manager, Secretary, "
        "App Orchestrator, Admin, Research Analyst, Red Team, Content Strategist "
        "and Data Steward. Route each next deliverable to the smallest accountable "
        "persona. Red Team is independent from the builder. Personas add judgement, "
        "not authority: never claim they spent, published, deleted or signed off."
    )
    parts.append("")
    answer_style = {
        "plan": "give a numbered plan of 3-5 concrete steps and the first action",
        "draft": "write the requested draft directly, ready to copy",
        "decide": "make one recommendation with its strongest reason and main risk",
        "brief": "follow the brief format below and keep it under 170 words",
    }.get(mode, "answer naturally in 2-4 short sentences, using more only when it genuinely helps")
    parts.append(
        "Ground every work claim in the NOTES, MISSION CONTROL and RECENT "
        "ACTIVITY below. For greetings, thanks, preferences, ideas and ordinary "
        "conversation, answer the person naturally instead of forcing board data "
        "into the reply. Never invent tasks, statuses, numbers or completed "
        f"actions. {answer_style}. Write like a person: use contractions, vary "
        "sentence length, and avoid canned openings such as 'Based on the "
        "provided context'. Do not use headings, tables, field names or bullet "
        "lists unless the mode calls for that structure. When a factual board or "
        "activity question is not covered by the context, say so plainly. Use "
        "exact task IDs, counts and dated events when they matter, and never say "
        "no information is available when those sections contain the answer. Do "
        "not put note ids in the operator-facing answer; the interface attaches "
        "the sources."
    )
    if notes:
        parts.append("\nNOTES:\n" + "\n\n".join(
            f"[{node['id']}] {node['title']} \u2014 {node['group']}\n{node['excerpt']}"
            for node in notes
        ))
    else:
        parts.append("\nNOTES: none of the notes match this question.")
    activity = recent_activity_context()
    parts.append("")
    parts.append("RECENT ACTIVITY (today, operator-visible events):")
    parts.append(activity or "No operator-visible events are recorded today.")
    parts.append(
        "If asked what happened today, lead with one event from RECENT ACTIVITY. "
        "Do not substitute the next task or answer with a spending line."
    )
    if work:
        parts.append("")
        parts.append("MISSION CONTROL (authoritative for next actions; read-only):")
        parts.append(work)
        parts.append(
            "Use MISSION CONTROL as the source of truth for next actions. The "
            "next move above overrides any different task mentioned in NOTES. "
            "Never claim a task is done unless its status is Done. Only tasks "
            "in In Test can be verified; do not call other open tasks verified. "
            "Only spending requires approval; act and report everything else."
        )
    return "\n".join(parts)


def call_llm(cfg: dict, messages: list[dict]) -> tuple[str | None, str | None]:
    """Ask DeepSeek when keyed, with a local Ollama fallback if it fails."""
    provider, llm, key = llm_settings(cfg)
    if provider == "ollama":
        return call_ollama(cfg, messages)
    answer, error = call_openai(cfg, messages, llm, key)
    if answer and not error:
        return answer, None
    print(f"! remote brain unavailable ({error}); falling back to local Ollama",
          file=sys.stderr)
    local_answer, local_error = call_ollama(cfg, messages)
    if local_answer:
        return local_answer, None
    return None, f"remote: {error}; local fallback: {local_error}"


def call_ollama(cfg: dict, messages: list[dict]) -> tuple[str | None, str | None]:
    llm = cfg.get("llm") or {}
    model = str(llm.get("model") or DEFAULT_OLLAMA_MODEL)
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": bool(llm.get("think", False)),
        "options": {
            "temperature": float(llm.get("temperature", 0.3)),
            "num_predict": int(llm.get("max_tokens", 400)),
            "num_ctx": int(llm.get("num_ctx", 8192)),
        },
    }
    request = urllib.request.Request(
        ollama_url(llm) + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "Helios/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=float(llm.get("timeout_seconds", 120))
        ) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
        answer = (data.get("message") or {}).get("content")
        if not answer:
            return None, "Ollama returned an empty answer"
        return str(answer).strip(), None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        return None, f"ollama error {exc.code}: {detail}"
    except Exception as exc:  # noqa: BLE001 - surface any transport failure
        return None, f"ollama error: {exc}"


def call_openai(cfg: dict, messages: list[dict], llm: dict,
                key: str) -> tuple[str | None, str | None]:
    """Call an OpenAI-compatible chat completions endpoint."""
    base = str(llm.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    payload = {
        "model": llm.get("model") or "gpt-4o-mini",
        "messages": messages,
        "temperature": float(llm.get("temperature", 0.3)),
        "max_tokens": int(llm.get("max_tokens", 400)),
    }
    if llm.get("dialect") == "deepseek":
        thinking = bool(llm.get("thinking", False))
        payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
        if thinking:
            payload.pop("temperature", None)
            payload["reasoning_effort"] = str(llm.get("reasoning_effort") or "low")
    request = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "User-Agent": "Helios/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=float(llm.get("timeout_seconds", 60))
        ) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
        answer = data["choices"][0]["message"]["content"]
        return str(answer).strip(), None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400].replace(key, "[redacted]")
        return None, f"model error {exc.code}: {detail}"
    except Exception as exc:  # noqa: BLE001 - surface any transport failure
        return None, f"model error: {exc}".replace(key, "[redacted]")


# ------------------------------------------------------------------ chat ----

BRIEF_INSTRUCTIONS = (
    "BRIEF MODE: write a compact brief with three sections: WHAT'S ACTIVE, "
    "CONNECTIONS WORTH MAKING, NEXT THREE ACTIONS. Keep it under 170 words. "
    "Every action must be free or use what the operator already owns; never "
    "spend money. Cite notes by [id]. Focus on the operator's own projects "
    "and plans; do not fill the brief with notes that only document this assistant."
)

CHAT_MODES = ("ask", "plan", "draft", "decide", "brief")
MODE_LABELS = {
    "ask": "Ask", "plan": "Plan", "draft": "Draft",
    "decide": "Decide", "brief": "Brief",
}
MODE_INSTRUCTIONS = {
    "ask": (
        "ASK MODE: begin with the answer itself. Add only the context needed to "
        "act on it."
    ),
    "plan": (
        "PLAN MODE: begin with PLAN: then give 3-5 numbered, concrete steps. For "
        "each step, include the owner and any dependency or task id the context "
        "provides. End with the single first action. Do not open with 'Based on' "
        "and do not claim anything was executed."
    ),
    "draft": (
        "DRAFT MODE: begin with DRAFT: then write only the requested text, ready "
        "to copy. Preserve the operator's voice, keep it concise, and do not invent "
        "facts. Use the selected task's title and description as the source of "
        "truth; do not reinterpret or broaden the mission. Do not add a Status "
        "field unless that exact status is supplied in MISSION CONTROL. Do not "
        "send or publish it."
    ),
    "decide": (
        "DECIDE MODE: begin with RECOMMENDATION: and name exactly one option. Give "
        "the strongest reason, the main risk or counterargument, and a confidence "
        "from 0 to 100. Do not give a numbered plan or hedge with equal options."
    ),
}


VOICE_CONVERSATION = (
    "VOICE CONVERSATION: this reply will be spoken aloud. Talk back like a "
    "person on a call: 1-3 short sentences, contractions, and no lists, "
    "headings, markdown, URLs or stage directions. Start with the substance, "
    "not 'Certainly' or 'Of course'. Use the recent conversation for "
    "follow-ups and pronouns. If more detail would help, give the headline "
    "and ask one natural follow-up."
)


def chat_suggestions(mode: str, notes: list[dict]) -> list[str]:
    """Small, deterministic next prompts shown under an answer."""
    top = str((notes[0] if notes else {}).get("title") or "").strip()
    if mode == "plan":
        return ["Turn the first step into a task", "What could block this?",
                "Handoff to Freebuff"]
    if mode == "draft":
        return ["Make it shorter", "Make it warmer", "Create a task to send it"]
    if mode == "decide":
        return ["What is the strongest counterargument?", "What are the risks?",
                "Turn this decision into a task"]
    if mode == "brief":
        return ["What changed since yesterday?", "What is the biggest risk?",
                "Plan my next 30 minutes"]
    if top:
        return [f"Go deeper on {top[:64]}", "What should I do next?",
                "Find related notes"]
    return ["What should I do next?", "What am I missing?", "Connect the dots"]


def brief_notes(galaxy: dict) -> list[dict]:
    """Sample the strongest notes across the biggest groups for a brief."""
    nodes = galaxy.get("nodes") or []
    by_group: dict[str, list[dict]] = {}
    for node in nodes:
        by_group.setdefault(str(node.get("group") or "ungrouped"), []).append(node)
    groups = sorted(by_group.values(), key=lambda group: -len(group))
    picked: list[dict] = []
    seen: set = set()
    for group in groups[:8]:
        for node in sorted(group, key=lambda item: -int(item.get("degree") or 0))[:1]:
            if node.get("id") not in seen:
                seen.add(node.get("id"))
                picked.append(node)
    for node in sorted(nodes, key=lambda item: -int(item.get("degree") or 0))[:2]:
        if node.get("id") not in seen:
            seen.add(node.get("id"))
            picked.append(node)
    return picked[:10]


def today_key() -> str:
    return time.strftime("%Y-%m-%d")


def load_daily_brief() -> dict:
    """Today's prepared brief, from memory or the daily cache file."""
    today = today_key()
    with _brief_lock:
        current = _brief_state.get("brief") or {}
        active_model = str((config().get("llm") or {}).get("model") or "").strip()
        if (_brief_state.get("ready") and current.get("date") == today
                and (not current.get("model") or str(current.get("model")).strip() == active_model)):
            return dict(current)
    try:
        stored = json.loads(BRIEF_PATH.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(stored, dict) or stored.get("date") != today:
        return {}
    if not str(stored.get("answer") or "").strip():
        return {}
    configured_model = str((config().get("llm") or {}).get("model") or "").strip()
    stored_model = str(stored.get("model") or "").strip()
    if configured_model and stored_model and configured_model != stored_model:
        return {}
    with _brief_lock:
        _brief_state.update({
            "date": today, "ready": True, "error": "", "brief": stored,
        })
    return stored


def generate_daily_brief(force: bool = False) -> dict:
    """Prepare today's brief in the background.

    This is deliberately read-only apart from ``data/daily_brief.json``:
    a SPEND_REQUEST from the model is stripped and never filed, because the
    brief must not create side effects before the operator has read it.
    """
    cached = load_daily_brief()
    if cached and not force:
        return cached
    today = today_key()
    with _brief_lock:
        if _brief_state.get("generating"):
            return dict(_brief_state.get("brief") or {})
        _brief_state.update({
            "date": today, "generating": True, "error": "",
            "attempted": time.time(),
        })
    try:
        prepared = prepare_chat({
            "message": "Give me the brief.", "mode": "brief", "session": "daily",
        })
        if prepared is None:
            raise RuntimeError("could not prepare the brief")
        if not prepared["configured"]:
            raise RuntimeError(prepared["brain"].get("error") or "no brain")
        answer, error = call_llm(prepared["cfg"], prepared["messages"])
        if error or not answer:
            raise RuntimeError(error or "empty brief")
        answer, _spend = extract_spend_request(answer)
        answer = strip_source_citations(answer)
        payload = {
            "date": today,
            "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "answer": answer.strip(),
            "sources": [
                {"id": node["id"], "title": node["title"], "group": node["group"]}
                for node in prepared["notes"]
            ],
            "model": prepared["brain"].get("model") or "",
            "provider": prepared["brain"].get("provider") or "",
        }
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = BRIEF_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(BRIEF_PATH)
        with _brief_lock:
            _brief_state.update({
                "generating": False, "ready": True, "error": "", "brief": payload,
            })
        print("  daily brief: ready", flush=True)
        return payload
    except Exception as exc:  # noqa: BLE001 - the chat path still works
        with _brief_lock:
            _brief_state.update({
                "generating": False, "ready": False,
                "error": str(exc)[:200],
            })
        print(f"! daily brief: {exc}", flush=True)
        return {"date": today, "answer": "", "sources": [],
                "error": str(exc)[:200]}


def refresh_daily_brief() -> dict:
    """Force a rebuild, waiting for an in-flight background build first."""
    with _brief_lock:
        was_generating = bool(_brief_state.get("generating"))
    if was_generating:
        deadline = time.time() + 30.0
        while time.time() < deadline:
            with _brief_lock:
                generating = bool(_brief_state.get("generating"))
                current = dict(_brief_state.get("brief") or {})
            if not generating:
                active_model = str((config().get("llm") or {}).get("model") or "").strip()
                if (current.get("date") == today_key()
                        and str(current.get("answer") or "").strip()
                        and str(current.get("model") or "").strip() == active_model):
                    return current
                break
            time.sleep(0.25)
    return generate_daily_brief(force=True)


def daily_brief_payload() -> dict:
    """Serve the cached brief, starting at most one background generation."""
    cached = load_daily_brief()
    if cached:
        return {"ready": True, "generating": False, "error": "",
                "date": cached.get("date"), "brief": cached}
    with _brief_lock:
        generating = bool(_brief_state.get("generating"))
        error = str(_brief_state.get("error") or "")
        attempted = float(_brief_state.get("attempted") or 0.0)
        date = _brief_state.get("date") or today_key()
    retry = time.time() - attempted > 120.0
    if not generating and (not error or retry):
        threading.Thread(target=generate_daily_brief, daemon=True).start()
        generating = True
    return {"ready": False, "generating": generating, "error": error,
            "date": date, "brief": {}}


def prepare_chat(body: dict) -> dict | None:
    message = str(body.get("message") or "").strip()[:MAX_MESSAGE_CHARS]
    if not message:
        return None
    cfg = config()
    galaxy = galaxy_data()
    mode = str(body.get("mode") or "ask").strip().lower()
    if mode in {"chat", "question", ""}:
        mode = "ask"
    if mode not in CHAT_MODES:
        mode = "ask"
    voice = bool(body.get("voice"))
    direct = direct_answer(message)
    session = clean_session(body.get("session"))
    history = history_for(session)
    work = work_context()
    if mode == "brief":
        notes = brief_notes(galaxy)
        system = build_system(cfg, notes, pending_approvals(), work, mode) + "\n\n" + BRIEF_INSTRUCTIONS
        message = "Give me the brief."
    else:
        notes = retrieve(message, galaxy)
        system = build_system(cfg, notes, pending_approvals(), work, mode)
    if voice and mode != "brief":
        system += "\n\n" + VOICE_CONVERSATION
    messages = (
        [{"role": "system", "content": system}]
        + history
        + [{"role": "user", "content": message}]
    )
    brain = brain_status(cfg)
    return {
        "message": message, "cfg": cfg, "galaxy": galaxy, "notes": notes,
        "session": session, "messages": messages, "brain": brain,
        "configured": bool(brain["ready"]), "mode": mode, "voice": voice,
        "direct": direct if mode != "brief" else None,
    }


def handoff_payload(body: dict) -> tuple[dict, int]:
    """Build a copyable briefing for the official Freebuff client.

    Freebuff's free tier is intentionally client-only. This endpoint does not
    call Freebuff; it packages the same notes, rules and mission context that a
    local answer would use so the operator can paste it into Freebuff itself.
    """
    mode = str(body.get("mode") or "ask").strip().lower()
    if mode in {"chat", "question", ""}:
        mode = "ask"
    if mode not in CHAT_MODES:
        mode = "ask"
    prepared = prepare_chat({
        "message": body.get("message"),
        "session": body.get("session"),
        "mode": mode,
    })
    if prepared is None:
        return {"error": "Ask a question first."}, 400
    notes = prepared.get("notes") or []
    messages = prepared.get("messages") or []
    context = str(messages[0].get("content") or "") if messages else ""
    recent = messages[1:-1]
    lines = [
        "# Helios → Freebuff handoff",
        "",
        "You are receiving a one-shot briefing from Helios, the operator's local assistant.",
        "Answer the question below using only the supplied notes and mission context.",
        "Do not edit files, run commands, browse, spend, send, or publish anything.",
        "If the notes do not cover something, say so plainly. Do not invent facts.",
        "Reply with the answer only; no preamble.",
        "",
        "## Question",
        prepared["message"],
        "",
        "## Mode",
        MODE_LABELS.get(prepared["mode"], prepared["mode"]),
        "",
        "## Helios context",
        context,
    ]
    if recent:
        lines.extend(["", "## Recent Helios conversation"])
        for item in recent[-6:]:
            role = "Operator" if item.get("role") == "user" else "Helios"
            content = str(item.get("content") or "").strip()
            if content:
                lines.append(f"{role}: {content}")
    prompt = "\n".join(lines).strip() + "\n"
    sources = [
        {"id": node.get("id"), "title": node.get("title", ""), "group": node.get("group", "")}
        for node in notes
    ]
    return {
        "ok": True,
        "question": prepared["message"],
        "prompt": prompt,
        "sources": sources,
        "session": prepared["session"],
        "mode": prepared["mode"],
    }, 200


def import_handoff(body: dict) -> tuple[dict, int]:
    """Store a Freebuff answer so local Helios can continue from it."""
    question = str(body.get("question") or body.get("message") or "").strip()[:MAX_MESSAGE_CHARS]
    answer = str(body.get("answer") or "").strip()
    if not question:
        question = "Freebuff handoff"
    if not answer:
        return {"error": "Paste Freebuff's answer first."}, 400
    session = clean_session(body.get("session"))
    remember(session, question, answer[:12000])
    return {
        "ok": True,
        "session": session,
        "chars": len(answer[:12000]),
    }, 200


def offline_answer(galaxy: dict, brain: dict) -> tuple[str, str]:
    stats = galaxy.get("stats") or {}
    loaded = (f"I've loaded your galaxy ({stats.get('notes', 0)} notes, "
              f"{stats.get('links', 0)} links), but ")
    if brain.get("error") == "ollama_model_missing":
        return loaded + (
            f"the model {brain.get('model')} isn't installed yet. "
            f"Run: ollama pull {brain.get('model')} - then ask again. "
            "The model runs on this PC, so no API key is needed."
        ), "ollama_model_missing"
    if brain.get("error") == "ollama_down":
        return loaded + (
            "my local brain isn't answering. Start Ollama from the Start menu "
            "(or run: ollama serve), then ask again. It runs on this PC, so no "
            "API key is needed."
        ), "ollama_down"
    return loaded + (
        "the free local brain is not ready. Start Ollama, then ask again."
    ), brain.get("error") or "no_brain"


def stream_ollama(cfg: dict, messages: list[dict]):
    llm = cfg.get("llm") or {}
    model = str(llm.get("model") or DEFAULT_OLLAMA_MODEL)
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "think": bool(llm.get("think", False)),
        "keep_alive": str(llm.get("keep_alive") or "30m"),
        "options": {
            "temperature": float(llm.get("temperature", 0.3)),
            "num_predict": int(llm.get("max_tokens", 400)),
            "num_ctx": int(llm.get("num_ctx", 8192)),
        },
    }
    request = urllib.request.Request(
        ollama_url(llm) + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "Helios/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(
        request, timeout=float(llm.get("timeout_seconds", 120))
    ) as response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if data.get("error"):
                raise RuntimeError(str(data["error"]))
            piece = (data.get("message") or {}).get("content")
            if piece:
                yield str(piece)
            if data.get("done"):
                break


def stream_llm(cfg: dict, messages: list[dict]):
    provider, llm, key = llm_settings(cfg)
    if provider == "ollama":
        yield from stream_ollama(cfg, messages)
        return
    answer, error = call_openai(cfg, messages, llm, key)
    if answer and not error:
        yield answer
        return
    print(f"! remote brain unavailable ({error}); streaming from local Ollama",
          file=sys.stderr)
    yield from stream_ollama(cfg, messages)


def handle_chat(body: dict) -> tuple[dict, int]:
    prepared = prepare_chat(body)
    if prepared is None:
        return {"answer": "Ask me something about your notes.", "nodes": [],
                "sources": [], "error": "empty message"}, 400
    started = time.time()
    cfg = prepared["cfg"]
    galaxy = prepared["galaxy"]
    direct = prepared.get("direct")
    notes = [] if direct else prepared["notes"]
    sources = [
        {"id": node["id"], "title": node["title"], "group": node["group"]}
        for node in notes
    ]
    trace = {
        "mode": prepared["mode"],
        "session": prepared["session"], "question": prepared["message"][:500],
        "source_ids": [node["id"] for node in notes],
        "model": "local operator" if direct else (prepared["brain"].get("model") or ""),
        "prompt_version": prompt_fingerprint(prepared["messages"]),
    }
    verification = None
    brain = prepared["brain"]
    configured = prepared["configured"]
    answer = None
    error = None
    approval = None
    spend = None

    if direct:
        answer = direct
        error = None
    elif configured:
        answer, error = call_llm(cfg, prepared["messages"])
        if error:
            answer = (
                f"My language model call failed ({error}). Check the configured "
                "brain or start Ollama for local fallback, then ask again."
            )
    else:
        answer, error = offline_answer(galaxy, brain)

    if answer and not error and (direct or configured):
        answer, spend = extract_spend_request(answer)
        answer = operator_answer(answer, spend)
        if spend and spend["amount"] > 0:
            approval = create_approval(spend["title"], spend["amount"], spend["reason"])
        verification = verify_answer(answer, sources, spend, approval)
        if not verification["ok"] and not direct and spend is None:
            repaired, repair_error = call_llm(
                cfg, repair_messages(prepared["messages"], answer, verification))
            if repaired and not repair_error:
                repaired, repaired_spend = extract_spend_request(repaired)
                repaired = operator_answer(repaired, repaired_spend)
                repaired_check = verify_answer(repaired, sources, repaired_spend, approval)
                if repaired_spend is None and repaired_check["ok"]:
                    answer, spend, verification = repaired, repaired_spend, repaired_check
                else:
                    verification = repaired_check
        if not verification["ok"]:
            answer = verification["fallback"]
            verification = verify_answer(answer, sources, spend, approval)
        remember(prepared["session"], prepared["message"], answer)

    final_answer = answer or "I couldn't form an answer from your notes."
    if verification is None:
        verification = verify_answer(final_answer, sources, spend, approval)
    elapsed_ms = round((time.time() - started) * 1000)
    run = record_run(
        "chat",
        story={"knew": ("local operator" if direct else
                         f"{len(sources)} source notes · {prepared['mode']} mode"),
               "did": ("Answered locally from live state" if direct else
                       (f"Answered with {brain['model']}" if not error else "The model call failed")),
               "need": (spend_summary(spend) if spend and approval else "nothing")},
        status="error" if error else ("ok" if verification["ok"] else "warn"),
        **trace, verification=verification, answer=final_answer[:1200],
        approval_id=(approval or {}).get("id") or "", elapsed_ms=elapsed_ms, error=error or "",
    )
    return {
        "answer": final_answer,
        "nodes": [node["id"] for node in notes],
        "sources": sources,
        "configured": True if direct else configured,
        "provider": "local" if direct else brain["provider"],
        "model": "local operator" if direct else brain["model"],
        "mode": prepared["mode"],
        "retrieval": retrieval_state(),
        "error": error,
        "approval": approval,
        "spend": spend_summary(spend) if spend else "",
        "verification": verification,
        "run_id": run["id"],
        "session": prepared["session"],
        "suggestions": chat_suggestions(prepared["mode"], notes),
        "elapsed_ms": elapsed_ms,
    }, 200


def attention_payload(task_data: dict | None = None,
                      mission_data: dict | None = None,
                      approvals: list[dict] | None = None) -> dict:
    """Only spend approvals belong in the operator queue."""
    task_data = task_data if isinstance(task_data, dict) else tasks_payload()
    mission_data = mission_data if isinstance(mission_data, dict) else missions_payload()
    approvals = approvals if isinstance(approvals, list) else load_approvals()
    tasks = task_data.get("tasks") or []
    summary = task_data.get("summary") or {}
    pending = [item for item in approvals if item.get("status") == "pending"]
    blocked = [task for task in tasks if task.get("dependency_state") == "blocked"]
    signoff = [task for task in tasks
               if task.get("status") == "In Test" and not task.get("verified")]
    empty = [mission for mission in mission_data.get("missions") or []
             if mission.get("lifecycle", "active") == "active"
             and not int((mission.get("progress") or {}).get("total") or 0)]
    waiting = int(summary.get("waiting") or 0)
    currency = str((config().get("money") or {}).get("currency") or "GBP")
    items = []
    if pending:
        items.append({
            "key": "approvals", "severity": "urgent",
            "title": f"{len(pending)} spend request{'s' if len(pending) != 1 else ''} waiting",
            "detail": "; ".join(
                f"{item.get('title')} · {float(item.get('amount') or 0):.2f} {item.get('currency') or currency}"
                for item in pending[:3]
            )[:180],
            "count": len(pending),
            "action": {"type": "view", "view": "approvals", "label": "Review queue"},
        })
    return {
        "items": items[:6],
        "summary": {
            "total": len(items),
            "urgent": len(items),
            "pending_approvals": len(pending),
            "blocked_tasks": len(blocked),
            "signoff_tasks": len(signoff),
            "empty_missions": len(empty),
            "waiting_tasks": waiting,
        },
        "generated": task_timestamp(),
    }


def snapshot_signature() -> tuple:
    signature = []
    for path in SNAPSHOT_STATE_PATHS:
        try:
            stat = path.stat()
            signature.append((path.name, stat.st_mtime_ns, stat.st_size))
        except OSError:
            signature.append((path.name, 0, 0))
    return tuple(signature)


def console_snapshot() -> dict:
    """One consistent read for the whole console, reused until state changes.

    The cache key is the modification signature of every file that feeds the
    snapshot. A task move, approval, mission control action or lesson write
    invalidates it automatically; no stale queue can be served after a write.
    """
    with _snapshot_lock:
        started = time.perf_counter()
        signature = snapshot_signature()
        cached = _snapshot_cache.get("payload")
        if cached is not None and _snapshot_cache.get("signature") == signature:
            payload = copy.deepcopy(cached)
            payload["cache"] = "hit"
            payload["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
            return payload
        task_data = tasks_payload()
        mission_data = missions_payload()
        approvals = load_approvals()
        payload = {
            "generated": task_timestamp(),
            "tasks": task_data,
            "missions": mission_data,
            "approvals": {
                "approvals": approvals,
                "pending": sum(1 for item in approvals if item.get("status") == "pending"),
            },
            "attention": attention_payload(task_data, mission_data, approvals),
            "crew": crew_workspace_payload(),
            "activity": activity_payload(),
            "commander": commander_payload(),
            "studio": studio_payload(),
        }
        payload["cache"] = "miss"
        payload["build_ms"] = round((time.perf_counter() - started) * 1000, 1)
        payload["elapsed_ms"] = payload["build_ms"]
        # A read can legitimately write its own derived cache (for example the
        # Commander refreshing its decision log). Key the cache to the state that
        # exists after the build, not the state that existed before it.
        _snapshot_cache.update({"signature": snapshot_signature(),
                                "payload": copy.deepcopy(payload)})
        return payload


# ---------------------------------------------------------------- health ----

def health() -> dict:
    cfg = config()
    galaxy = galaxy_data()
    stats = galaxy.get("stats") or {}
    brain = brain_status(cfg)
    money = cfg.get("money") or {}
    return {
        "ok": True,
        "notes": stats.get("notes", 0),
        "links": stats.get("links", 0),
        "groups": stats.get("groups", 0),
        "generated": galaxy.get("generated"),
        "model": brain["model"],
        "provider": brain["provider"],
        "configured": brain["ready"],
        "brain": brain,
        "voice": cfg.get("voice") or {},
        "money": {
            "can_earn": money.get("can_earn", True),
            "can_spend": money.get("can_spend", False),
            "require_approval": money.get("require_approval", True),
            "currency": money.get("currency", "GBP"),
        },
        "rules": [str(rule) for rule in (cfg.get("rules") or [])],
        "retrieval": retrieval_state(),
        "pending_approvals": len(pending_approvals()),
        "crew": {"roles": len(crew.PERSONAS), "principles": len(crew.PRINCIPLES)},
        "autonomy": autonomy_status(),
    }


# ----------------------------------------------------------------- http -----

class HeliosHandler(SimpleHTTPRequestHandler):
    server_version = "Helios/1.0"
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(VIEWER), **kwargs)

    # -- helpers ------------------------------------------------------------
    def _json(self, payload, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        body = raw
        encoding = ""
        if len(raw) >= 1200 and "gzip" in str(self.headers.get("Accept-Encoding") or "").lower():
            packed = gzip.compress(raw, compresslevel=6)
            if len(packed) < len(raw):
                body = packed
                encoding = "gzip"
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Vary", "Accept-Encoding")
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValueError("bad Content-Length")
        if length <= 0:
            return {}
        if length > 1_000_000:
            raise ValueError("body too large")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(str(exc))
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        return data

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _sse(self, event: str, payload: dict) -> None:
        frame = f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        self.wfile.write(frame.encode("utf-8"))
        self.wfile.flush()

    def stream_chat(self, body: dict) -> None:
        prepared = prepare_chat(body)
        if prepared is None:
            return self._json({"error": "empty message"}, 400)
        started = time.time()
        cfg = prepared["cfg"]
        galaxy = prepared["galaxy"]
        direct = prepared.get("direct")
        notes = [] if direct else prepared["notes"]
        brain = prepared["brain"]
        session = prepared["session"]
        configured = prepared["configured"]
        node_ids = [node["id"] for node in notes]
        sources = [
            {"id": node["id"], "title": node["title"], "group": node["group"]}
            for node in notes
        ]
        trace = {
            "mode": prepared["mode"], "session": session,
            "question": prepared["message"][:500], "source_ids": node_ids,
            "model": "local operator" if direct else brain["model"],
            "prompt_version": prompt_fingerprint(prepared["messages"]),
        }
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True
        try:
            self._sse("meta", {
                "session": session, "nodes": node_ids, "sources": sources,
                "configured": True if direct else configured,
                "provider": "local" if direct else brain["provider"],
                "model": "local operator" if direct else brain["model"],
                "brain": brain, "mode": prepared["mode"],
                "retrieval": retrieval_state(),
            })
            if direct:
                answer = direct
                verification = verify_answer(answer, [])
                remember(session, prepared["message"], answer)
                elapsed_ms = round((time.time() - started) * 1000)
                run = record_run(
                    "chat",
                    story={"knew": f"local operator · {prepared['mode']}",
                           "did": "Answered locally from live state", "need": "nothing"},
                    status="ok", **trace, verification=verification,
                    answer=answer[:1200], elapsed_ms=elapsed_ms, error="",
                )
                self._sse("token", {"text": answer})
                self._sse("done", {
                    "answer": answer, "configured": True, "error": None,
                    "spend": "", "nodes": node_ids, "session": session,
                    "approval": None, "verification": verification,
                    "run_id": run["id"],
                    "suggestions": chat_suggestions(prepared["mode"], notes),
                    "elapsed_ms": elapsed_ms, "mode": prepared["mode"],
                    "model": "local operator", "provider": "local",
                })
                return
            if not configured:
                text, error = offline_answer(galaxy, brain)
                verification = verify_answer(text, sources)
                if not verification["ok"]:
                    text = verification["fallback"]
                elapsed_ms = round((time.time() - started) * 1000)
                run = record_run(
                    "chat",
                    story={"knew": f"{len(sources)} source notes · offline",
                           "did": "Returned the local brain status", "need": "nothing"},
                    status="warn" if not verification["ok"] else "ok",
                    **trace, verification=verification, answer=text[:1200],
                    elapsed_ms=elapsed_ms, error=error or "",
                )
                self._sse("token", {"text": text})
                self._sse("done", {
                    "answer": text, "configured": False, "error": error,
                    "nodes": node_ids, "session": session, "approval": None,
                    "verification": verification, "run_id": run["id"],
                    "suggestions": chat_suggestions(prepared["mode"], notes),
                    "elapsed_ms": elapsed_ms,
                    "mode": prepared["mode"], "model": brain["model"],
                })
                return
            answer = ""
            error = None
            try:
                for piece in stream_llm(cfg, prepared["messages"]):
                    answer += piece
                    self._sse("token", {"text": piece})
            except Exception as exc:  # noqa: BLE001 - report mid-stream failure
                error = str(exc)
            if error:
                verification = verify_answer(answer, sources)
                elapsed_ms = round((time.time() - started) * 1000)
                run = record_run(
                    "chat",
                    story={"knew": f"{len(sources)} source notes · {prepared['mode']}",
                           "did": "The model call failed", "need": "restart the brain"},
                    status="error", **trace, verification=verification,
                    answer=answer[:1200], elapsed_ms=elapsed_ms, error=error,
                )
                self._sse("done", {
                    "answer": answer, "configured": True, "error": error,
                    "nodes": node_ids, "session": session, "approval": None,
                    "verification": verification, "run_id": run["id"],
                    "suggestions": chat_suggestions(prepared["mode"], notes),
                    "elapsed_ms": elapsed_ms,
                    "mode": prepared["mode"], "model": brain["model"],
                })
                return
            answer, spend = extract_spend_request(answer)
            answer = operator_answer(answer, spend)
            approval = None
            if spend and spend["amount"] > 0:
                approval = create_approval(spend["title"], spend["amount"], spend["reason"])
            verification = verify_answer(answer, sources, spend, approval)
            if not verification["ok"] and spend is None:
                repaired, repair_error = call_llm(
                    cfg, repair_messages(prepared["messages"], answer, verification))
                if repaired and not repair_error:
                    repaired, repaired_spend = extract_spend_request(repaired)
                    repaired = operator_answer(repaired, repaired_spend)
                    repaired_check = verify_answer(repaired, sources, repaired_spend, approval)
                    if repaired_spend is None and repaired_check["ok"]:
                        answer, spend, verification = repaired, repaired_spend, repaired_check
                    else:
                        verification = repaired_check
            if not verification["ok"]:
                answer = verification["fallback"]
                verification = verify_answer(answer, sources, spend, approval)
            if answer:
                remember(session, prepared["message"], answer)
            elapsed_ms = round((time.time() - started) * 1000)
            run = record_run(
                "chat",
                story={"knew": f"{len(sources)} source notes · {prepared['mode']}",
                       "did": f"Answered with {brain['model']}",
                       "need": (spend_summary(spend) if spend and approval else "nothing")},
                status="ok" if verification["ok"] else "warn",
                **trace, verification=verification, answer=answer[:1200],
                approval_id=(approval or {}).get("id") or "",
                elapsed_ms=elapsed_ms, error="",
            )
            self._sse("done", {
                "answer": answer, "configured": True, "error": None,
                "spend": spend_summary(spend) if spend else "",
                "nodes": node_ids, "session": session, "approval": approval,
                "verification": verification, "run_id": run["id"],
                "suggestions": chat_suggestions(prepared["mode"], notes),
                "elapsed_ms": elapsed_ms,
                "mode": prepared["mode"], "model": brain["model"],
            })
        except (BrokenPipeError, ConnectionResetError):
            pass

    # -- routes -------------------------------------------------------------
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/snapshot":
            return self._json(console_snapshot())
        if path == "/api/attention":
            return self._json(attention_payload())
        if path == "/api/health":
            return self._json(health())
        if path == "/api/galaxy":
            return self._json(galaxy_data())
        if path == "/api/links/semantic":
            data = semantic_links()
            return self._json({
                "generated": (data.get("key") or (None, None))[0],
                "model": data.get("model") or "",
                "ready": bool(data.get("ready")),
                "count": int(data.get("count") or 0),
                "links": data.get("links") or [],
            })
        if path == "/api/brief":
            return self._json(daily_brief_payload())
        if path == "/api/tasks":
            return self._json(tasks_payload())
        if path == "/api/studio":
            return self._json(studio_payload())
        if path == "/api/harness":
            return self._json(harness_payload())
        if path == "/api/evals":
            return self._json(load_eval_store())
        if path == "/api/runs":
            return self._json(load_run_store())
        if path == "/api/missions/review":
            query = parse_qs(urlparse(self.path).query)
            payload, status = mission_review_payload((query.get("id") or [""])[0])
            return self._json(payload, status)
        if path == "/api/missions":
            return self._json(missions_payload())
        if path == "/api/crew":
            return self._json(crew_workspace_payload())
        if path == "/api/activity":
            query = parse_qs(urlparse(self.path).query)
            return self._json(activity_payload(
                mission_id=(query.get("mission_id") or [""])[0]))
        if path == "/api/autonomy":
            return self._json(autonomy_status())
        if path == "/api/commander":
            return self._json(commander_payload())
        if path == "/api/approvals":
            items = load_approvals()
            return self._json({
                "approvals": items,
                "pending": sum(1 for item in items if item.get("status") == "pending"),
            })
        return super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            body = self._read_json()
        except ValueError as exc:
            return self._json({"error": f"bad request: {exc}"}, 400)

        if path == "/api/tasks":
            payload, status = create_task(body)
            return self._json(payload, status)

        if path == "/api/tasks/update":
            payload, status = update_task(body)
            return self._json(payload, status)

        if path == "/api/tasks/verify":
            payload, status = verify_task(body)
            return self._json(payload, status)

        if path == "/api/tasks/seed":
            payload, status = seed_roadmap()
            return self._json(payload, status)

        if path == "/api/missions/control":
            payload, status = control_mission(body)
            return self._json(payload, status)

        if path == "/api/missions/review":
            payload, status = record_mission_decision(body)
            return self._json(payload, status)

        if path == "/api/studio/cycle":
            return self._json(run_studio_cycle())

        if path == "/api/studio/evals":
            return self._json(run_evals())

        if path == "/api/studio/policy":
            payload, status = update_action_policy(body)
            return self._json(payload, status)

        if path == "/api/studio/learn":
            payload, status = capture_learning(body)
            return self._json(payload, status)

        if path == "/api/studio/idea/accept":
            payload, status = accept_idea(body)
            return self._json(payload, status)

        if path == "/api/studio/idea/decide":
            payload, status = decide_idea(body)
            return self._json(payload, status)

        if path == "/api/missions":
            payload, status = create_mission(body)
            return self._json(payload, status)

        if path == "/api/crew/brief":
            payload, status = apply_crew_brief(body)
            return self._json(payload, status)

        if path == "/api/crew/route":
            return self._json(run_autonomy_pulse(brief_decision=False))

        if path == "/api/pulse":
            return self._json(run_autonomy_pulse(brief_decision=True))

        if path == "/api/commander/cycle":
            payload = commander_payload(force=True, narrate=True)
            status = 202 if payload.get("generating") else 200
            return self._json(payload, status)

        if path == "/api/commander/apply":
            payload, status = apply_commander_briefing(body)
            return self._json(payload, status)

        if path == "/api/handoff":
            payload, status = handoff_payload(body)
            return self._json(payload, status)

        if path == "/api/handoff/import":
            payload, status = import_handoff(body)
            return self._json(payload, status)

        if path == "/api/brief/refresh":
            payload = refresh_daily_brief()
            ready = bool(str(payload.get("answer") or "").strip())
            return self._json({
                "ready": ready,
                "generating": False,
                "error": payload.get("error") or ("" if ready else "brief generation failed"),
                "date": payload.get("date"),
                "brief": payload if ready else {},
            })

        if path == "/api/chat/stream":
            return self.stream_chat(body)

        if path == "/api/chat":
            payload, status = handle_chat(body)
            return self._json(payload, status)

        if path == "/api/approvals":
            title = str(body.get("title") or "").strip()
            if not title:
                return self._json({"error": "title required"}, 400)
            try:
                amount = float(body.get("amount") or 0)
            except (TypeError, ValueError):
                amount = 0.0
            item = create_approval(title, amount, str(body.get("reason") or ""))
            return self._json({"approval": item}, 201)

        if path == "/api/approvals/decide":
            approval_id = str(body.get("id") or "").strip()
            decision = str(body.get("decision") or "").strip().lower()
            if decision not in {"approve", "deny"}:
                return self._json({"error": "decision must be approve or deny"}, 400)
            item = decide_approval(approval_id, decision)
            if item is None:
                return self._json({"error": "approval not found"}, 404)
            return self._json({"approval": item})

        return self._json({"error": "not found"}, 404)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="Serve the Helios viewer on port 4700.")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    cfg = config()
    port = args.port or int((cfg.get("server") or {}).get("port") or DEFAULT_PORT)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not (VIEWER / "index.html").exists():
        print("! viewer/index.html missing \u2014 run: python build.py", file=sys.stderr)
    if not GALAXY_PATH.exists():
        print("! viewer/galaxy.json missing \u2014 run: python build.py", file=sys.stderr)

    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), HeliosHandler)
    except OSError as exc:
        print(f"! cannot bind 127.0.0.1:{port}: {exc}", file=sys.stderr)
        return 1
    httpd.daemon_threads = True

    stats = (galaxy_data().get("stats") or {})
    print("+ Helios online -> http://localhost:%d" % port, flush=True)
    print(f"  serving : {VIEWER}", flush=True)
    print(f"  galaxy  : {stats.get('notes', 0)} notes / {stats.get('links', 0)} links", flush=True)
    brain = brain_status(cfg)
    state = "ready" if brain["ready"] else (brain["error"] or "not ready")
    print(f"  brain   : {brain['provider']} / {brain['model']} ({state})", flush=True)
    print("  spend   : blocked - approvals queue only\n", flush=True)
    threading.Thread(target=embedding_keeper, daemon=True).start()
    threading.Thread(target=commander_keeper, daemon=True).start()
    threading.Thread(target=pulse_keeper, daemon=True).start()
    threading.Thread(target=console_snapshot, daemon=True).start()

    def warm_daily_brief() -> None:
        time.sleep(12)
        generate_daily_brief()

    threading.Thread(target=warm_daily_brief, daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nHelios sleeping.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
