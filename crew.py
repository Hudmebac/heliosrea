#!/usr/bin/env python3
"""Helios crew: accountable personas, skills and deterministic task routing.

A persona is a lens and a delivery contract. It never gets extra authority:
only spending requires operator approval; everything else is actioned and reported.
"""

from __future__ import annotations

import re

PERSONAS = (
    {
        "id": "mission-controller",
        "name": "Mission Controller",
        "title": "Mission control",
        "glyph": "◈",
        "accent": "#f6ca70",
        "stage": "Command",
        "purpose": "Turns intent into a gated mission, chooses the next move and protects scope.",
        "skills": (
            "Mission framing", "Priority arbitration", "Dependency gating",
            "Decision logs", "Escalation",
        ),
        "deliverables": ("Mission charter", "Ranked next move", "Gate decision", "Escalation note"),
        "guardrails": (
            "Never mark work done without evidence.",
            "Treat spend as the only hard gate; act and advise on publish and delete.",
            "Keep one accountable owner for each mission.",
        ),
        "handoff": "Hands the approved scope to the Project Manager and the delivery lead.",
        "keywords": (
            "mission", "goal", "priority", "strategy", "scope", "decision",
            "commander", "vision", "north star", "outcome",
        ),
    },
    {
        "id": "architect",
        "name": "Systems Architect",
        "title": "Architecture",
        "glyph": "⌬",
        "accent": "#62e8ff",
        "stage": "Shape",
        "purpose": "Sets system boundaries, data contracts and the simplest viable technical path.",
        "skills": (
            "System design", "Data contracts", "Integration design",
            "Trade-off analysis", "Technical risk",
        ),
        "deliverables": ("Architecture note", "Component map", "Interface contract", "Decision record"),
        "guardrails": (
            "No architecture astronautics; name the simplest path.",
            "State the failure modes of the chosen design.",
            "Do not approve an unverified dependency.",
        ),
        "handoff": "Hands component contracts to the App Orchestrator and UI Engineer.",
        "keywords": (
            "architecture", "api", "schema", "data model", "system", "integration",
            "backend", "contract", "database", "technical", "stack", "service",
        ),
    },
    {
        "id": "product-designer",
        "name": "Product Designer",
        "title": "Product & UX",
        "glyph": "◇",
        "accent": "#b6a2ff",
        "stage": "Shape",
        "purpose": "Shapes the user problem, flow and information architecture before pixels.",
        "skills": (
            "User journeys", "Information architecture", "Interaction design",
            "Accessibility", "Usability testing",
        ),
        "deliverables": ("Problem frame", "User flow", "Wireframe", "Usability check"),
        "guardrails": (
            "Design for the real user, not the demo.",
            "Accessibility is part of done.",
            "Do not invent research or user quotes.",
        ),
        "handoff": "Hands the flow to the UI Engineer and acceptance criteria to Red Team.",
        "keywords": (
            "design", "ux", "user", "flow", "journey", "wireframe", "prototype",
            "experience", "usability", "accessibility", "screen", "form",
        ),
    },
    {
        "id": "ui-engineer",
        "name": "UI Engineer",
        "title": "Interface",
        "glyph": "▤",
        "accent": "#81f0ad",
        "stage": "Build",
        "purpose": "Builds the interface, states and responsive behaviour that make the design real.",
        "skills": (
            "Frontend implementation", "Component states", "Responsive layout",
            "Design systems", "Browser verification",
        ),
        "deliverables": ("Working UI", "State matrix", "Responsive check", "Browser evidence"),
        "guardrails": (
            "No fake loading or success states.",
            "Check empty, loading, error and long-content states.",
            "Keep the browser console clean.",
        ),
        "handoff": "Hands a working build and browser evidence to Red Team.",
        "keywords": (
            "ui", "frontend", "css", "html", "javascript", "component", "responsive",
            "layout", "browser", "interface", "visual", "page", "screen",
        ),
    },
    {
        "id": "project-manager",
        "name": "Project Manager",
        "title": "Delivery",
        "glyph": "▦",
        "accent": "#8ed2ff",
        "stage": "Plan",
        "purpose": "Turns a mission into a sequenced plan with owners, dependencies and a cadence.",
        "skills": (
            "Work breakdown", "Dependency mapping", "Scheduling",
            "Risk tracking", "Status reporting",
        ),
        "deliverables": ("Delivery plan", "Dependency map", "Weekly status", "Blocker list"),
        "guardrails": (
            "No task without a testable outcome.",
            "Surface blockers before they become surprises.",
            "Keep status evidence-based.",
        ),
        "handoff": "Hands the sequenced plan to the delivery crew and reports to the Mission Controller.",
        "keywords": (
            "plan", "milestone", "schedule", "deadline", "dependency", "delivery",
            "project", "manage", "sequence", "risk", "status", "coordinate",
        ),
    },
    {
        "id": "secretary",
        "name": "Secretary",
        "title": "Chief of Staff",
        "glyph": "✎",
        "accent": "#ffc2a1",
        "stage": "Command",
        "purpose": "Keeps the operator's memory, decisions and follow-ups from slipping.",
        "skills": (
            "Note hygiene", "Decision capture", "Meeting briefs",
            "Follow-up tracking", "Inbox triage",
        ),
        "deliverables": ("Decision note", "Daily brief", "Follow-up list", "Meeting pack"),
        "guardrails": (
            "Separate what was said from what was decided.",
            "Do not invent commitments.",
            "Respect privacy and the approval boundary.",
        ),
        "handoff": "Publishes decisions and open loops to the Mission Controller and Project Manager.",
        "keywords": (
            "note", "minutes", "meeting", "follow up", "brief", "summarise",
            "summary", "admin", "inbox", "decision log", "remind", "secretary",
        ),
    },
    {
        "id": "app-orchestrator",
        "name": "App Orchestrator",
        "title": "Build orchestration",
        "glyph": "⌘",
        "accent": "#ffd166",
        "stage": "Build",
        "purpose": "Decomposes an app build, sequences modules and keeps integrations shippable.",
        "skills": (
            "Build decomposition", "Module sequencing", "Integration wiring",
            "Release slicing", "Environment hygiene",
        ),
        "deliverables": ("Build plan", "Module map", "Integration checklist", "Shippable slice"),
        "guardrails": (
            "Prefer a thin end-to-end slice over a pile of parts.",
            "No hidden network, account or hosting dependency.",
            "Verify each module before wiring the next.",
        ),
        "handoff": "Hands module specs to Architecture, UI and Red Team; keeps the build slice moving.",
        "keywords": (
            "app", "build", "orchestrate", "module", "feature", "ship", "launch",
            "integration", "prototype", "mvp", "workflow", "assemble",
        ),
    },
    {
        "id": "admin",
        "name": "Admin",
        "title": "Operations & governance",
        "glyph": "⚙",
        "accent": "#c8d7e3",
        "stage": "Operate",
        "purpose": "Keeps configuration, access, records and release hygiene clean and reversible.",
        "skills": (
            "Configuration control", "Access review", "Release checklist",
            "Audit hygiene", "Rollback planning",
        ),
        "deliverables": ("Config record", "Access check", "Release checklist", "Rollback note"),
        "guardrails": (
            "Least privilege, always.",
            "Never expose secrets or copy keys into notes.",
            "No release without an owner and a rollback path.",
        ),
        "handoff": "Confirms operational readiness to the Mission Controller before release.",
        "keywords": (
            "admin", "config", "permission", "access", "release", "deploy",
            "environment", "security", "audit", "backup", "rollback", "ops",
            "role", "roles", "governance", "reporting", "authority", "policy",
        ),
    },
    {
        "id": "research-analyst",
        "name": "Research Analyst",
        "title": "Evidence",
        "glyph": "✧",
        "accent": "#9fe7ff",
        "stage": "Discover",
        "purpose": "Finds primary evidence, separates fact from claim and records freshness.",
        "skills": (
            "Source discovery", "Evidence grading", "Triangulation",
            "Freshness checks", "Citation hygiene",
        ),
        "deliverables": ("Evidence log", "Source table", "Confidence note", "Gap list"),
        "guardrails": (
            "Primary sources over summaries.",
            "Record dates, uncertainty and missing evidence.",
            "Never turn correlation into causation.",
        ),
        "handoff": "Hands evidence and gaps to Architecture, Product Design or Content.",
        "keywords": (
            "research", "source", "evidence", "data", "analyse", "analyze",
            "study", "market", "competitor", "fact", "cite", "report",
        ),
    },
    {
        "id": "qa-red-team",
        "name": "Red Team",
        "title": "Quality & challenge",
        "glyph": "⌖",
        "accent": "#ff91a0",
        "stage": "Verify",
        "purpose": "Independently tries to break the work and checks the claim against the evidence.",
        "skills": (
            "Test design", "Adversarial review", "Edge cases",
            "Regression checks", "Evidence verification",
        ),
        "deliverables": ("Test plan", "Reproduction steps", "Pass/fail evidence", "Risk verdict"),
        "guardrails": (
            "Stay independent from the builder.",
            "No Done without evidence.",
            "Report failures plainly and never hide them.",
        ),
        "handoff": "Returns a verdict to the Mission Controller; blocks release on critical findings.",
        "keywords": (
            "test", "qa", "verify", "quality", "bug", "regression", "review",
            "red team", "edge case", "validate", "acceptance", "security",
        ),
    },
    {
        "id": "content-strategist",
        "name": "Content Strategist",
        "title": "Narrative & copy",
        "glyph": "✍",
        "accent": "#f3c7ff",
        "stage": "Shape",
        "purpose": "Makes the work clear, truthful and easy to act on across product and launch surfaces.",
        "skills": (
            "Message architecture", "Plain-language copy", "Content design",
            "Editorial review", "Launch narrative",
        ),
        "deliverables": ("Message map", "Page copy", "Microcopy", "Launch note"),
        "guardrails": (
            "No unsupported claims.",
            "Write for the reader's next action.",
            "Keep the tone consistent with Helios.",
        ),
        "handoff": "Hands approved copy to UI and launch evidence to Red Team.",
        "keywords": (
            "copy", "content", "write", "draft", "message", "launch", "blog",
            "docs", "documentation", "email", "narrative", "text",
        ),
    },
    {
        "id": "data-steward",
        "name": "Data Steward",
        "title": "Data & metrics",
        "glyph": "▥",
        "accent": "#8ee8d8",
        "stage": "Discover",
        "purpose": "Makes data trustworthy: definitions, quality checks and decision-ready metrics.",
        "skills": (
            "Metric definitions", "Data quality", "Schema stewardship",
            "Analysis", "Lineage",
        ),
        "deliverables": ("Metric dictionary", "Quality report", "Analysis note", "Lineage map"),
        "guardrails": (
            "Every metric has one definition.",
            "Show sample size, gaps and uncertainty.",
            "Do not launder uncertainty into a dashboard.",
        ),
        "handoff": "Hands trusted metrics to Research, Product and the Mission Controller.",
        "keywords": (
            "metric", "analytics", "data quality", "sql", "dataset", "dashboard",
            "kpi", "measure", "reporting", "lineage", "calculation",
        ),
    },
)

PERSONA_BY_ID = {persona["id"]: persona for persona in PERSONAS}

ALIASES = {
    "ceo agent": "mission-controller",
    "ceo": "mission-controller",
    "chief of staff": "secretary",
    "product agent": "product-designer",
    "build agent": "app-orchestrator",
    "red team": "qa-red-team",
    "research agent": "research-analyst",
    "data agent": "data-steward",
    "content agent": "content-strategist",
    "project manager": "project-manager",
    "architect": "architect",
    "designer": "product-designer",
    "ui": "ui-engineer",
    "admin": "admin",
    "secretary": "secretary",
}

MISSION_AFFINITY = {
    "App": ("app-orchestrator", "architect", "ui-engineer"),
    "Website": ("content-strategist", "ui-engineer", "product-designer"),
    "Research": ("research-analyst", "data-steward", "qa-red-team"),
    "Custom": (),
}

STATUS_AFFINITY = {
    "In Plan": ("project-manager", "architect", "product-designer", "mission-controller"),
    "In Analysis": ("research-analyst", "data-steward", "architect"),
    "In Progress": ("app-orchestrator", "ui-engineer", "project-manager"),
    "In Test": ("qa-red-team",),
}

PRINCIPLES = (
    "One accountable persona per task. Shared ownership is not ownership.",
    "Route on the next deliverable, not the loudest keyword.",
    "Red Team stays independent from the builder; the Mission Controller owns evidence and closure.",
    "Personas add judgement, not spending authority: only spend is operator-gated.",
)


def public(persona: dict | None, extra: dict | None = None) -> dict | None:
    if not persona:
        return None
    view = {
        key: persona.get(key)
        for key in (
            "id", "name", "title", "glyph", "accent", "stage", "purpose",
            "skills", "deliverables", "guardrails", "handoff",
        )
    }
    if extra:
        view.update(extra)
    return view


def get(persona_id: str | None) -> dict | None:
    return PERSONA_BY_ID.get(str(persona_id or "").strip())


def resolve(value: object) -> dict | None:
    """Resolve an id, display name, title or legacy agent label."""
    needle = str(value or "").strip().casefold()
    if not needle:
        return None
    if needle in PERSONA_BY_ID:
        return PERSONA_BY_ID[needle]
    if needle in ALIASES:
        return PERSONA_BY_ID.get(ALIASES[needle])
    for persona in PERSONAS:
        if needle in {str(persona["name"]).casefold(), str(persona["title"]).casefold()}:
            return persona
    return None


def _has_phrase(text: str, phrase: str) -> bool:
    return bool(re.search(r"\b" + re.escape(phrase.casefold()) + r"\b", text))


def route_task(task: dict, mission_type: str = "", limit: int = 3) -> list[dict]:
    """Return the best persona fits for a task with explicit reasons."""
    title = str(task.get("title") or "")
    text = " ".join(
        str(task.get(key) or "")
        for key in ("title", "description", "risks")
    ).casefold()
    status = str(task.get("status") or "In Plan")
    affinity = set(MISSION_AFFINITY.get(str(mission_type or task.get("mission_type") or ""), ()))
    status_affinity = set(STATUS_AFFINITY.get(status, ()))
    results = []
    for index, persona in enumerate(PERSONAS):
        score = 32
        reasons: list[str] = []
        matched = [word for word in persona["keywords"] if _has_phrase(text, word)]
        if matched:
            score += min(30, 6 + len(matched) * 5 + min(4, len(" ".join(matched)) // 12))
            reasons.append("matches " + ", ".join(f"“{word}”" for word in matched[:2]))
        if persona["id"] in affinity:
            score += 9
            reasons.append(f"{mission_type} mission fit")
        if persona["id"] in status_affinity:
            score += 8 if status != "In Test" else 22
            if status == "In Test":
                reasons.append("independent verification needed")
            else:
                reasons.append(f"{status.lower()} stage fit")
        if not reasons:
            if persona["id"] == "mission-controller":
                reasons.append("keeps the mission sequence and gate clean")
            elif persona["id"] == "project-manager":
                reasons.append("turns an ambiguous move into a sequenced deliverable")
            else:
                reasons.append("general delivery support")
        score = max(1, min(97, score))
        if title and _has_phrase(title.casefold(), str(persona["name"]).casefold()):
            score = min(99, score + 5)
        results.append({
            "persona_id": persona["id"],
            "name": persona["name"],
            "title": persona["title"],
            "glyph": persona["glyph"],
            "accent": persona["accent"],
            "fit": score,
            "confidence": round(min(0.97, 0.48 + score / 220), 2),
            "reason": " · ".join(reasons[:2]),
            "skills": list(persona["skills"][:3]),
            "_order": index,
        })
    results.sort(key=lambda item: (-item["fit"], item["_order"]))
    for item in results:
        item.pop("_order", None)
    return results[:max(1, limit)]


def for_task(task: dict, mission_type: str = "") -> dict | None:
    """Explicit assignment wins; otherwise route on the work, not the old label."""
    persona = resolve(task.get("persona"))
    if persona:
        return persona
    route = route_task(task, mission_type, limit=1)
    if route:
        return get(route[0]["persona_id"])
    return resolve(task.get("owner")) or PERSONAS[0]


def mission_crew(mission: dict, tasks: list[dict]) -> list[dict]:
    lead = resolve(mission.get("lead_persona")) or resolve(mission.get("owner"))
    counts: dict[str, dict] = {}
    for task in tasks:
        if str(task.get("mission_id") or "") != str(mission.get("id") or ""):
            continue
        persona = for_task(task, str(mission.get("type") or ""))
        if not persona:
            continue
        bucket = counts.setdefault(persona["id"], {"open": 0, "done": 0})
        if str(task.get("status") or "") == "Done":
            bucket["done"] += 1
        else:
            bucket["open"] += 1
    if lead:
        counts.setdefault(lead["id"], {"open": 0, "done": 0})
    ordered = sorted(
        counts.items(),
        key=lambda item: (
            item[0] != (lead or {}).get("id"),
            -(item[1]["open"] + item[1]["done"]),
            item[0],
        ),
    )
    crew = []
    for persona_id, bucket in ordered:
        persona = get(persona_id)
        if not persona:
            continue
        crew.append(public(persona, {
            "count": bucket["open"] + bucket["done"],
            "open": bucket["open"],
            "done": bucket["done"],
            "lead": bool(lead and lead["id"] == persona_id),
        }))
    return crew


def crew_payload(tasks: list[dict], missions: list[dict]) -> dict:
    mission_by_id = {str(mission.get("id")): mission for mission in missions}
    mission_titles = {
        str(mission.get("id")): str(mission.get("title") or "") for mission in missions
    }
    counts: dict[str, dict] = {}
    routes = []
    for task in tasks:
        mission = mission_by_id.get(str(task.get("mission_id") or ""), {})
        mission_type = str(mission.get("type") or "")
        persona = for_task(task, mission_type)
        if persona:
            bucket = counts.setdefault(persona["id"], {"total": 0, "open": 0, "done": 0, "task_ids": []})
            bucket["total"] += 1
            bucket["task_ids"].append(str(task.get("id") or ""))
            if str(task.get("status") or "") == "Done":
                bucket["done"] += 1
            else:
                bucket["open"] += 1
        if str(task.get("status") or "") == "Done":
            continue
        routes.append({
            "task_id": str(task.get("id") or ""),
            "title": str(task.get("title") or ""),
            "status": str(task.get("status") or "In Plan"),
            "priority": str(task.get("priority") or "Medium"),
            "mission_id": str(task.get("mission_id") or ""),
            "mission_title": mission_titles.get(str(task.get("mission_id") or ""), ""),
            "current_persona": public(persona),
            "route": route_task(task, mission_type, limit=3),
        })
    status_order = {"In Test": 0, "In Progress": 1, "In Analysis": 2, "In Plan": 3}
    priority_order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    routes.sort(key=lambda item: (
        status_order.get(item["status"], 9),
        priority_order.get(item["priority"], 9),
        item["task_id"],
    ))
    team = []
    for persona in PERSONAS:
        bucket = counts.get(persona["id"], {"total": 0, "open": 0, "done": 0, "task_ids": []})
        team.append(public(persona, dict(bucket)))
    active = sum(1 for task in tasks if str(task.get("status") or "") != "Done")
    return {
        "personas": team,
        "summary": {
            "roles": len(PERSONAS),
            "assignments": len(tasks),
            "active": active,
            "missions": len(missions),
            "routed": len(routes),
        },
        "allocations": team,
        "routes": routes[:12],
        "principles": list(PRINCIPLES),
    }


def brief_for(task: dict, persona: dict, mission: dict | None = None) -> dict:
    mission = mission or {}
    deliverable = persona["deliverables"][0]
    dependencies = [str(dep) for dep in (task.get("dependencies") or [])]
    risks = str(task.get("risks") or "").strip()
    status = str(task.get("status") or "In Plan")
    first_moves = [
        f"Confirm the outcome: {task.get('title') or 'deliver the task'}.",
        f"Produce the {deliverable.lower()} before widening scope.",
        f"Record the evidence, then hand off: {persona['handoff']}",
    ]
    if dependencies:
        first_moves.insert(0, f"Check dependencies first: {', '.join(dependencies)}.")
    definition = [
        f"The {deliverable.lower()} exists and can be inspected.",
        "Evidence is recorded on the task before it moves to In Test.",
        "Red Team verifies independently before the task moves to Done.",
    ]
    if risks:
        definition.append(f"Risk reviewed: {risks}")
    brief = {
        "persona_id": persona["id"],
        "persona": public(persona),
        "objective": str(task.get("title") or ""),
        "mission": str(mission.get("title") or ""),
        "status": status,
        "deliverable": deliverable,
        "definition_of_done": definition,
        "first_moves": first_moves,
        "guardrails": list(persona["guardrails"]),
        "handoff": persona["handoff"],
        "risks": risks,
        "dependencies": dependencies,
        "skills": list(persona["skills"]),
    }
    lines = [
        f"{persona['name']} · {persona['title']}",
        f"Objective: {brief['objective'] or 'Unnamed task'}",
        f"Mission: {brief['mission'] or 'No mission'} · status: {status}",
        f"Deliverable: {deliverable}",
        "First moves:",
        *[f"- {item}" for item in first_moves],
        "Definition of done:",
        *[f"- {item}" for item in definition],
        "Guardrails:",
        *[f"- {item}" for item in persona["guardrails"]],
    ]
    brief["text"] = "\n".join(lines)
    return brief
