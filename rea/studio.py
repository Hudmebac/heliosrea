"""REA Studio — turn a completed dossier into a publishable evidence file.

The studio is deliberately strict. It refuses a dossier unless the operator
attested authorisation, the run completed, the target is not a fixture/demo,
and there are real findings with at least one mapped change surface. It writes a
static site, a video shot script and an evidence appendix. No model call, no
invented evidence and no secret values are written.

Generated layout::

    site/
      index.html
      evidence/index.html
      facts/index.html + facts/<slug>.html
      guides/index.html + guides/<slug>.html
      method/index.html
      crew/index.html
      proof/index.html
      newsletter/index.html
      pricing/index.html
      assets/site.css
      assets/site.js
      publications/<slug>/
        index.html
        manifest.json
        evidence.md
        video-script.md
        card.svg
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import engine
import mailroom
import media
import playbook
import site_content

ROOT = Path(__file__).resolve().parent.parent
SITE_DIR = ROOT / "site"
SITE_URL = "https://heliosrea.netlify.app"
MIN_FINDINGS = 3
MIN_EDITS = 1

FIXTURE_MARKERS = (
    "demo",
    "synthetic",
    "fixture",
    "/stage3/",
    "/stage4/",
    "example.test",
    "example.com",
    "example.org",
    "example.net",
)

PIPELINE = (
    ("evidence", "Evidence", "Run the ladder on a real target and finish the dossier."),
    ("verify", "Verify", "Check locators, confidence, redactions and the publication gate."),
    ("package", "Package", "Generate the story page, evidence appendix and video shot script."),
    ("publish", "Publish", "Put the static evidence file on the public site."),
    ("sell", "Sell", "Attach the field guide and the paid teardown offer to the proof."),
)

CREW_LANES = (
    ("Evidence", ("reverse-engineer", "embedded-re", "protocol", "archaeologist"),
     "Authorised probes, locators and a complete dossier."),
    ("Verification", ("evidence-editor", "security-re", "technical-director"),
     "Claim-to-evidence table, redaction pass and publication gate."),
    ("Story", ("ux", "evidence-editor"),
     "A plain-language spine: scope, finding, seam, proof and limit."),
    ("Video", ("video-producer", "ux"),
     "Paper edit, shot list, screen capture and a cut built from the dossier."),
    ("Publish", ("web-publisher", "security-re"),
     "Static pages, metadata, link checks and a clean deployment bundle."),
    ("Sell", ("product", "distribution"),
     "Buyer fit, the field guide, a paid teardown slot and honest outreach."),
)


# ------------------------------------------------------------------ helpers --

def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _clean(value: object, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _confidence(value: object) -> int:
    try:
        return max(0, min(100, round(float(value) * 100)))
    except (TypeError, ValueError):
        return 0


def is_fixture_target(value: object) -> bool:
    text = str(value or "").lower().replace("\\", "/")
    return any(marker in text for marker in FIXTURE_MARKERS)


def _public_target(inv: dict) -> str:
    target = engine.redact(str(inv.get("target") or "unknown target")).strip()
    kind = str(inv.get("kind") or "")
    if kind in {"website", "internet"} and "://" in target:
        return urlsplit(target).hostname or target
    if kind == "wifi" or target.lower() == "wifi":
        return "private lab network"
    if kind in {"folder", "file", "har"}:
        name = re.split(r"[\\/]", target.rstrip("/\\"))[-1]
        return name or target
    return target


def _public_locator(value: object) -> str:
    text = engine.redact(str(value or "")).strip()
    if re.match(r"^[A-Za-z]:[\\/]", text) or text.startswith("/"):
        return re.split(r"[\\/]", text.rstrip("/\\"))[-1] or "artifact"
    return text


def _default_title(inv: dict) -> str:
    return f"{_public_target(inv)} — evidence teardown"


def _digest(inv: dict) -> str:
    raw = json.dumps(inv, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _summary_counts(inv: dict) -> dict:
    findings = len(inv.get("findings") or [])
    edits = len(inv.get("edits") or [])
    probes = inv.get("probes") or []
    counts = (inv.get("summary") or {}).get("counts") or {}
    return {
        "findings": findings or int(counts.get("findings") or 0),
        "edits": edits or int(counts.get("edits") or 0),
        "probes": len(probes) or int(counts.get("probes") or 0),
        "signal": sum(1 for probe in probes if probe.get("status") == "signal")
        or int(counts.get("signal") or 0),
        "empty": sum(1 for probe in probes if probe.get("status") == "empty"),
        "error": sum(1 for probe in probes if probe.get("status") == "error"),
        "sources": len(inv.get("sources") or []),
    }


TEST_MARKERS = ("test_engine", "api.fixture", "fixture.test", "example.test", "/test/", "test_")


def _story_findings(inv: dict) -> list[dict]:
    """Select production findings for the public story.

    The evidence appendix retains everything. The story should not lead with
    test fixtures, duplicate reads or example hosts that are not the target.
    """
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    candidates = sorted(
        [f for f in (inv.get("findings") or []) if f.get("title")],
        key=lambda item: float(item.get("confidence") or 0),
        reverse=True,
    )
    for finding in candidates:
        blob = " ".join(str(finding.get(key) or "") for key in
                        ("title", "detail", "path", "quote")).lower()
        if any(marker in blob for marker in TEST_MARKERS):
            continue
        if not finding.get("path") and str(finding.get("kind") or "") in {"structure", "modules"}:
            continue
        key = (str(finding.get("title") or "").lower(),
               str(finding.get("detail") or "").lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(finding)
    return out[:6] or candidates[:4]


def _story_edits(inv: dict) -> list[dict]:
    """Production seams for the story; test-only edits stay in the appendix."""
    out: list[dict] = []
    for edit in inv.get("edits") or []:
        blob = " ".join(str(edit.get(key) or "") for key in
                        ("locator", "action", "note", "category")).lower()
        if any(marker in blob for marker in TEST_MARKERS):
            continue
        out.append(edit)
    return out[:3] or (inv.get("edits") or [])[:2]


def _fact_payloads(pubs: list[dict], extra_inv: dict | None = None) -> list[dict]:
    """Turn a published dossier into stable per-claim fact files.

    The public story is a selection. A fact file gives one finding or one seam
    its own URL, verification path and limit without adding a new claim.
    """
    facts: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for pub in pubs:
        dossier_id = str(pub.get("dossier_id") or "")
        inv = extra_inv if extra_inv and str(extra_inv.get("id") or "") == dossier_id else engine.load(dossier_id)
        if not inv:
            continue
        pub_slug = str(pub.get("slug") or "")
        story = f"../publications/{pub_slug}/"
        appendix = story + "evidence.md"
        overall = _confidence((inv.get("summary") or {}).get("confidence"))
        target = _public_target(inv)

        for finding in _story_findings(inv):
            fid = str(finding.get("id") or "finding")
            key = (dossier_id, fid)
            if key in seen:
                continue
            seen.add(key)
            locator = finding.get("path") or finding.get("locator") or ""
            if finding.get("line"):
                locator += f":{finding['line']}"
            locator_public = _public_locator(locator) or "whole-target inventory"
            detail = engine.redact(finding.get("detail") or "")
            confidence = _confidence(finding.get("confidence"))
            kind = str(finding.get("kind") or finding.get("family") or "evidence")
            why_text = {
                "inventory": "Before you can judge a system, you need its shape: file types, size, folders and binary presence. This is a map, not a vulnerability.",
                "entry-points": "Candidate entry points tell you where a reader or tester should start. They are candidates until the code or runtime confirms what actually serves.",
                "switches": "Configuration is usually the cheapest reversible seam. A supported switch can change behaviour without touching code, which makes it the first thing to verify.",
                "docs": "Documentation records intended behaviour. It is useful precisely because it can be compared with what the run actually found.",
            }.get(kind, "This observation narrows the target. It is a recorded fact with a locator, not a conclusion about the whole system.")
            verify_text = {
                "inventory": "Re-run the inventory on the same target and compare the file count, extensions and top folders. Investigate any difference before reusing the number.",
                "entry-points": "Open each named file and confirm it is served or executed by the target. Remove candidates that are docs, tests or dead files.",
                "switches": "Search the source for the switch and read every use. Confirm the default, the accepted values and what happens when it is absent.",
                "docs": "Open the documentation and run the documented command in a safe copy. Note where the documentation is stale or incomplete.",
            }.get(kind, "Open the recorded locator and compare the observation with the current artifact. Re-run the probe if the artifact changed.")
            facts.append({
                "slug": f"{pub_slug}-{fid.lower()}",
                "code": fid,
                "publication": pub_slug,
                "group": "Target facts",
                "title": engine.redact(finding.get("title") or fid),
                "eyebrow": f"Finding · {finding.get('family') or 'Evidence'}",
                "summary": detail or engine.redact(finding.get("title") or fid),
                "confidence": confidence,
                "kind": kind,
                "dossier_id": dossier_id,
                "table": {
                    "headers": ["Field", "Recorded value"],
                    "rows": [
                        ["Finding ID", fid],
                        ["Family", finding.get("family") or "General"],
                        ["Locator", locator_public],
                        ["Confidence", f"{confidence}%"],
                        ["Dossier", dossier_id],
                    ],
                },
                "body": [
                    {"h": "What was recorded", "p": [detail or "The dossier recorded this finding without a longer detail field."]},
                    {"h": "Why it matters", "p": [why_text]},
                    {"h": "How to verify it", "p": [verify_text]},
                    {"h": "What this does not prove", "p": [f"This is a {kind} observation from a {target} run. It does not establish runtime behaviour beyond the recorded probe, and it is not a vulnerability claim."]},
                ],
                "links": [
                    ("Read it in the story", story),
                    ("Open the full evidence appendix", appendix),
                    ("Read the verification guide", "../guides/verify-a-dossier.html"),
                ],
                "note": "Selected production evidence. The full record, including test-only observations and duplicates, stays in the evidence appendix.",
            })

        for edit in _story_edits(inv):
            eid = str(edit.get("id") or "seam")
            key = (dossier_id, eid)
            if key in seen:
                continue
            seen.add(key)
            locator = _public_locator(edit.get("locator")) or "recorded seam"
            action = engine.redact(edit.get("action") or "")
            risk = str(edit.get("risk") or "unknown")
            confidence = _confidence(edit.get("confidence"))
            facts.append({
                "slug": f"{pub_slug}-{eid.lower()}",
                "code": eid,
                "group": "Change surfaces",
                "title": f"{locator} — recorded change surface",
                "eyebrow": f"Seam · {edit.get('category') or 'Change surface'}",
                "summary": action or "A recorded place where behaviour could be changed.",
                "confidence": confidence,
                "kind": "change surface",
                "dossier_id": dossier_id,
                "table": {
                    "headers": ["Field", "Recorded value"],
                    "rows": [
                        ["Seam ID", eid],
                        ["Category", edit.get("category") or "General"],
                        ["Locator", locator],
                        ["Risk", risk],
                        ["Confidence", f"{confidence}%"],
                    ],
                },
                "body": [
                    {"h": "What was recorded", "p": [action or "The dossier recorded a change surface without an action note."]},
                    {"h": "Where it sits", "p": [f"The seam is recorded at {locator}. It is a place where behaviour can be altered or repaired; it is not a claim that a change was made."]},
                    {"h": "Safe way to test it", "p": ["Take a baseline on a copy or a maintenance window, make the smallest reversible change, and compare the same probes before and after. Record the result even if nothing changes."]},
                    {"h": "What was not done", "p": ["No change is claimed by this fact file. The risk and confidence are the dossier's assessment, not a guarantee that the seam is safe in production."]},
                ],
                "links": [
                    ("Read it in the story", story),
                    ("Open the full evidence appendix", appendix),
                    ("Read the verification guide", "../guides/verify-a-dossier.html"),
                ],
                "note": "A seam is a place where change is possible. It is not a change that was made or a proven exploit.",
            })

        summary = inv.get("summary") or {}
        modes = summary.get("headline") or []
        mode_value = next((str(item.get("value")) for item in modes if item.get("label") == "Access modes"), "")
        counts = _summary_counts(inv)
        facts.append({
            "slug": f"{pub_slug}-scope",
            "code": "SCOPE",
            "publication": pub_slug,
            "group": "Run facts",
            "title": "Scope, authorisation and what was actually run",
            "eyebrow": "Run fact · Dossier frame",
            "summary": engine.redact(inv.get("scope") or "The dossier records the target and scope."),
            "confidence": overall,
            "kind": "run",
            "dossier_id": dossier_id,
            "table": {
                "headers": ["Field", "Recorded value"],
                "rows": [
                    ["Target", target],
                    ["Kind", summary.get("kind_label") or inv.get("kind") or "unknown"],
                    ["Scope", engine.redact(inv.get("scope") or "not stated")],
                    ["Authorisation", "operator attested ownership or written permission"],
                    ["Finished", inv.get("finished") or inv.get("created") or "unknown"],
                    ["Access modes", mode_value or "not recorded"],
                    ["Evidence / seams / probes", f"{counts['findings']} / {counts['edits']} / {counts['probes']}"],
                    ["Dossier SHA-256", str(pub.get("digest") or "")],
                ],
            },
            "body": [
                {"h": "What was in scope", "p": [engine.redact(inv.get("scope") or "The scope was not stated in the dossier.")]},
                {"h": "What was not in scope", "p": ["The page does not claim production safety, complete coverage or a successful exploit. Probes that returned empty or error are listed in the appendix and in the probe fact file."]},
                {"h": "How to reproduce the frame", "p": ["Open the dossier, confirm the target and authorisation, then re-run the recorded commands on a copy or an authorised window. Compare the counts before reusing them."]},
                {"h": "What this does not prove", "p": ["A completed dossier is a record of what was run, not proof that nothing else exists. The scope and the limits are part of the evidence."]},
            ],
            "links": [
                ("Read the evidence file", story),
                ("Open the appendix", appendix),
                ("Read the method", "../method/"),
            ],
            "note": "This is the frame around the evidence: target, permission, depth, counts and date.",
        })

        probe_rows = [
            [probe.get("label") or probe.get("id"), probe.get("family") or "",
             probe.get("status") or "", engine.redact(probe.get("summary") or "")]
            for probe in (inv.get("probes") or [])
        ]
        facts.append({
            "slug": f"{pub_slug}-probe-ledger",
            "code": "PROBES",
            "publication": pub_slug,
            "group": "Run facts",
            "title": "Probe ledger — signal, empty and error",
            "eyebrow": "Run fact · Probe ledger",
            "summary": f"{counts['probes']} probes recorded: {counts['signal']} signal, {counts['empty']} empty, {counts['error']} error. The negative results are part of the file.",
            "confidence": overall,
            "kind": "run",
            "dossier_id": dossier_id,
            "table": {"headers": ["Probe", "Family", "Status", "Recorded result"], "rows": probe_rows},
            "body": [
                {"h": "How to read the ledger", "p": ["A signal is not automatically a finding. An empty result is a fact about the target. An error is a route for the next run. The status matters as much as the result."]},
                {"h": "Why empty and error stay in", "p": ["Hiding a negative result makes the remaining evidence look stronger than it is. The appendix keeps the full ledger so a reader can see what was not found as well as what was."]},
                {"h": "What to run next", "p": ["Choose the next question from the empty and error rows. Re-run only when the target or method changed, and record the reason for the retry."]},
            ],
            "links": [
                ("Read the evidence file", story),
                ("Open the full appendix", appendix),
                ("Read the evidence guide", "../guides/run-the-evidence-ladder.html"),
            ],
            "note": "A probe ledger is an audit trail. It is not a scorecard and it does not promise complete coverage.",
        })

    return facts


def gate_for(inv: dict) -> dict:
    """Return the publication gate and a human-readable reason when blocked."""
    reasons: list[str] = []
    if inv.get("authorized") is not True:
        reasons.append("authorisation is not recorded")
    if inv.get("status") != "complete":
        reasons.append("the investigation is not complete")
    if is_fixture_target(inv.get("target")):
        reasons.append("the target is a fixture/demo/example, not publishable evidence")
    target = str(inv.get("target") or "").strip().lower()
    if inv.get("kind") == "wifi" or target == "wifi":
        reasons.append("private WiFi/LAN capture cannot be published automatically")
    if target == "internet":
        reasons.append("private egress capture cannot be published automatically")
    if len(inv.get("findings") or []) < MIN_FINDINGS:
        reasons.append(f"at least {MIN_FINDINGS} evidence records are required")
    if len(inv.get("edits") or []) < MIN_EDITS:
        reasons.append(f"at least {MIN_EDITS} mapped change surface is required")
    if not any(str(f.get("id") or "") and str(f.get("title") or "")
               for f in (inv.get("findings") or [])):
        reasons.append("evidence records need an id and a title")
    return {
        "ready": not reasons,
        "reason": "; ".join(reasons),
        "min_findings": MIN_FINDINGS,
        "min_edits": MIN_EDITS,
        "rules": [
            "Operator attested ownership or written permission.",
            "Investigation status is complete.",
            "No fixture, demo, example or private network target.",
            f"At least {MIN_FINDINGS} evidence records and {MIN_EDITS} mapped seam.",
            "Findings retain an ID, locator and confidence; secrets are redacted.",
        ],
    }


def _candidate(inv: dict) -> dict:
    gate = gate_for(inv)
    counts = _summary_counts(inv)
    summary = inv.get("summary") or {}
    return {
        "id": inv.get("id"),
        "target": _public_target(inv),
        "title": _default_title(inv),
        "kind": inv.get("kind"),
        "kind_label": summary.get("kind_label") or inv.get("kind"),
        "verdict": summary.get("verdict") or "",
        "findings": counts["findings"],
        "edits": counts["edits"],
        "probes": counts["probes"],
        "confidence": round(float(summary.get("confidence") or 0), 2),
        "finished": inv.get("finished") or inv.get("created") or "",
        "ready": gate["ready"],
        "reason": gate["reason"],
    }


def monthly_material(site_root: str | Path | None = None) -> tuple[dict | None, list[dict]]:
    """Return the latest published file and its fact payloads for the monthly note."""
    root = Path(site_root) if site_root else SITE_DIR
    pubs = _published_manifests(root)
    return (pubs[0] if pubs else None), _fact_payloads(pubs, None)


def monthly_facts(site_root: str | Path | None = None) -> list[dict]:
    return monthly_material(site_root)[1]


def _published_manifests(site_root: Path) -> list[dict]:
    root = Path(site_root) / "publications"
    if not root.is_dir():
        return []
    items: list[dict] = []
    for path in sorted(root.glob("*/manifest.json")):
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("slug"):
            items.append(data)
    items.sort(key=lambda item: str(item.get("published_at") or ""), reverse=True)
    return items


def published(site_root: str | Path | None = None) -> list[dict]:
    return _published_manifests(Path(site_root) if site_root else SITE_DIR)


def _profession(role_id: str) -> dict | None:
    return next((item for item in playbook.PROFESSIONS if item.get("id") == role_id), None)


def _crew_lanes() -> list[dict]:
    lanes = []
    for name, role_ids, output in CREW_LANES:
        people = []
        for role_id in role_ids:
            person = _profession(role_id)
            if person:
                people.append({"id": role_id, "name": person.get("name", role_id)})
        lanes.append({"name": name, "crew": people, "output": output})
    return lanes


def _pipeline(candidates: list[dict], pubs: list[dict], real_count: int) -> list[dict]:
    ready = len(candidates)
    counts = {
        "evidence": real_count,
        "verify": ready,
        "package": len(pubs),
        "publish": len(pubs),
        "sell": 1,
    }
    details = {
        "evidence": f"{real_count} real dossier(s) available",
        "verify": f"{ready} dossier(s) through the gate",
        "package": f"{len(pubs)} evidence package(s) built",
        "publish": "Static site at /published/",
        "sell": "Optional £5 donation and field-note list wired",
    }
    out = []
    for key, label, action in PIPELINE:
        out.append({
            "id": key,
            "label": label,
            "count": counts[key],
            "detail": details[key],
            "state": "live" if counts[key] else "waiting",
            "next": action,
        })
    return out


def payload(site_root: str | Path | None = None) -> dict:
    root = Path(site_root) if site_root else SITE_DIR
    ensure_site(root)
    briefs = engine.list_investigations(500)
    hidden = 0
    candidates: list[dict] = []
    real_count = 0
    for brief in briefs:
        inv = engine.load(str(brief.get("id") or ""))
        if not inv:
            continue
        if is_fixture_target(inv.get("target")):
            hidden += 1
            continue
        real_count += 1
        candidate = _candidate(inv)
        if candidate["ready"]:
            candidates.append(candidate)
    pubs = published(root)
    brand = media.payload()
    return {
        "brand": brand["channel"],
        "stripe": brand["stripe"],
        "contact_email": brand["contact_email"],
        "stack": brand["stack"],
        "rules": brand["rules"],
        "principles": brand["principles"],
        "gate": {
            "min_findings": MIN_FINDINGS,
            "min_edits": MIN_EDITS,
            "rules": [
                "Operator attested ownership or written permission.",
                "Investigation status is complete.",
                "No fixture, demo, example or private network target.",
                f"At least {MIN_FINDINGS} evidence records and {MIN_EDITS} mapped seam.",
                "Findings retain an ID, locator and confidence; secrets are redacted.",
            ],
        },
        "candidates": candidates,
        "published": pubs,
        "hidden_fixtures": hidden,
        "totals": {
            "real_dossiers": real_count,
            "ready": len(candidates),
            "evidence": sum(item["findings"] for item in candidates),
            "published": len(pubs),
        },
        "pipeline": _pipeline(candidates, pubs, real_count),
        "crew_lanes": _crew_lanes(),
        "site": {"path": str(root), "url": "/published/"},
        "note": (
            "The Engine Room only packages completed, authorised, real dossiers. Fixture and "
            "demo dossiers are hidden from publication. A story without evidence stays out."
        ),
        "generated": _now(),
    }


# -------------------------------------------------------------------- site ---

SITE_CSS = """
:root{--bg:#070a0f;--panel:#0d141d;--line:rgba(151,193,224,.18);--text:#eaf4ff;
--dim:#a8bfd2;--faint:#7f96aa;--cyan:#62e8ff;--gold:#f6ca70;--green:#81f0ad;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
--sans:ui-sans-serif,system-ui,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
*{box-sizing:border-box}body{margin:0;color:var(--text);background:
radial-gradient(900px 600px at 10% -10%,rgba(53,127,190,.2),transparent 65%),
var(--bg);font-family:var(--sans);line-height:1.6}
a{color:#b8e9f6;text-decoration:none}a:hover{color:var(--cyan);text-decoration:underline}
.wrap{max-width:1120px;margin:0 auto;padding:28px 22px 70px}
.top{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;
flex-wrap:wrap;border-bottom:1px solid var(--line);padding-bottom:18px;margin-bottom:34px}
.brand b{display:block;letter-spacing:.22em;font-size:14px}.brand span{display:block;
color:var(--faint);font-size:10px;letter-spacing:.16em;text-transform:uppercase;margin-top:4px}
.nav{font-family:var(--mono);font-size:11px;color:var(--faint);display:flex;gap:14px;flex-wrap:wrap}
.eyebrow{font-family:var(--mono);font-size:10px;letter-spacing:.16em;text-transform:uppercase;
color:var(--gold)}h1{font-size:42px;line-height:1.12;letter-spacing:-.035em;font-weight:600;
margin:8px 0 12px;max-width:880px}h2{font-size:23px;line-height:1.25;margin:30px 0 10px;
letter-spacing:-.02em;font-weight:580}h3{font-size:15px;margin:18px 0 5px;font-weight:560}
p{color:#c3d6e5}.dek{font-size:17px;color:var(--dim);max-width:820px;line-height:1.7}
.meta{display:flex;gap:7px;flex-wrap:wrap;margin:18px 0 8px}.chip{display:inline-flex;
gap:5px;border:1px solid var(--line);border-radius:999px;padding:4px 9px;font-family:var(--mono);
font-size:10px;color:#b8cfe0;background:rgba(255,255,255,.025)}.chip b{color:#eef8ff}
.card{border:1px solid var(--line);border-radius:12px;padding:18px;
background:linear-gradient(150deg,rgba(14,22,32,.88),rgba(8,13,20,.88));margin:14px 0}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.proof{border-left:2px solid
rgba(98,232,255,.45);padding:10px 0 10px 14px;margin:10px 0}.proof b{display:block;color:#eef8ff}
.proof small{display:block;color:var(--faint);font-family:var(--mono);font-size:10px;margin-top:3px}
.tag{display:inline-block;border:1px solid var(--line);border-radius:4px;padding:2px 5px;
font-family:var(--mono);font-size:9px;letter-spacing:.05em;text-transform:uppercase;color:#c3dcea}
.tag.low{border-color:rgba(129,240,173,.35);color:#b6f5ce}.tag.medium{
border-color:rgba(246,202,112,.35);color:#ffe4ac}.tag.high{border-color:rgba(255,145,160,.4);color:#ffc9d0}
table{width:100%;border-collapse:collapse;margin:10px 0;font-size:12px}th,td{text-align:left;
vertical-align:top;padding:8px;border-bottom:1px solid rgba(151,193,224,.1)}th{color:var(--faint);
font-family:var(--mono);font-size:9px;text-transform:uppercase;letter-spacing:.08em}
.btn{display:inline-flex;align-items:center;border:1px solid rgba(151,193,224,.28);border-radius:7px;
padding:8px 11px;background:rgba(255,255,255,.04);color:#dcecf8;font-size:12px;margin:4px 6px 4px 0}
.btn.gold{border-color:rgba(246,202,112,.45);background:rgba(246,202,112,.1);color:#ffe6b3}
.btn:hover{border-color:rgba(98,232,255,.55);text-decoration:none;color:#fff}
.note{border:1px solid rgba(246,202,112,.25);background:rgba(246,202,112,.05);border-radius:9px;
padding:11px 13px;color:#d8cda9;font-size:12px}.story-card{display:grid;grid-template-columns:220px 1fr;
gap:16px;align-items:start}.story-card img{width:100%;border-radius:8px;border:1px solid var(--line)}
.story-card h2{margin:2px 0 5px}.story-card p{margin:0 0 8px;font-size:12px}
.empty{border:1px dashed rgba(151,193,224,.28);border-radius:12px;padding:26px;color:var(--dim)}
.foot{border-top:1px solid var(--line);margin-top:40px;padding-top:16px;color:var(--faint);
font-size:11px;line-height:1.7}.hash{font-family:var(--mono);font-size:10px;word-break:break-all;
color:#9fc7dc}.small{font-size:12px;color:var(--faint)}
.nav-buy{color:var(--gold)}.nav a.on{color:var(--cyan);text-decoration:underline;text-underline-offset:3px}
.hero{padding:6px 0 8px}.hero h1{font-size:52px}
.hero-actions{margin:16px 0 20px}
.stat-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:18px 0}
.stat{border:1px solid var(--line);border-radius:9px;padding:10px 12px;background:rgba(255,255,255,.02)}
.stat b{display:block;font-family:var(--mono);font-size:24px;color:var(--cyan);line-height:1.2}
.stat span{font-size:10px;color:var(--faint);text-transform:uppercase;letter-spacing:.08em}
.split{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:24px 0}
.section-head{display:flex;justify-content:space-between;gap:18px;align-items:end;margin:34px 0 8px;flex-wrap:wrap}
.section-head h2{margin:4px 0}.section-head p{max-width:720px;margin:0;font-size:13px}
.cards{display:grid;grid-template-columns:1fr;gap:14px}
.cards-2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.cards-3{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.cards-4{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.cards-2 .card,.cards-3 .card,.cards-4 .card{margin:0}
.fact-card{position:relative;padding-top:14px}.fact-code{position:absolute;top:12px;right:12px;font-family:var(--mono);font-size:9px;color:var(--gold);border:1px solid rgba(246,202,112,.3);border-radius:4px;padding:2px 5px}
.fact-card h3,.guide-card h3{font-size:16px;line-height:1.3;margin:6px 0}
.fact-card p,.guide-card p{font-size:12px;color:var(--dim);margin:0 0 8px}
.lane h3{font-size:14px;line-height:1.4}
.quote{border-left:2px solid rgba(246,202,112,.55);padding:6px 0 6px 14px;margin:0}
.quote blockquote{margin:0;color:#e7f2fb;font-size:14px;line-height:1.6}
.quote figcaption{font-family:var(--mono);font-size:9px;color:var(--faint);margin-top:8px;text-transform:uppercase;letter-spacing:.08em}
.quote-grid .quote{background:rgba(246,202,112,.03);padding:12px;border-radius:8px}
.steps{counter-reset:step;list-style:none;padding:0;margin:10px 0}
.steps li{counter-increment:step;position:relative;padding:10px 0 10px 42px;border-bottom:1px solid rgba(151,193,224,.1);color:var(--dim)}
.steps li:before{content:counter(step,decimal-leading-zero);position:absolute;left:0;top:9px;font-family:var(--mono);font-size:11px;color:var(--gold);border:1px solid var(--line);border-radius:4px;padding:1px 5px}
.steps b{color:var(--text)}
.prose{max-width:820px}.prose-block{margin:18px 0}
.prose-block h2{font-size:20px;margin:18px 0 6px}.prose-block h3{font-size:15px;margin:16px 0 4px}
.prose p{font-size:14px}.prose ul{margin:8px 0 0 18px;padding:0}.prose li{margin:4px 0;color:var(--dim)}
.breadcrumb{font-family:var(--mono);font-size:10px;color:var(--faint);margin:0 0 14px}.breadcrumb a{color:#9fc7dc}
.table-wrap{overflow-x:auto;margin:12px 0}.table-wrap table{min-width:560px}
.faq details{border-bottom:1px solid rgba(151,193,224,.12);padding:10px 0}.faq summary{cursor:pointer;color:var(--text);font-weight:560}.faq p{margin:6px 0 0;font-size:13px}
.rule-list{margin:4px 0 4px 18px;padding:0}.rule-list li{margin:5px 0;color:var(--dim);font-size:13px}
.price-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:18px 0}
.price-grid.price-single{grid-template-columns:minmax(0,480px)}
.price{display:flex;flex-direction:column}.price .amount{font-family:var(--mono);font-size:30px;color:var(--gold);margin:4px 0}
.price h3{margin:0 0 6px}.price p{font-size:12px;color:var(--dim)}.price ul{margin:4px 0 14px 18px;padding:0}.price li{font-size:11px;color:var(--faint);margin:3px 0}.price .btn{margin-top:auto;align-self:flex-start}
.subscribe-form{border:1px solid rgba(98,232,255,.22);border-radius:10px;padding:16px;background:rgba(98,232,255,.025);margin:12px 0}
.subscribe-form h2,.subscribe-form h3{margin-top:0}.form-grid{display:grid;grid-template-columns:1.4fr 1fr;gap:10px}
.subscribe-form label{display:block;font-family:var(--mono);font-size:10px;letter-spacing:.05em;color:var(--dim);margin:0 0 9px}
.subscribe-form input[type=email],.subscribe-form input[type=text]{display:block;width:100%;margin-top:4px;padding:9px 10px;border:1px solid var(--line);border-radius:6px;background:#08111a;color:var(--text);font-size:13px}
.subscribe-form fieldset{border:0;padding:0;margin:6px 0 10px}.subscribe-form legend{font-family:var(--mono);font-size:10px;color:var(--dim);margin-bottom:5px}
.subscribe-form .check{display:flex;gap:7px;align-items:flex-start;font-family:var(--sans);font-size:12px;letter-spacing:0;color:var(--dim);margin:6px 0}
.subscribe-form .check input{margin-top:3px;accent-color:var(--gold)}.subscribe-form .req{color:var(--gold)}.subscribe-form .opt{color:var(--faint)}
.subscribe-form .btn{cursor:pointer}.form-note{font-size:11px;color:var(--faint);margin:8px 0 0}.form-result{font-size:12px;margin-top:10px;min-height:18px}.form-result.ok{color:var(--green)}.form-result.err{color:#ffb3bd}
.issue-card h3{margin:4px 0 5px}.issue-card details{margin-top:8px}.issue-card summary{cursor:pointer;color:#b8e9f6;font-size:12px}.issue-section{margin:10px 0}.issue-section h4{font-family:var(--mono);font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--gold);margin:0 0 3px}.issue-section p{font-size:13px;margin:4px 0}
.toc{border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin:18px 0;background:rgba(255,255,255,.02)}.toc b{font-family:var(--mono);font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:var(--faint)}.toc ol{margin:8px 0 0 18px;padding:0}.toc li{margin:3px 0;font-size:12px}
.footer-grid{display:grid;grid-template-columns:1.5fr 1fr 1fr 1fr;gap:20px;margin-bottom:18px}
.footer-grid b{display:block;letter-spacing:.16em;font-size:11px;margin-bottom:6px}
.footer-grid h4{font-family:var(--mono);font-size:9px;letter-spacing:.12em;text-transform:uppercase;color:var(--faint);margin:0 0 6px}.footer-grid a{display:block;font-size:11px;margin:3px 0}.footer-grid p{font-size:11px;color:var(--faint);max-width:360px}
.footer-rule{border-top:1px solid rgba(151,193,224,.12);padding-top:12px;margin:0}
.skip{position:absolute;left:-9999px;top:10px;z-index:100;background:var(--gold);color:#111;padding:8px 12px;border-radius:6px;font:600 12px var(--sans)}.skip:focus{left:14px}
.nav-start{color:var(--gold)}.nav-search{border:0;background:none;color:inherit;font:inherit;padding:0;cursor:pointer}.nav-search:hover{color:var(--cyan)}.nav kbd{font:9px var(--mono);border:1px solid var(--line);border-radius:3px;padding:0 4px;color:var(--faint)}
.search-panel{position:fixed;inset:0;z-index:80;background:rgba(3,7,12,.84);backdrop-filter:blur(6px);padding:8vh 16px;overflow:auto}.search-panel[hidden]{display:none}
.search-box{max-width:760px;margin:0 auto;border:1px solid rgba(98,232,255,.35);border-radius:14px;padding:18px;background:#0a121b;box-shadow:0 24px 80px rgba(0,0,0,.5)}
.search-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:10px}.search-head b{font-size:16px}.search-close{border:1px solid var(--line);border-radius:5px;background:transparent;color:var(--dim);font:10px var(--mono);padding:5px 7px;cursor:pointer}.search-close:hover{color:#fff;border-color:var(--cyan)}
#search-input{width:100%;border:1px solid rgba(98,232,255,.3);border-radius:8px;background:#060d14;color:var(--text);font:15px var(--sans);padding:11px 12px;outline:none}#search-input:focus{border-color:var(--cyan);box-shadow:0 0 0 2px rgba(98,232,255,.12)}
.search-hint{font:10px var(--mono);color:var(--faint);margin:7px 0 12px}.search-results{display:grid;gap:6px;max-height:56vh;overflow:auto}.search-result{display:block;border:1px solid rgba(151,193,224,.14);border-radius:8px;padding:9px 11px;background:rgba(255,255,255,.02)}.search-result:hover{border-color:rgba(98,232,255,.45);text-decoration:none}.search-result b{display:block;font-size:13px;color:#eef8ff;font-weight:560}.search-result p{margin:2px 0 0;font-size:11px;color:var(--dim)}.search-result span{font:9px var(--mono);color:var(--gold);text-transform:uppercase;letter-spacing:.08em}.search-empty{color:var(--faint);font-size:12px;padding:10px 0}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.path-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:18px 0}.path-card{display:flex;flex-direction:column;border:1px solid var(--line);border-top:2px solid rgba(98,232,255,.5);border-radius:11px;padding:16px;background:linear-gradient(150deg,rgba(14,22,32,.9),rgba(8,13,20,.9))}.path-card.curious{border-top-color:rgba(246,202,112,.8)}.path-card.beginner{border-top-color:rgba(98,232,255,.75)}.path-card.expert{border-top-color:rgba(129,240,173,.7)}.path-label{font:9px var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--gold)}.path-card h3{font-size:18px;margin:6px 0 6px}.path-card p{font-size:12px;color:var(--dim);margin:0 0 8px}.path-list{margin:0 0 10px 17px;padding:0}.path-list li{font-size:11.5px;color:#a8bfd2;margin:4px 0}.path-actions{margin-top:auto;padding-top:8px}.path-actions .btn{font-size:11px}
.video-block{margin:14px 0;border:1px solid rgba(98,232,255,.24);border-radius:11px;padding:10px;background:rgba(4,10,16,.8)}.video-block video{display:block;width:100%;aspect-ratio:16/9;background:#02060a;border-radius:7px}.video-block figcaption{font-size:11px;color:var(--faint);line-height:1.55;padding:8px 2px 0}.video-block figcaption b{color:var(--dim)}
.evidence-reader{border:1px solid rgba(98,232,255,.24);border-radius:12px;padding:14px;background:rgba(98,232,255,.025);margin:14px 0}.evidence-tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}.evidence-tabs button{border:1px solid var(--line);border-radius:5px;background:rgba(255,255,255,.03);color:var(--dim);font:10px var(--mono);letter-spacing:.06em;text-transform:uppercase;padding:5px 8px;cursor:pointer}.evidence-tabs button[aria-selected=true]{border-color:rgba(246,202,112,.55);color:var(--gold);background:rgba(246,202,112,.08)}.evidence-panel{padding:4px 2px}.evidence-panel h3{font-size:22px;margin:4px 0 7px;line-height:1.3}.evidence-panel p{font-size:13px;margin:0 0 8px}.evidence-panel[hidden]{display:none}
.transcript{border:1px solid var(--line);border-radius:10px;padding:12px 14px;background:rgba(255,255,255,.02);margin:12px 0}.transcript-line{display:grid;grid-template-columns:34px 1fr;gap:10px;padding:8px 0;border-bottom:1px solid rgba(151,193,224,.09)}.transcript-line:last-child{border-bottom:0}.transcript-line>span{font:9px var(--mono);color:var(--gold)}.transcript-line b{font-size:12px;color:#eef8ff}.transcript-line p{font-size:12px;color:var(--dim);margin:2px 0 0}
.note-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:16px 0}.note-card{display:grid;grid-template-columns:170px 1fr;gap:12px;border:1px solid var(--line);border-top:2px solid var(--note-accent,#f6ca70);border-radius:10px;padding:10px;background:rgba(255,255,255,.025);transition:border-color .15s ease,transform .15s ease}.note-card:hover{border-color:var(--note-accent,#f6ca70);transform:translateY(-1px)}.note-thumb img{display:block;width:100%;aspect-ratio:16/9;object-fit:cover;border:1px solid var(--line);border-radius:6px;background:#02060a}.note-copy{min-width:0}.note-number{font:9px var(--mono);letter-spacing:.1em;color:var(--note-accent,#f6ca70);text-transform:uppercase}.note-card h3{font-size:15px;line-height:1.3;margin:3px 0 5px}.note-card h3 a{color:#eef8ff}.note-card p{font-size:11px;color:var(--dim);margin:0 0 7px;line-height:1.5}.note-card .meta{gap:4px}.note-card .chip{font-size:8px;padding:2px 4px}.note-nav{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:28px 0}.note-nav a{display:block;border:1px solid var(--line);border-radius:9px;padding:10px 12px;background:rgba(255,255,255,.025)}.note-nav a:hover{border-color:rgba(98,232,255,.45);text-decoration:none}.note-nav .next{text-align:right}.note-nav span{display:block;font:9px var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--faint);margin-bottom:3px}.note-nav b{font-size:12px;color:#dcecf8;font-weight:540}
.glossary-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:16px 0}.glossary-term{border-left:2px solid rgba(246,202,112,.45);padding:2px 0 2px 12px}.glossary-term h3{font-size:15px;margin:0 0 3px}.glossary-term p{font-size:12px;color:var(--dim);margin:0}
.guide-nav{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:28px 0}.guide-nav a{display:block;border:1px solid var(--line);border-radius:9px;padding:10px 12px;background:rgba(255,255,255,.025)}.guide-nav a:hover{border-color:rgba(98,232,255,.45);text-decoration:none}.guide-nav .next{text-align:right}.guide-nav span{display:block;font:9px var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--faint);margin-bottom:3px}.guide-nav b{font-size:12px;color:#dcecf8;font-weight:540}
@media(max-width:900px){.path-grid,.glossary-grid{grid-template-columns:1fr 1fr}.guide-nav,.note-nav{grid-template-columns:1fr}.guide-nav .next,.note-nav .next{text-align:left}.cards-3,.cards-4,.price-grid{grid-template-columns:1fr 1fr}.stat-grid{grid-template-columns:repeat(3,1fr)}.footer-grid{grid-template-columns:1fr 1fr}.note-grid{grid-template-columns:1fr}}
@media(max-width:720px){h1{font-size:30px}.hero h1{font-size:34px}.grid,.story-card,.split,.cards-2,.cards-3,.cards-4,.price-grid,.form-grid,.path-grid,.glossary-grid{grid-template-columns:1fr}.stat-grid{grid-template-columns:1fr 1fr}.wrap{padding:22px 15px 50px}.card{padding:14px}.footer-grid{grid-template-columns:1fr}.note-card{grid-template-columns:125px 1fr}}
.download-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:16px 0}
pre.shell{margin:7px 0 0;padding:9px 11px;border:1px solid var(--line);border-radius:7px;background:#03080d;color:#bfe6ff;font:11.5px/1.6 var(--mono);overflow:auto;white-space:pre}
pre.shell code{font:inherit;color:inherit;background:none;padding:0}
@media(max-width:720px){.download-grid{grid-template-columns:1fr}}
""".strip()


def _page_url(root: Path, path: Path) -> str:
    rel = path.relative_to(root).as_posix()
    if rel.endswith("index.html"):
        rel = rel[: -len("index.html")]
    return SITE_URL + "/" + rel


def _stamp_canonicals(root: Path) -> None:
    nl = chr(10)
    for path in sorted(root.rglob("*.html")):
        if path.name == "404.html":
            continue
        text = path.read_text("utf-8")
        if 'rel="canonical"' in text:
            continue
        tag = f'<link rel="canonical" href="{html.escape(_page_url(root, path), quote=True)}">'
        if '<meta name="description"' in text:
            text = text.replace('<meta name="description"', tag + nl + '<meta name="description"', 1)
        else:
            text = text.replace("</head>", tag + "</head>", 1)
        _write(path, text)


def _write_site_indexes(root: Path) -> None:
    nl = chr(10)
    pages = [path for path in sorted(root.rglob("*.html")) if path.name != "404.html"]
    urls = "".join(
        f"<url><loc>{html.escape(_page_url(root, path))}</loc></url>" for path in pages
    )
    _write(root / "sitemap.xml",
           '<?xml version="1.0" encoding="UTF-8"?>' + nl +
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' +
           urls + "</urlset>" + nl)
    _write(root / "robots.txt",
           "User-agent: *" + nl + "Allow: /" + nl + nl +
           "Sitemap: " + SITE_URL + "/sitemap.xml" + nl)


def ensure_site(site_root: str | Path | None = None, inv: dict | None = None) -> Path:
    root = Path(site_root) if site_root else SITE_DIR
    pubs = _published_manifests(root)
    facts = _fact_payloads(pubs, inv)
    _write(root / "assets" / "site.css", SITE_CSS)
    site_content.write_site(root, pubs, facts, _crew_lanes(), mailroom.public_issues())
    _stamp_canonicals(root)
    _write_site_indexes(root)
    return root


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, "utf-8")
    tmp.replace(path)


def _escape(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _line_break(text: str, width: int, limit: int = 4) -> list[str]:
    words = _clean(text, 180).split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
            if len(lines) >= limit:
                break
        else:
            current = candidate
    if current and len(lines) < limit:
        lines.append(current)
    if not lines:
        lines = ["Evidence file"]
    if len(lines) == limit and words:
        lines[-1] = lines[-1][: max(0, width - 1)].rstrip() + "…"
    return lines


def _card_svg(title: str, target: str, findings: int, edits: int, digest: str) -> str:
    title_lines = _line_break(title, 30, 3)
    title_svg = "".join(
        f'<text x="72" y="{232 + index * 62}" font-size="48" font-weight="650" fill="#eaf4ff">{_escape(line)}</text>'
        for index, line in enumerate(title_lines)
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#0b1622"/><stop offset="1" stop-color="#05080d"/></linearGradient>
<radialGradient id="glow" cx="78%" cy="18%" r="55%"><stop offset="0" stop-color="#62e8ff" stop-opacity=".18"/>
<stop offset="1" stop-color="#62e8ff" stop-opacity="0"/></radialGradient></defs>
<rect width="1280" height="720" fill="url(#bg)"/><rect width="1280" height="720" fill="url(#glow)"/>
<text x="72" y="92" font-family="ui-monospace, Menlo, monospace" font-size="17" letter-spacing="5" fill="#f6ca70">THE TEARDOWN ROOM</text>
<text x="72" y="124" font-family="ui-monospace, Menlo, monospace" font-size="13" letter-spacing="2" fill="#7f96aa">EVIDENCE FILE · {_escape(target)}</text>
{title_svg}
<line x1="72" y1="472" x2="1208" y2="472" stroke="#1e3345" stroke-width="1"/>
<text x="72" y="518" font-family="ui-monospace, Menlo, monospace" font-size="17" fill="#b8e9f6">{findings} EVIDENCE RECORDS · {edits} CHANGE SURFACES</text>
<text x="72" y="554" font-family="ui-monospace, Menlo, monospace" font-size="13" fill="#7f96aa">sha256 {_escape(digest[:32])}…</text>
<text x="72" y="644" font-family="ui-monospace, Menlo, monospace" font-size="12" fill="#7f96aa">AUTHORISED · REDACTED · LIMITS STATED</text>
<circle cx="1172" cy="622" r="34" fill="none" stroke="#62e8ff" stroke-opacity=".45"/>
<text x="1172" y="630" text-anchor="middle" font-family="ui-monospace, Menlo, monospace" font-size="20" fill="#62e8ff">⌬</text>
</svg>"""


def _evidence_md(inv: dict, title: str, digest: str) -> str:
    c = _summary_counts(inv)
    lines = [
        f"# Evidence appendix — {title}",
        "",
        f"- **Dossier:** `{inv.get('id')}`",
        f"- **Target:** {_public_target(inv)}",
        f"- **Kind:** {(inv.get('summary') or {}).get('kind_label') or inv.get('kind')}",
        f"- **Finished:** {inv.get('finished') or inv.get('created') or 'unknown'}",
        f"- **Scope:** {engine.redact(inv.get('scope') or 'not stated')}",
        "- **Authorisation:** operator attested ownership / written permission",
        f"- **Dossier SHA-256:** `{digest}`",
        f"- **Evidence records:** {c['findings']} · **Change surfaces:** {c['edits']}",
        "",
        "## Findings",
        "",
        "| ID | Family | Finding | Locator | Confidence |",
        "|---|---|---|---|---|",
    ]
    for finding in inv.get("findings") or []:
        locator = finding.get("path") or finding.get("locator") or "—"
        if finding.get("line"):
            locator += f":{finding['line']}"
        lines.append(
            f"| {finding.get('id')} | {finding.get('family') or 'General'} | "
            f"{engine.redact(finding.get('title') or '')} | `{_public_locator(locator)}` | "
            f"{_confidence(finding.get('confidence'))}% |"
        )
        detail = engine.redact(finding.get("detail") or "")
        if detail:
            lines.append(f"|  |  | {detail} |  |  |")
    lines += ["", "## Change surfaces", "",
              "| ID | Category | Locator | Action | Risk | Confidence |",
              "|---|---|---|---|---|---|"]
    for edit in inv.get("edits") or []:
        lines.append(
            f"| {edit.get('id')} | {edit.get('category')} | `{_public_locator(edit.get('locator'))}` | "
            f"{engine.redact(edit.get('action') or '').replace('|', '/')} | {edit.get('risk')} | "
            f"{_confidence(edit.get('confidence'))}% |"
        )
    lines += ["", "## Probe ledger", "",
              "| Probe | Family | Status | Result |", "|---|---|---|---|"]
    for probe in inv.get("probes") or []:
        lines.append(
            f"| {probe.get('label') or probe.get('id')} | {probe.get('family') or ''} | "
            f"{probe.get('status')} | {engine.redact(probe.get('summary') or '').replace('|', '/')} |"
        )
    if inv.get("sources"):
        lines += ["", "## Sources", ""]
        for source in inv["sources"]:
            lines.append(f"- **{engine.redact(source.get('label'))}** — {engine.redact(source.get('locator'))}")
    lines += [
        "",
        "## Limit",
        "",
        "This appendix contains the recorded evidence, not a claim of complete coverage. "
        "A probe marked empty or error is a route for the next investigation.",
        "",
    ]
    return "\n".join(lines)


def _video_md(inv: dict, title: str, digest: str) -> str:
    findings = _story_findings(inv)
    strong = findings[:3]
    edits = _story_edits(inv)[:2]
    probes = [p for p in (inv.get("probes") or []) if p.get("status") in {"empty", "error"}]
    moves = (inv.get("next_moves") or [])[:3]
    target = _public_target(inv)
    summary = (inv.get("summary") or {}).get("verdict") or "The dossier records what was observed and what remains unknown."
    c = _summary_counts(inv)
    lines = [
        f"# Video shot script — {title}",
        "",
        f"- **Dossier:** `{inv.get('id')}`",
        f"- **Evidence hash:** `{digest}`",
        f"- **Target:** {target}",
        f"- **Evidence records:** {c['findings']} · **Change surfaces:** {c['edits']}",
        "",
        "> Record the proof before writing the edit. This is a shot plan built from the dossier, "
        "not a claim that a finished video exists. Use only footage of equipment you own or have "
        "written permission to film.",
        "",
        "## Editorial spine",
        "",
        f"**Cold open:** {summary}",
        "",
        "**Scope:** State the target, the authorisation, the date and the limit. The viewer must "
        "know what was not tested.",
        "",
        "## Shot list",
        "",
        "| Time | Beat | Script / narration | Shot |",
        "|---|---|---|---|",
        f"| 0:00–0:18 | Cold open | Show the evidence card. Say: “This is {target}. We recorded "
        f"{c['findings']} findings and {c['edits']} change surfaces. Here is the seam, and here is "
        f"what we could not prove.” | Screen recording of the dossier and the hash card; no stock footage. |",
        "| 0:18–0:45 | Scope | Name the owner/permission, the scope note, the tools and the stop "
        "condition. | Close-up of the target label and the scope card. |",
    ]
    start = 45
    for index, finding in enumerate(strong):
        end = start + 42
        locator = finding.get("path") or finding.get("locator") or "recorded locator"
        lines.append(
            f"| {start // 60}:{start % 60:02d}–{end // 60}:{end % 60:02d} | Evidence {index + 1} · "
            f"{finding.get('id')} | **{engine.redact(finding.get('title'))}.** "
            f"{engine.redact(finding.get('detail') or '')} "
            f"Locator: `{_public_locator(locator)}`; confidence {_confidence(finding.get('confidence'))}%. | "
            "Highlight the exact evidence row, then cut to the artifact or screen. |"
        )
        start = end + 8
    if edits:
        for index, edit in enumerate(edits):
            end = start + 40
            lines.append(
                f"| {start // 60}:{start % 60:02d}–{end // 60}:{end % 60:02d} | Seam {index + 1} · "
                f"{edit.get('id')} | **{engine.redact(_public_locator(edit.get('locator')))}.** "
                f"{engine.redact(edit.get('action') or '')} Risk: {edit.get('risk')}; "
                f"confidence {_confidence(edit.get('confidence'))}%. | "
                "Annotate the seam; show the before/after only if it was actually tested. |"
            )
            start = end + 8
    lines += [
        f"| {start // 60}:{start % 60:02d}–{(start + 45) // 60}:{(start + 45) % 60:02d} | Proof and limit | "
        f"Show the dossier ID `{inv.get('id')}`, hash prefix `{digest[:16]}…`, the probe ledger and "
        f"the empty/error paths. Say what remains unknown. | Full-screen evidence table and a "
        "terminal or file-manager shot of the real artifact. |",
        f"| {(start + 45) // 60}:{(start + 45) % 60:02d}–END | Close | Offer the field guide and one "
        "fixed-scope paid teardown. No fake urgency, no promised outcome. | End card with the public "
        "links and the next evidence question. |",
        "",
        "## B-roll",
        "",
        "- Screen recording of the dossier opening on the finding ID.",
        "- Macro shot of the owned device or the local artifact, with serials covered.",
        "- The hash card generated for this publication.",
        "- The probe ladder, including one empty or error row.",
        "- A diagram of the seam, drawn from the edit-point locator.",
        "",
        "## Thumbnail copy",
        "",
        f"- `{target.upper()[:22]}: SHOW THE SEAM`",
        f"- `{c['findings']} FINDINGS · 1 SEAM`",
        "- `EVIDENCE, NOT HYPE`",
        "",
        "## Title options",
        "",
        f"- {target}: the evidence, the seam and the limits",
        f"- What {c['findings']} evidence records revealed about {target}",
        f"- The seam in {target} — a documented teardown",
        "",
        "## Description",
        "",
        f"{title}",
        "",
        f"Built from REA dossier {inv.get('id')} ({digest[:16]}…). "
        f"{summary}",
        "",
        "Chapters",
        "00:00 Cold open",
        "00:18 Scope and evidence",
        "00:45 Evidence",
        "04:00 The seam",
        "06:00 Proof and limit",
        "07:00 Close",
        "",
        "Every claim in this video is tied to a finding ID, a locator and a confidence in the "
        "evidence appendix. Probes that returned nothing are listed as limits, not hidden.",
        "",
        f"Optional £5 donation: {media.STRIPE['donation_link']}",
        "Monthly field notes: opt in separately; the donation does not add you to the list.",
        "",
        "Scope: owned or written-permission equipment only. No exploits, stolen data or control bypass.",
        "",
    ]
    if probes:
        lines += ["## Empty / error probes to mention", ""]
        for probe in probes:
            lines.append(f"- **{probe.get('label') or probe.get('id')}** — {probe.get('summary') or probe.get('status')}")
        lines.append("")
    if moves:
        lines += ["## Next evidence questions", ""]
        for move in moves:
            lines.append(f"- {engine.redact(move.get('move'))} _(because {engine.redact(move.get('because'))})_")
        lines.append("")
    lines += [
        "## Production rule",
        "",
        "Do not add a claim, a device, a screenshot or a result that is not in the dossier. If the "
        "edit needs a new claim, run another investigation first.",
        "",
    ]
    return "\n".join(lines)


def _story_html(inv: dict, title: str, digest: str, slug: str) -> str:
    c = _summary_counts(inv)
    summary = inv.get("summary") or {}
    findings = _story_findings(inv)
    edits = _story_edits(inv)
    probes = inv.get("probes") or []
    findings_html = []
    for finding in findings:
        locator = finding.get("path") or finding.get("locator") or ""
        if finding.get("line"):
            locator += f":{finding['line']}"
        findings_html.append(
            '<div class="proof"><b>' + _escape(finding.get("title")) +
            ' <span class="tag">' + _escape(finding.get("id")) + '</span></b>' +
            (f'<p class="small">{_escape(engine.redact(finding.get("detail")))}</p>'
             if finding.get("detail") else "") +
            f'<small>{_escape(_public_locator(locator))} · confidence '
            f'{_confidence(finding.get("confidence"))}%</small></div>'
        )
    edits_html = []
    for edit in edits:
        edits_html.append(
            '<tr><td><code>' + _escape(edit.get("id")) + '</code></td>' +
            '<td><code>' + _escape(_public_locator(edit.get("locator"))) + '</code><br>' +
            _escape(engine.redact(edit.get("action"))) + '</td>' +
            '<td><span class="tag ' + _escape(edit.get("risk")) + '">' +
            _escape(edit.get("risk")) + '</span><br><span class="small">' +
            f'{_confidence(edit.get("confidence"))}%</span></td></tr>'
        )
    probe_rows = "".join(
        '<tr><td>' + _escape(probe.get("label") or probe.get("id")) + '</td><td>' +
        _escape(probe.get("status")) + '</td><td>' +
        _escape(engine.redact(probe.get("summary") or "")) + '</td></tr>'
        for probe in probes
    )
    limits = [p for p in probes if p.get("status") in {"empty", "error"}]
    limit_html = "".join(
        '<li><b>' + _escape(p.get("label") or p.get("id")) + '</b> — ' +
        _escape(engine.redact(p.get("summary") or p.get("status"))) + '</li>'
        for p in limits
    ) or "<li>No empty or error probe was recorded. The evidence still has a scope.</li>"
    moves_html = "".join(
        '<li>' + _escape(engine.redact(move.get("move"))) +
        ' <span class="small">because ' + _escape(engine.redact(move.get("because"))) + '</span></li>'
        for move in (inv.get("next_moves") or [])[:5]
    ) or "<li>No next move recorded.</li>"
    sources_html = "".join(
        '<li><b>' + _escape(engine.redact(source.get("label"))) + '</b> — ' +
        _escape(engine.redact(source.get("locator"))) + '</li>'
        for source in (inv.get("sources") or [])
    )
    sources = f'<h2>Sources learned from</h2><ul>{sources_html}</ul>' if sources_html else ""
    stripe = media.STRIPE
    return f"""<!doctype html>
<html lang="en-GB"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_escape(title)} — The Teardown Room</title>
<meta name="description" content="{_escape(summary.get('verdict') or title)}">
<meta property="og:title" content="{_escape(title)}">
<meta property="og:description" content="{_escape(summary.get('verdict') or title)}">
<meta property="og:image" content="card.svg">
<link rel="stylesheet" href="../../assets/site.css"></head>
<body><div class="wrap">
<header class="top"><div class="brand"><b>THE TEARDOWN ROOM</b><span>Evidence file · The Teardown Room</span></div>
<nav class="nav"><a href="../../evidence/">Evidence library</a><a href="../../facts/">Fact files</a>
<a href="../../guides/">Guides</a><a href="../../proof/">Proof</a><a href="../../newsletter/">Field notes</a><a href="video-script.md">Video script</a>
<a href="evidence.md">Appendix</a></nav></header>
<main>
<span class="eyebrow">Evidence file · {_escape(summary.get('kind_label') or inv.get('kind'))}</span>
<h1>{_escape(title)}</h1>
<p class="dek">{_escape(summary.get('verdict') or 'A documented teardown with stated limits.')}</p>
<div class="meta">
<span class="chip">dossier <b>{_escape(inv.get('id'))}</b></span>
<span class="chip">evidence <b>{c['findings']}</b></span>
<span class="chip">seams <b>{c['edits']}</b></span>
<span class="chip">confidence <b>{_confidence(summary.get('confidence'))}%</b></span>
<span class="chip">scope <b>authorised</b></span>
</div>
<div class="note"><b>Publication gate:</b> this file was built from a completed, authorised
dossier. Fixture and demo dossiers are refused. The original dossier hash is
<span class="hash">{_escape(digest)}</span>.</div>

<h2>What the evidence shows</h2>
<p>{_escape(summary.get('verdict') or 'The dossier records the observations below.')}</p>
<p class="small">Selected production evidence is shown below. The full record, including test-only
observations and duplicates, is in the <a href="evidence.md">evidence appendix</a>.</p>
{''.join(findings_html)}

<h2>Where the system can change</h2>
<p class="small">A change surface is a place where behaviour can be altered or repaired. It is not
a claim that a change was made.</p>
<table><thead><tr><th>ID</th><th>Locator / action</th><th>Risk</th></tr></thead>
<tbody>{''.join(edits_html)}</tbody></table>

<h2>What the evidence does not establish</h2>
<ul>{limit_html}</ul>
<h3>Next evidence questions</h3><ol>{moves_html}</ol>

<h2>Scope and method</h2>
<div class="card"><div class="grid">
<div><h3>Target</h3><p>{_escape(_public_target(inv))}</p>
<h3>Scope</h3><p>{_escape(engine.redact(inv.get('scope') or 'Not stated in the dossier.'))}</p></div>
<div><h3>Authorisation</h3><p>Operator attested ownership or written permission. Passive,
non-destructive probing only.</p><h3>Finished</h3><p>{_escape(inv.get('finished') or inv.get('created') or '—')}</p></div>
</div><h3>Probe ledger</h3>
<table><thead><tr><th>Probe</th><th>Status</th><th>Recorded result</th></tr></thead>
<tbody>{probe_rows}</tbody></table></div>
{sources}

<h2>Watch the teardown</h2>
<p>The video script is a paper edit built from this dossier: cold open, scope, evidence, seam,
proof, limits and close. It is not a rendered video. Use the shots that are actually available.</p>
<a class="btn" href="video-script.md">Read the shot script</a>
<a class="btn" href="evidence.md">Open the evidence appendix</a>
<a class="btn" href="card.svg">Open the evidence card</a>

<h2>Go deeper</h2>
<p>If this evidence file is useful, you can leave an optional one-time £5 donation or request the
monthly field notes. There is no paid pack and no subscription. The donation buys no deliverable.</p>
<a class="btn gold" href="{_escape(stripe['donation_link'])}" rel="noreferrer">Leave a £5 donation</a>
<a class="btn" href="../../newsletter/">Request monthly field notes</a>
<p class="small">No result or income is guaranteed. Read the evidence and the limits before
deciding whether the work is useful to you.</p>
</main>
<footer class="foot">Published from the REA Engine Room. Evidence IDs, locators, confidence and the
dossier hash are retained. Secret-shaped values are redacted before publication. This page is
generated from a local dossier; it is not an exploit write-up or an offer of unauthorised access.</footer>
</div></body></html>"""


def _site_index_html(pubs: list[dict]) -> str:
    stripe = media.STRIPE
    cards = ""
    for pub in pubs:
        cards += f"""<article class="card story-card">
<a href="publications/{_escape(pub['slug'])}/"><img src="publications/{_escape(pub['slug'])}/card.svg" alt="Evidence card for {_escape(pub['title'])}"></a>
<div><span class="eyebrow">{_escape(pub.get('kind_label') or 'Evidence file')}</span>
<h2><a href="publications/{_escape(pub['slug'])}/">{_escape(pub['title'])}</a></h2>
<p>{_escape(pub.get('verdict') or '')}</p>
<div class="meta"><span class="chip">{pub.get('findings', 0)} evidence</span>
<span class="chip">{pub.get('edits', 0)} seams</span>
<span class="chip">{_escape((pub.get('published_at') or '')[:10])}</span></div>
<a class="btn" href="publications/{_escape(pub['slug'])}/">Read the evidence file</a></div>
</article>"""
    if not cards:
        cards = """<div class="empty"><b>No evidence file has been published yet.</b>
<p>The studio refuses to publish a fixture, a demo or a thin dossier. A story appears here only
after a completed, authorised investigation passes the gate.</p></div>"""
    return f"""<!doctype html>
<html lang="en-GB"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Teardown Room — evidence-led teardowns</title>
<meta name="description" content="Evidence-led teardowns of systems we own or have written permission to inspect. Every claim has a finding ID, a locator and a stated limit.">
<link rel="stylesheet" href="assets/site.css"></head>
<body><div class="wrap">
<header class="top"><div class="brand"><b>THE TEARDOWN ROOM</b><span>Open the box. Map the system. Show the seam.</span></div>
<nav class="nav"><a href="index.html">Evidence files</a><a href="#method">Method</a>
<a href="newsletter/">Field notes</a><a href="{_escape(stripe['donation_link'])}" rel="noreferrer">Support · £5</a></nav></header>
<main>
<span class="eyebrow">Evidence-led reverse engineering</span>
<h1>Open the box. Map the system. Show the seam.</h1>
<p class="dek">Teardowns of devices, apps and workflows we own or have written permission to
inspect. Each file carries evidence records, confidence, a change surface and the limits of what
was actually tested. No exploits, stolen data or invented claims.</p>
<div class="meta"><span class="chip">published <b>{len(pubs)}</b></span>
<span class="chip">evidence <b>{sum(int(p.get('findings') or 0) for p in pubs)}</b></span>
<span class="chip">scope <b>authorised only</b></span></div>

<h2>Evidence files</h2>
{cards}

<h2 id="method">How a file gets published</h2>
<div class="card"><ol>
<li><b>Investigate a real target.</b> The operator attests ownership or written permission and the
dossier records the scope.</li>
<li><b>Pass the gate.</b> The dossier must be complete, non-synthetic, with at least three evidence
records and one mapped change surface.</li>
<li><b>Package the evidence.</b> The studio generates the story, evidence appendix, hash, video shot
script and evidence card from the dossier.</li>
<li><b>State the limits.</b> Empty and error probes stay in the page. A block is a finding, not a
challenge.</li>
<li><b>Share after proof.</b> The optional donation and field-note signup are offered next to the
evidence, not instead of it.</li>
</ol></div>

<h2>Go deeper</h2>
<p>There is no paid pack. The optional £5 donation supports the next evidence file; it buys no
deliverable. The monthly field notes are a separate opt-in.</p>
<a class="btn gold" href="{_escape(stripe['donation_link'])}" rel="noreferrer">Leave a £5 donation</a>
<a class="btn" href="newsletter/">Request monthly field notes</a>
</main>
<footer class="foot">The Teardown Room publishes only authorised, non-destructive evidence work.
No page here claims a result that is not in its dossier. Secret-shaped values are redacted before
publication. The donation link is a public Payment Link; no secret key is stored in this site. The
field-note list is opt-in and is never published.</footer>
</div></body></html>"""


def _write_index(site_root: Path) -> None:
    pubs = _published_manifests(site_root)
    _write(site_root / "index.html", _site_index_html(pubs))


def _slug(inv: dict) -> str:
    base = f"{_public_target(inv)}-{str(inv.get('id') or 'dossier')[-6:]}"
    slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    return (slug or "evidence-file")[:72]


def build_publication(inv: dict, title: str = "",
                      site_root: str | Path | None = None) -> dict:
    gate = gate_for(inv)
    if not gate["ready"]:
        raise ValueError(f"publication blocked: {gate['reason']}")
    root = Path(site_root) if site_root else SITE_DIR
    title = _clean(title, 160) or _default_title(inv)
    digest = _digest(inv)
    slug = _slug(inv)
    out = root / "publications" / slug
    out.mkdir(parents=True, exist_ok=True)
    counts = _summary_counts(inv)
    published_at = _now()
    payload = {
        "slug": slug,
        "title": title,
        "target": _public_target(inv),
        "dossier_id": inv.get("id"),
        "kind": inv.get("kind"),
        "kind_label": (inv.get("summary") or {}).get("kind_label") or inv.get("kind"),
        "verdict": (inv.get("summary") or {}).get("verdict") or "",
        "findings": counts["findings"],
        "edits": counts["edits"],
        "probes": counts["probes"],
        "confidence": round(float((inv.get("summary") or {}).get("confidence") or 0), 2),
        "digest": digest,
        "published_at": published_at,
        "page_url": f"/published/publications/{slug}/",
        "evidence_url": f"/published/publications/{slug}/evidence.md",
        "video_url": f"/published/publications/{slug}/video-script.md",
        "card_url": f"/published/publications/{slug}/card.svg",
    }
    _write(out / "index.html", _story_html(inv, title, digest, slug))
    _write(out / "evidence.md", _evidence_md(inv, title, digest))
    _write(out / "video-script.md", _video_md(inv, title, digest))
    _write(out / "card.svg", _card_svg(title, payload["target"], counts["findings"],
                                       counts["edits"], digest))
    _write(out / "manifest.json", json.dumps(payload, ensure_ascii=False, indent=1))
    ensure_site(root, inv)
    return payload
