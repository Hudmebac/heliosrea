#!/usr/bin/env python3
"""Improve plans: the smallest reversible change after an approved dossier.

The Improve phase records what a human intends to change, why, the baseline,
the verification and the rollback before anything is touched. A baselined plan
with a unified diff may be executed by ``executor.py`` against a clean local git
work tree; that path is explicit, verifiable and reversible. A plan is local JSON
under ``rea/data/`` and is deliberately separate from the evidence dossier.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

import engine
import studio

STORE_PATH = Path(__file__).resolve().parent / "data" / "improvements.json"

# The same Improve discipline applies to any system. These classes keep the
# method honest: a television, a car and an alarm panel are not the same risk.
SYSTEM_CLASSES = {
    "software": {
        "label": "Software / desktop / server",
        "hazard": "standard",
        "blurb": "An app, service, repository or operating system you can inspect, rebuild and run again.",
        "examples": ("desktop app", "local service", "source tree", "server image"),
        "improve": ("Remove dead features and unused dependencies.",
                    "Replace a confusing interface with a local one.",
                    "Add a read-only dashboard or automation."),
        "evidence": ("Installed version and build.", "Current config and data location.",
                     "A repeatable start, stop and check command."),
        "stop": ("Credential or licence bypass.",
                 "A destructive migration without a tested backup.",
                 "Changing a production service without a rollback window."),
        "recovery": "Keep the previous build, installer or source revision and a tested restore command.",
        "allowed": ("observe", "configure", "debloat", "interface", "extend", "replace"),
    },
    "mobile": {
        "label": "Phone / tablet",
        "hazard": "guarded",
        "blurb": "A mobile OS or app on a device you own and can restore.",
        "examples": ("Android app", "iPhone app", "owner-managed tablet"),
        "improve": ("Remove unused apps and duplicate workflows.",
                    "Replace a home-screen workflow with an owner-controlled one.",
                    "Add a local export or read-only dashboard."),
        "evidence": ("OS build and app version.", "Developer-mode and backup state.",
                     "A tested restore or reinstall path."),
        "stop": ("Lock, activation or DRM bypass.",
                 "A corporate or someone else's device.",
                 "Destroying the only recovery path."),
        "recovery": "Take a full backup and rehearse the restore before any system-level change.",
        "allowed": ("observe", "configure", "debloat", "interface", "extend", "replace"),
    },
    "web": {
        "label": "Website / SaaS / API",
        "hazard": "standard",
        "blurb": "A public or internal web surface, service or API with an owner and a release process.",
        "examples": ("website", "SaaS tenant", "HTTP API", "captured user journey"),
        "improve": ("Remove unused scripts, endpoints and third-party calls.",
                    "Replace a noisy front end with an accessible local view.",
                    "Add an export or read-only status page."),
        "evidence": ("Release version and route inventory.", "Current data flow and retention.",
                     "A staging copy or reversible release."),
        "stop": ("Authentication bypass or private-data scraping.",
                 "Publishing without the data owner's approval.",
                 "A schema change with no tested backup."),
        "recovery": "Keep the previous release and a verified database backup; migrate only behind a rollback window.",
        "allowed": ("observe", "configure", "debloat", "interface", "extend", "replace"),
    },
    "streaming": {
        "label": "TV / streaming device",
        "hazard": "guarded",
        "blurb": "A TV, streaming stick or set-top box you own, including its launcher and owner-facing apps.",
        "examples": ("Fire TV", "Android TV", "Apple TV", "Roku", "set-top box"),
        "improve": ("Disable unused apps, autoplay surfaces and duplicate recommendations.",
                    "Replace the default launcher with an owner-controlled front end where the supported developer path allows it.",
                    "Add a local remote, status view or input shortcut."),
        "evidence": ("Model, OS build and installed-app inventory.",
                     "Stock launcher, remote and factory-reset path.",
                     "DRM, account and service constraints."),
        "stop": ("DRM, paywall or service-term bypass.",
                 "Losing the stock launcher, remote or recovery path.",
                 "Changing a device you do not own."),
        "recovery": "Keep the stock launcher, remote, account access and a tested factory-reset path.",
        "allowed": ("observe", "configure", "debloat", "interface", "extend", "replace"),
    },
    "vehicle": {
        "label": "Car / vehicle",
        "hazard": "safety",
        "blurb": "Owner-facing infotainment, telemetry or diagnostic surfaces. Never steering, braking or safety systems.",
        "examples": ("infotainment head unit", "OBD export", "vehicle app", "service-record dataset"),
        "improve": ("Simplify the infotainment UI within vendor-supported interfaces.",
                    "Add a read-only trip or battery dashboard.",
                    "Remove unused owner-facing apps where the system supports it."),
        "evidence": ("Asset ID kept private, head-unit model and software version.",
                     "Diagnostic export and a known-good baseline.",
                     "Written owner permission and a recovery route."),
        "stop": ("Steering, braking, airbags, immobiliser, ADAS, emissions or drive-by-wire.",
                 "Safety-critical control changes.",
                 "A change that voids warranty or inspection compliance without owner sign-off."),
        "recovery": "Keep the vendor restore image or dealer recovery route and a known-good diagnostic baseline.",
        "allowed": ("observe", "configure", "interface", "extend"),
    },
    "appliance": {
        "label": "Appliance / white goods",
        "hazard": "safety",
        "blurb": "Owner-facing controls and telemetry for a washing machine, fridge, HVAC unit or similar. Not the safety loop.",
        "examples": ("washing machine", "fridge", "HVAC controller", "dishwasher"),
        "improve": ("Expose a local read-only status view.",
                    "Change documented eco or comfort settings.",
                    "Remove an unused cloud dependency only when local operation remains."),
        "evidence": ("Model and firmware version.", "Current settings and a known-good cycle.",
                     "Manufacturer service and reset path."),
        "stop": ("Lid locks, heating limits, gas or water valves, thermal fuses, defrost limits or safety interlocks.",
                 "A local fallback that stops working.",
                 "Warranty or insurance conditions."),
        "recovery": "Record the stock settings and service procedure; keep the manufacturer reset path.",
        "allowed": ("observe", "configure", "extend"),
    },
    "alarm": {
        "label": "Alarm / access control",
        "hazard": "safety",
        "blurb": "Configuration and read-only status for alarm, CCTV and access systems. Never monitoring or life-safety functions.",
        "examples": ("alarm panel", "CCTV recorder", "door access controller", "monitoring link"),
        "improve": ("Export event history for an owner review.",
                    "Remove duplicate notifications where the panel supports it.",
                    "Add a local read-only dashboard."),
        "evidence": ("Panel model and firmware.", "Zone and signalling map, with private values redacted.",
                     "Monitoring-station contract and test window.",
                     "Owner and installer authorisation."),
        "stop": ("Disabling monitoring, sensors, sirens, signalling, tamper detection, locks or life-safety functions.",
                 "Testing without the monitoring station or owner present.",
                 "Exposing alarm credentials or private camera data."),
        "recovery": "Installer-backed configuration backup and a monitoring-station test before and after.",
        "allowed": ("observe", "configure", "extend"),
    },
    "smart": {
        "label": "Smart home / IoT",
        "hazard": "guarded",
        "blurb": "Hubs, cameras, speakers, thermostats and local devices you own and can recover.",
        "examples": ("smart hub", "camera", "speaker", "thermostat", "sensor bridge"),
        "improve": ("Move a device to local control where the vendor supports it.",
                    "Remove unused cloud integrations.",
                    "Replace a cloud-only dashboard with a local read-only view."),
        "evidence": ("Device model, firmware and local protocol.",
                     "Account ownership and privacy settings.", "Automation inventory and fallback."),
        "stop": ("Smoke or CO detection, security sensors, door locks, local fallback or update signalling.",
                 "Disabling safety alerts.",
                 "Exposing camera or presence data."),
        "recovery": "Keep the vendor app, account recovery and a local fallback before changing automations.",
        "allowed": ("observe", "configure", "debloat", "interface", "extend", "replace"),
    },
    "network": {
        "label": "Network / router / NAS",
        "hazard": "guarded",
        "blurb": "A router, access point, firewall or storage appliance with an owner and a recovery path.",
        "examples": ("router", "access point", "firewall", "NAS"),
        "improve": ("Remove unused services and default accounts.",
                    "Replace the admin page with a local read-only view.",
                    "Add a local status or backup dashboard."),
        "evidence": ("Model, firmware and current configuration export.",
                     "Management network and out-of-band access.", "Update and recovery procedure."),
        "stop": ("Exposing management to the internet.",
                 "Disabling firewall, update or logging controls.",
                 "Bypassing ISP or service terms."),
        "recovery": "Export the configuration and confirm out-of-band access before any firmware or UI change.",
        "allowed": ("observe", "configure", "debloat", "interface", "extend", "replace"),
    },
    "embedded": {
        "label": "Embedded / firmware",
        "hazard": "guarded",
        "blurb": "Microcontrollers, firmware images and serviceable devices. Stop at the safety boundary.",
        "examples": ("firmware image", "microcontroller board", "industrial controller", "medical service device"),
        "improve": ("Map the container and runtime.",
                    "Change documented configuration.",
                    "Add read-only telemetry or a local status view."),
        "evidence": ("Board, firmware and container version.", "Partition and rootfs map.",
                     "Recovery image, serial access and safety classification."),
        "stop": ("Safety interlocks, medical or industrial control loops.",
                 "No verified recovery image.",
                 "Signed-boot bypass or anti-forensics."),
        "recovery": "Do not write without a verified recovery image and physical recovery access.",
        "allowed": ("observe", "configure", "interface", "extend", "replace"),
    },
}

INTERVENTIONS = (
    {"id": "observe", "label": "Observe",
     "blurb": "Read-only evidence and a baseline. No change is made.",
     "requires": ("Authorised read-only access", "A repeatable measurement"),
     "stop": ("Any step that needs a change, credential or bypass",)},
    {"id": "configure", "label": "Configure",
     "blurb": "Use documented settings to make the system simpler or more local.",
     "requires": ("Owner access", "Exported current settings", "A tested reset"),
     "stop": ("Hidden or undocumented settings",
              "Disabling update or safety controls")},
    {"id": "debloat", "label": "Debloat",
     "blurb": "Remove or disable non-essential apps, services and defaults.",
     "requires": ("Complete inventory", "Dependency check", "Restore image or reinstall list"),
     "stop": ("Boot, update, recovery, accessibility, security or safety functions",)},
    {"id": "interface", "label": "Replace / add interface",
     "blurb": "Put an owner-controlled launcher, dashboard or front end in front of the system.",
     "requires": ("Supported extension or developer path", "Stock interface and recovery kept",
                  "Input and remote fallback tested"),
     "stop": ("DRM or paywall bypass", "No way back to the stock interface",
              "Replacing a safety interface")},
    {"id": "extend", "label": "Extend",
     "blurb": "Add a local integration, automation or read-only control surface.",
     "requires": ("Documented API or protocol", "Least-privilege access", "No safety actuator"),
     "stop": ("Controlling a safety function", "Exposing private data",
              "An undocumented cloud dependency")},
    {"id": "replace", "label": "Replace runtime",
     "blurb": "Replace the OS, firmware or runtime with a supported or recoverable image.",
     "requires": ("Verified recovery image", "Power and physical access",
                  "Full backup and rollback rehearsal"),
     "stop": ("No recovery path", "Signed boot blocks the image",
              "A safety-critical control loop")},
)

HAZARD_LABEL = {"standard": "standard", "guarded": "guarded", "safety": "safety-critical"}
INTERVENTIONS_BY_ID = {item["id"]: item for item in INTERVENTIONS}

LEVEL_KEYWORDS = {
    "observe": ("see", "understand", "inspect", "audit", "map", "monitor", "status", "baseline"),
    "configure": ("config", "setting", "privacy", "telemetry", "account", "local", "disable", "simplify", "clean"),
    "debloat": ("bloat", "remove", "unused", "delete", "clean", "faster", "speed", "lean", "unnecessary"),
    "interface": ("interface", "ui", "launcher", "home screen", "dashboard", "front end", "design", "remote", "look"),
    "extend": ("add", "extend", "integrat", "automation", "api", "export", "remote", "monitor", "dashboard"),
    "replace": ("replace", "upgrade", "firmware", "os", "runtime", "migrate", "port"),
}
LEVEL_BASE_SCORE = {
    "observe": 0.15, "configure": 0.30, "debloat": 0.22,
    "interface": 0.24, "extend": 0.20, "replace": 0.10,
}
RISK_PENALTY = {"low": 0.0, "medium": 0.04, "high": 0.12, "blocked": 1.0}
CATEGORY_LABEL = {
    "source": "source", "config": "configuration", "protocol": "protocol",
    "binary": "binary", "edge": "edge content", "firmware": "firmware",
}

STATUSES = ("draft", "approved", "baselined", "changed", "verified", "rolled_back")
STATUS_LABEL = {
    "draft": "draft",
    "approved": "approved",
    "baselined": "baseline recorded",
    "changed": "change recorded",
    "verified": "verified",
    "rolled_back": "rolled back",
}

TRANSITIONS = {
    "draft": ("approved",),
    "approved": ("baselined", "rolled_back"),
    "baselined": ("changed", "rolled_back"),
    "changed": ("verified", "rolled_back"),
    "verified": ("rolled_back",),
    "rolled_back": (),
}

GUARDRAILS = (
    "One seam, one change. Do not widen the scope while testing.",
    "Record the baseline before the change. No baseline means no approved change.",
    "The rollback must be concrete and usable before the change is made.",
    "A baselined plan may be applied locally with git apply; verification failure reverses the patch automatically.",
    "Stop if the change needs a new permission, credential, bypass or hidden access.",
)

TEXT_LIMITS = {
    "goal": 500,
    "change": 800,
    "baseline": 500,
    "verification": 500,
    "rollback": 500,
    "patch": 20000,
    "verify_command": 400,
    "owner": 80,
    "note": 400,
    "brief": 500,
}

LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _clean(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return engine.redact(text)[:limit]


def _clean_block(value: object, limit: int) -> str:
    """Keep the line structure of a patch or command block."""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.strip(chr(10))
    text = engine.redact(text)[:limit]
    # A unified diff needs its terminating newline or git calls it corrupt.
    return text + "\n" if text else ""


def _store(path: Path | None = None) -> Path:
    return Path(path) if path else Path(STORE_PATH)


def load(path: Path | None = None) -> dict:
    target = _store(path)
    with LOCK:
        if not target.is_file():
            return {"version": 1, "plans": [], "suggestions": []}
        try:
            data = json.loads(target.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "plans": [], "suggestions": []}
    if not isinstance(data, dict):
        return {"version": 1, "plans": [], "suggestions": []}
    if not isinstance(data.get("plans"), list):
        data["plans"] = []
    if not isinstance(data.get("suggestions"), list):
        data["suggestions"] = []
    data.setdefault("version", 1)
    return data


def save(data: dict, path: Path | None = None) -> None:
    target = _store(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=1)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with LOCK:
        tmp.write_text(payload, "utf-8")
        tmp.replace(target)


def _require(value: object, label: str, limit: int) -> str:
    text = _clean(value, limit)
    if not text:
        raise ValueError(f"{label} is required")
    return text


def _system(value: object) -> tuple[str, dict]:
    key = str(value or "").strip()
    if not key:
        raise ValueError("choose a system class")
    if key not in SYSTEM_CLASSES:
        raise ValueError("unknown system class")
    return key, SYSTEM_CLASSES[key]


def _intervention(value: object) -> tuple[str, dict]:
    key = str(value or "").strip()
    if not key:
        raise ValueError("choose a change level")
    if key not in INTERVENTIONS_BY_ID:
        raise ValueError("unknown change level")
    return key, INTERVENTIONS_BY_ID[key]


def _public_system(system_id: str, system: dict) -> dict:
    return {"id": system_id, **{
        key: list(value) if isinstance(value, tuple) else value
        for key, value in system.items()
    }}


def _public_intervention(level: dict) -> dict:
    return {**level, "requires": list(level["requires"]), "stop": list(level["stop"])}


def _dossier(dossier_id: object) -> dict:
    inv = engine.load(str(dossier_id or ""))
    if not inv:
        raise KeyError("investigation not found")
    if inv.get("authorized") is not True:
        raise ValueError("the dossier is not authorised")
    if str(inv.get("status") or "") != "complete":
        raise ValueError("the dossier must be complete before it can be improved")
    if studio.is_fixture_target(inv.get("target")):
        raise ValueError("fixture, demo and example targets cannot be improved")
    return inv


def _seam(inv: dict, seam_id: object) -> dict:
    wanted = str(seam_id or "").strip()
    if not wanted:
        raise ValueError("seam is required")
    for edit in inv.get("edits") or []:
        if isinstance(edit, dict) and str(edit.get("id") or "") == wanted:
            return edit
    raise KeyError("seam not found in this dossier")


def _seam_record(edit: dict) -> dict:
    return {
        "id": str(edit.get("id") or ""),
        "category": str(edit.get("category") or ""),
        "locator": _clean(edit.get("locator"), 300),
        "action": _clean(edit.get("action"), 600),
        "risk": str(edit.get("risk") or ""),
        "confidence": edit.get("confidence") if isinstance(edit.get("confidence"), (int, float)) else 0,
        "requires": [str(item)[:120] for item in (edit.get("requires") or []) if str(item).strip()],
        "note": _clean(edit.get("note"), 400),
        "probe": str(edit.get("probe") or ""),
        "evidence": [str(item)[:80] for item in (edit.get("evidence") or []) if str(item).strip()],
    }


def _keyword_match(text: str, tokens: set[str], word: str) -> bool:
    if " " in word or len(word) > 4:
        return word in text
    return word in tokens


def _dedupe(items) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items or []:
        text = _clean(item, 240)
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def _suggestion_title(level_id: str, seam: dict) -> str:
    label = INTERVENTIONS_BY_ID[level_id]["label"]
    return _clean(f"{label} · {seam.get('locator') or 'mapped seam'}", 150)


def _suggestion_change(level_id: str, seam: dict) -> str:
    label = CATEGORY_LABEL.get(str(seam.get("category") or ""), str(seam.get("category") or "mapped"))
    locator = seam.get("locator") or "the mapped seam"
    action = seam.get("action") or "".strip()
    prefix = {
        "observe": f"Make no change. Record the current {label} behaviour at {locator} and keep it as the baseline.",
        "configure": f"Change only the documented {label} settings at {locator}. Leave hidden, security and safety controls alone.",
        "debloat": f"Remove or disable only the non-essential {label} weight at {locator}. Do not touch boot, update, recovery, accessibility, security or safety functions.",
        "interface": f"Replace or add only the owner-facing {label} interface at {locator}. Keep the stock interface and a tested way back.",
        "extend": f"Add a local read-only {label} view or integration at {locator}. Do not add a safety actuator or a new cloud dependency.",
        "replace": f"Replace only the {label} runtime at {locator} with a verified, recoverable image. Keep the previous image and a tested restore.",
    }[level_id]
    return _clean(f"{prefix} The mapped action is: {action}", TEXT_LIMITS["change"])


def _suggestion_baseline(seam: dict) -> str:
    probe = seam.get("probe") or "the mapping probe"
    return _clean(
        f"Record the current result at {seam.get('locator') or 'the mapped seam'}. "
        f"Re-run {probe} and save the output, screenshot or hash before changing anything.",
        TEXT_LIMITS["baseline"],
    )


def _suggestion_verification(seam: dict, goal: str) -> str:
    probe = seam.get("probe") or "the same check"
    return _clean(
        f"Re-run {probe} and compare with the baseline. State whether this goal is met: {goal}. "
        "If it is not, use the rollback.",
        TEXT_LIMITS["verification"],
    )


def _suggestion_rollback(system: dict, seam: dict) -> str:
    return _clean(
        f"{system.get('recovery') or 'Keep a tested recovery path.'} "
        f"Restore {seam.get('locator') or 'the mapped seam'} from the baseline copy or reinstall the previous version.",
        TEXT_LIMITS["rollback"],
    )


def suggest(dossier_id: object, goal: object, system_class: object,
            limit: object = 4, path: Path | None = None) -> dict:
    """Suggest evidence-backed improvements from one dossier and a plain brief."""
    inv = _dossier(dossier_id)
    system_id, system = _system(system_class)
    brief = _require(goal, "improvement brief", TEXT_LIMITS["brief"])
    try:
        max_items = max(1, min(6, int(limit or 4)))
    except (TypeError, ValueError):
        max_items = 4
    now = _now()
    candidates: list[dict] = []
    goal_text = brief.lower()
    goal_tokens = set(re.findall(r"[a-z0-9]+", goal_text))
    for edit in inv.get("edits") or []:
        if not isinstance(edit, dict):
            continue
        seam = _seam_record(edit)
        if not seam["id"] or seam["risk"] == "blocked":
            continue
        scored: list[tuple[float, str, dict]] = []
        for level_id in system["allowed"]:
            level = INTERVENTIONS_BY_ID[level_id]
            matches = sum(1 for word in LEVEL_KEYWORDS.get(level_id, ())
                          if _keyword_match(goal_text, goal_tokens, word))
            score = (float(seam.get("confidence") or 0) * 0.7 +
                     0.18 * matches + LEVEL_BASE_SCORE.get(level_id, 0) -
                     RISK_PENALTY.get(seam.get("risk"), 0.05))
            scored.append((score, level_id, level))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        for score, level_id, level in scored[:2]:
            candidates.append({
                "id": f"SUG-{datetime.now(tz=timezone.utc).strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3).upper()}",
                "status": "suggested",
                "created_at": now,
                "updated_at": now,
                "dossier_id": str(inv.get("id") or ""),
                "target": _clean(inv.get("target"), 500),
                "dossier_verdict": _clean((inv.get("summary") or {}).get("verdict"), 500),
                "system_class": system_id,
                "system_label": system["label"],
                "system_hazard": system["hazard"],
                "system_stop": list(system["stop"]),
                "intervention": level_id,
                "intervention_label": level["label"],
                "intervention_requires": list(level["requires"]),
                "intervention_stop": list(level["stop"]),
                "seam": seam,
                "goal": brief,
                "title": _suggestion_title(level_id, seam),
                "why": _clean(
                    f"The dossier mapped {seam.get('action')} at {seam.get('locator')} with "
                    f"{round((float(seam.get('confidence') or 0)) * 100)}% confidence. "
                    f"{level['label']} is allowed for {system['label']} and matches the brief.",
                    600,
                ),
                "change": _suggestion_change(level_id, seam),
                "baseline": _suggestion_baseline(seam),
                "verification": _suggestion_verification(seam, brief),
                "rollback": _suggestion_rollback(system, seam),
                "patch": "",
                "requires": _dedupe([*(seam.get("requires") or []), *(level["requires"])]),
                "stop": _dedupe([*(system["stop"]), *(level["stop"])]),
                "risk": seam["risk"],
                "confidence": seam["confidence"],
                "plan_id": "",
                "_score": score,
            })
    candidates.sort(key=lambda item: (item["_score"], item.get("confidence") or 0), reverse=True)
    selected = candidates[:max_items]
    for item in selected:
        item.pop("_score", None)
    with LOCK:
        data = load(path)
        for old in data.get("suggestions") or []:
            if (isinstance(old, dict) and old.get("status") == "suggested"
                    and str(old.get("dossier_id") or "") == str(inv.get("id") or "")):
                old["status"] = "superseded"
                old["updated_at"] = now
        data["suggestions"].extend(selected)
        save(data, path)
    note = ("No unblocked seam in this dossier could support a suggestion. "
            "Record more evidence or choose another dossier." if not selected else
            f"{len(selected)} evidence-backed suggestion(s) from {len(inv.get('edits') or [])} mapped seam(s).")
    return {
        "goal": brief,
        "system": {"id": system_id, "label": system["label"], "hazard": system["hazard"]},
        "suggestions": selected,
        "note": note,
    }


def get_suggestion(suggestion_id: object, path: Path | None = None) -> dict | None:
    wanted = str(suggestion_id or "").strip()
    if not wanted:
        return None
    for suggestion in load(path).get("suggestions") or []:
        if isinstance(suggestion, dict) and suggestion.get("id") == wanted:
            return suggestion
    return None


def decline_suggestion(suggestion_id: object, path: Path | None = None) -> dict:
    with LOCK:
        data = load(path)
        suggestion = next((item for item in data.get("suggestions") or []
                           if isinstance(item, dict) and item.get("id") == str(suggestion_id or "")), None)
        if suggestion is None:
            raise KeyError("suggestion not found")
        if suggestion.get("status") != "suggested":
            raise ValueError("only a suggested improvement can be declined")
        suggestion["status"] = "declined"
        suggestion["updated_at"] = _now()
        save(data, path)
    return suggestion


def approve_suggestion(suggestion_id: object, owner: object, goal: object = "",
                       change: object = "", baseline: object = "", verification: object = "",
                       rollback: object = "", patch: object = "", verify_command: object = "",
                       path: Path | None = None) -> tuple[dict, dict]:
    with LOCK:
        data = load(path)
        suggestion = next((item for item in data.get("suggestions") or []
                           if isinstance(item, dict) and item.get("id") == str(suggestion_id or "")), None)
        if suggestion is None:
            raise KeyError("suggestion not found")
        if suggestion.get("status") != "suggested":
            raise ValueError("this suggestion has already been decided")
        seam = suggestion.get("seam") or {}
        plan = create(
            suggestion.get("dossier_id"), seam.get("id"),
            goal or suggestion.get("goal"),
            change or suggestion.get("change"),
            baseline or suggestion.get("baseline"),
            verification or suggestion.get("verification"),
            rollback or suggestion.get("rollback"),
            patch or suggestion.get("patch") or "",
            owner,
            suggestion.get("system_class"),
            suggestion.get("intervention"),
            suggestion_id=suggestion.get("id"),
            verify_command=verify_command,
            path=path,
        )
        data = load(path)
        suggestion = next(item for item in data.get("suggestions") or []
                          if isinstance(item, dict) and item.get("id") == str(suggestion_id or ""))
        suggestion["status"] = "approved"
        suggestion["updated_at"] = _now()
        suggestion["plan_id"] = plan["id"]
        save(data, path)
    return plan, suggestion


def create(dossier_id: object, seam_id: object, goal: object, change: object,
           baseline: object, verification: object, rollback: object,
           patch: object = "", owner: object = "", system_class: object = "",
           intervention: object = "", suggestion_id: object = "",
           verify_command: object = "", path: Path | None = None) -> dict:
    """Create one draft plan. This records intent; applying it is a separate step."""
    inv = _dossier(dossier_id)
    seam = _seam(inv, seam_id)
    system_id, system = _system(system_class)
    level_id, level = _intervention(intervention)
    if level_id not in system["allowed"]:
        allowed = ", ".join(INTERVENTIONS_BY_ID[item]["label"] for item in system["allowed"])
        raise ValueError(f"{level['label']} is not allowed for {system['label']}; choose {allowed}")
    now = _now()
    plan = {
        "id": f"IMP-{datetime.now(tz=timezone.utc).strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3).upper()}",
        "status": "draft",
        "created_at": now,
        "updated_at": now,
        "dossier_id": str(inv.get("id") or ""),
        "target": _clean(inv.get("target"), 500),
        "dossier_verdict": _clean((inv.get("summary") or {}).get("verdict"), 500),
        "seam": _seam_record(seam),
        "system_class": system_id,
        "system_label": system["label"],
        "system_hazard": system["hazard"],
        "system_stop": list(system["stop"]),
        "intervention": level_id,
        "intervention_label": level["label"],
        "intervention_requires": list(level["requires"]),
        "intervention_stop": list(level["stop"]),
        "suggestion_id": str(suggestion_id or ""),
        "owner": _require(owner, "owner / approver", TEXT_LIMITS["owner"]),
        "goal": _require(goal, "what better means", TEXT_LIMITS["goal"]),
        "change": _require(change, "smallest change", TEXT_LIMITS["change"]),
        "baseline": _require(baseline, "baseline", TEXT_LIMITS["baseline"]),
        "verification": _require(verification, "verification", TEXT_LIMITS["verification"]),
        "rollback": _require(rollback, "rollback", TEXT_LIMITS["rollback"]),
        "patch": _clean_block(patch, TEXT_LIMITS["patch"]),
        "verify_command": _clean(verify_command, TEXT_LIMITS["verify_command"]),
        "history": [{
            "status": "draft",
            "at": now,
            "note": f"Plan drafted from seam {seam.get('id') or ''}. No target change recorded.",
        }],
    }
    with LOCK:
        data = load(path)
        data["plans"].append(plan)
        save(data, path)
    return plan


def get(plan_id: object, path: Path | None = None) -> dict | None:
    wanted = str(plan_id or "").strip()
    if not wanted:
        return None
    for plan in load(path).get("plans") or []:
        if isinstance(plan, dict) and plan.get("id") == wanted:
            return plan
    return None


def set_status(plan_id: object, status: object, note: object = "",
               path: Path | None = None) -> dict:
    wanted = str(status or "").strip()
    if wanted not in STATUSES:
        raise ValueError("unknown plan status")
    with LOCK:
        data = load(path)
        plan = next((item for item in data.get("plans") or []
                     if isinstance(item, dict) and item.get("id") == str(plan_id or "")), None)
        if plan is None:
            raise KeyError("plan not found")
        current = str(plan.get("status") or "draft")
        if wanted not in TRANSITIONS.get(current, ()):
            raise ValueError(f"cannot move a {current} plan to {wanted}")
        now = _now()
        plan["status"] = wanted
        plan["updated_at"] = now
        plan.setdefault("history", []).append({
            "status": wanted,
            "at": now,
            "note": _clean(note, TEXT_LIMITS["note"]),
        })
        save(data, path)
    return plan


def record_execution(plan_id: object, record: dict, status: object = "",
                     note: object = "", path: Path | None = None) -> dict:
    """Store the executor's audit record and, optionally, the next plan state."""
    with LOCK:
        data = load(path)
        plan = next((item for item in data.get("plans") or []
                     if isinstance(item, dict) and item.get("id") == str(plan_id or "")), None)
        if plan is None:
            raise KeyError("plan not found")
        now = _now()
        plan["execution"] = {**dict(record or {}), "recorded_at": now}
        wanted = str(status or "").strip()
        if wanted:
            current = str(plan.get("status") or "draft")
            if wanted not in TRANSITIONS.get(current, ()):
                raise ValueError(f"cannot move a {current} plan to {wanted}")
            plan["status"] = wanted
            plan.setdefault("history", []).append({
                "status": wanted,
                "at": now,
                "note": _clean(note, 1200),
            })
        plan["updated_at"] = now
        save(data, path)
    return plan


def set_decision(plan_id: object, decision: dict, path: Path | None = None) -> dict:
    """Store the crew's decision and advice on the plan."""
    with LOCK:
        data = load(path)
        plan = next((item for item in data.get("plans") or []
                     if isinstance(item, dict) and item.get("id") == str(plan_id or "")), None)
        if plan is None:
            raise KeyError("plan not found")
        plan["decision"] = dict(decision or {})
        plan["updated_at"] = _now()
        save(data, path)
    return plan


def payload(path: Path | None = None) -> dict:
    store = load(path)
    plans = [plan for plan in store.get("plans") or [] if isinstance(plan, dict)]
    plans.sort(key=lambda plan: str(plan.get("updated_at") or ""), reverse=True)
    suggestions = [item for item in store.get("suggestions") or []
                   if isinstance(item, dict) and item.get("status") == "suggested"]
    suggestions.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    counts = {status: 0 for status in STATUSES}
    for plan in plans:
        status = str(plan.get("status") or "")
        if status in counts:
            counts[status] += 1
    counts["open"] = sum(counts[status] for status in ("draft", "approved", "baselined", "changed"))
    counts["suggestions"] = len(suggestions)
    return {
        "plans": plans,
        "suggestions": suggestions,
        "counts": counts,
        "statuses": [{"id": status, "label": STATUS_LABEL[status]} for status in STATUSES],
        "transitions": {status: list(TRANSITIONS[status]) for status in STATUSES},
        "guardrails": list(GUARDRAILS),
        "guardrail": "A class is a safety boundary, not an authorisation. The target still needs an owner or written permission.",
        "system_classes": [_public_system(key, value) for key, value in SYSTEM_CLASSES.items()],
        "interventions": [_public_intervention(level) for level in INTERVENTIONS],
        "hazard_labels": dict(HAZARD_LABEL),
    }


def brief(plan: dict) -> str:
    seam = plan.get("seam") or {}
    history = plan.get("history") or []
    lines = [
        "# Change brief",
        "",
        f"- **Plan:** `{plan.get('id') or ''}`",
        f"- **Status:** {STATUS_LABEL.get(str(plan.get('status')), plan.get('status') or '')}",
        f"- **Target:** {plan.get('target') or ''}",
        f"- **Dossier:** `{plan.get('dossier_id') or ''}`",
        f"- **Seam:** `{seam.get('id') or ''}` · {seam.get('locator') or ''}",
        f"- **System class:** {plan.get('system_label') or 'not recorded'}",
        f"- **Change level:** {plan.get('intervention_label') or 'not recorded'}",
        f"- **Owner / approver:** {plan.get('owner') or ''}",
        "",
    ]
    decision = plan.get("decision") or {}
    if decision:
        lines.extend(["## Crew decision", "", decision.get("advice") or "",
                      f"- **Ranked by:** confidence {round((float(decision.get('confidence') or 0)) * 100)}%"
                      f" · risk {decision.get('risk') or 'unknown'}", ""])
    lines.extend([
        "## What better means",
        "",
        plan.get("goal") or "",
        "",
        "## Smallest reversible change",
        "",
        plan.get("change") or "",
        "",
        "## Baseline",
        "",
        plan.get("baseline") or "",
        "",
        "## Verification",
        "",
        plan.get("verification") or "",
        "",
        "## Rollback",
        "",
        plan.get("rollback") or "",
        "",
        "## Seam record",
        "",
        f"- **Category:** {seam.get('category') or ''}",
        f"- **Action:** {seam.get('action') or ''}",
        f"- **Risk:** {seam.get('risk') or ''}",
        f"- **Confidence:** {round((float(seam.get('confidence') or 0)) * 100)}%",
        f"- **Probe:** {seam.get('probe') or '—'}",
    ])
    requires = seam.get("requires") or []
    if requires:
        lines.extend(["", "### Requirements before the change", ""])
        lines.extend(f"- {item}" for item in requires)
    evidence = seam.get("evidence") or []
    if evidence:
        lines.extend(["", "### Evidence references", ""])
        lines.extend(f"- `{item}`" for item in evidence)
    if plan.get("patch"):
        lines.extend(["", "## Exact patch or steps", "", "```text",
                      str(plan.get("patch")), "```"])
    system_stop = plan.get("system_stop") or []
    if not system_stop and plan.get("system_class") in SYSTEM_CLASSES:
        system_stop = list(SYSTEM_CLASSES[plan["system_class"]]["stop"])
    if system_stop:
        lines.extend(["", "## Class limits", ""])
        lines.extend(f"- {item}" for item in system_stop)
    level_requires = plan.get("intervention_requires") or []
    level_stop = plan.get("intervention_stop") or []
    if not level_requires and plan.get("intervention") in INTERVENTIONS_BY_ID:
        level_requires = list(INTERVENTIONS_BY_ID[plan["intervention"]]["requires"])
    if not level_stop and plan.get("intervention") in INTERVENTIONS_BY_ID:
        level_stop = list(INTERVENTIONS_BY_ID[plan["intervention"]]["stop"])
    if level_requires or level_stop:
        lines.extend(["", "## Change-level conditions", ""])
        lines.extend(f"- Required: {item}" for item in level_requires)
        lines.extend(f"- Stop: {item}" for item in level_stop)
    execution = plan.get("execution") or {}
    if execution:
        verify_exit = execution.get("verify_exit")
        lines.extend([
            "", "## Local execution", "",
            f"- **Work tree:** `{execution.get('root') or ''}`",
            f"- **Patch digest:** `{execution.get('patch_digest') or ''}`",
            f"- **Verification exit:** {verify_exit if verify_exit is not None else 'not run'}",
            f"- **Patch reversed:** {'yes' if execution.get('reverted') else 'no'}",
        ])
    lines.extend(["", "## Execution record", ""])
    for event in history:
        note = f" — {event.get('note')}" if event.get("note") else ""
        lines.append(f"- {event.get('at') or ''} · **{event.get('status') or ''}**{note}")
    lines.extend(["", "## Guardrails", ""])
    lines.extend(f"- {item}" for item in GUARDRAILS)
    lines.extend([
        "",
        "---",
        "",
        "This brief records intent and any executed result. Automatic apply is local,",
        "git-anchored and reversible. A human approves the plan and checks the outcome.",
        "",
    ])
    return "\n".join(lines)
