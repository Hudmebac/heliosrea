"""Crew-led mission planning for the local Engine Room.

The operator is the initiator. The crew chooses the lead, the lenses and the
route; it never grants authority. Planning is read-only, so no target is
contacted until the operator attests ownership and starts the investigation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import autonomy
import playbook

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # the delivery roster lives beside the package, not inside it
    import crew as helios_crew  # type: ignore
except Exception:  # pragma: no cover - the roster is optional
    helios_crew = None

KIND_LENSES = {
    "folder": ("reverse-engineer", "systems", "archaeologist"),
    "file": ("embedded-re", "reverse-engineer", "security-re"),
    "repo": ("archaeologist", "architect", "modernization"),
    "website": ("security-re", "protocol", "web-publisher"),
    "wifi": ("protocol", "systems", "security-re"),
    "internet": ("security-re", "protocol", "ux"),
    "har": ("protocol", "security-re", "systems"),
}

GUARDRAILS = (
    "Only start on a target you own or have written permission to assess.",
    "No credentials, authentication bypass, brute force, stealth or trace erasure.",
    "A blocked control is a finding, not a challenge to defeat.",
    "Planning is read-only; the run starts only after you confirm authorisation.",
)


def _profession(profession_id: str) -> dict | None:
    return next((item for item in playbook.PROFESSIONS
                 if item.get("id") == profession_id), None)


def _lenses(kind: str) -> list[dict]:
    out = []
    for profession_id in KIND_LENSES.get(kind, ("systems", "security-re")):
        person = _profession(profession_id)
        if not person:
            continue
        out.append({
            "id": person["id"],
            "name": person["name"],
            "focus": person["focus"],
            "deliverables": list(person["deliverables"]),
            "why": f"Reads the {kind} surface and names the reversible next move.",
        })
    return out


def _personas(goal: str, target: str, kind: str) -> list[dict]:
    if helios_crew is None:
        return []
    route = helios_crew.route_task({
        "title": goal or f"Map the {kind} target",
        "description": f"{kind} target: {target}",
        "status": "In Analysis",
    }, limit=3)
    out = []
    for item in route:
        person = helios_crew.get(item.get("persona_id"))
        view = helios_crew.public(person) or {}
        view.update({
            "fit": item.get("fit"),
            "confidence": item.get("confidence"),
            "reason": item.get("reason"),
            "skills": item.get("skills") or view.get("skills") or [],
        })
        out.append(view)
    return out


def roster() -> dict:
    """Return every crew member the local console can display."""
    personas = []
    principles = []
    if helios_crew is not None:
        personas = [helios_crew.public(person) for person in helios_crew.PERSONAS]
        principles = list(helios_crew.PRINCIPLES)
    professions = [dict(person) for person in playbook.PROFESSIONS]
    return {
        "personas": personas,
        "professions": professions,
        "principles": principles,
        "counts": {
            "personas": len(personas),
            "professions": len(professions),
            "crew": len(personas) + len(professions),
        },
    }


def _brief(goal: str, crew: dict, plan: dict, lenses: list[dict]) -> str:
    lead = crew.get("lead") or {}
    people = crew.get("personas") or []
    lines = [
        f"# Crew brief — {goal or plan['target']}",
        f"Target: {plan['target']} ({plan['kind_label']})",
        f"Route: {plan['summary']}",
        "Initiator: you (the operator)",
        f"Lead: {lead.get('name') or 'Mission Controller'}"
        + (f" — {lead.get('title')}" if lead.get("title") else ""),
        "",
        "Crew:",
    ]
    lines.extend(
        f"- {person.get('name')} — {person.get('reason') or person.get('purpose') or ''}".rstrip(" —")
        for person in people
    )
    if not people:
        lines.append("- Mission Controller — keeps the route and the gate honest")
    lines.append("")
    lines.append("Investigation lenses:")
    lines.extend(
        f"- {lens['name']} — {lens['focus']}" for lens in lenses
    )
    lines.extend([
        "",
        "Route:",
    ])
    for phase in plan.get("phases", []):
        lines.append(f"## {phase['title']}")
        for step in phase.get("steps", []):
            mark = "" if step.get("available", True) else " [BLOCKED]"
            lines.append(f"- {step['label']} (`{step['id']}`){mark}")
    lines.extend(["", "Stop conditions:"])
    lines.extend(f"- {item}" for item in plan.get("stop", []))
    lines.extend(["", "Guardrails:"])
    lines.extend(f"- {item}" for item in GUARDRAILS)
    return "\n".join(lines)


def plan(goal: str, target: str, kind: str = "", depth: str = "standard",
         sweep: bool = False, network: bool = True) -> dict:
    """Build a crew assignment around a read-only autonomy route."""
    goal = str(goal or "").strip()[:240]
    kind = str(kind or "").strip().lower()
    wanted = None if kind in {"", "auto"} else kind
    route = autonomy.build_plan(
        str(target or "").strip(), kind=wanted, depth=depth,
        network=network, sweep=sweep,
    )
    resolved_kind = route["kind"]
    lenses = _lenses(resolved_kind)
    people = _personas(goal, route["target"], resolved_kind)
    lead = None
    if helios_crew is not None:
        lead = helios_crew.public(helios_crew.get("mission-controller"))
    if lead:
        people = [person for person in people if person.get("id") != lead["id"]]
    else:
        lead = people.pop(0) if people else {
            "id": "mission-controller",
            "name": "Mission Controller",
            "title": "Mission control",
            "purpose": "Keeps the route, evidence and gate accountable.",
        }
    crew = {
        "lead": lead,
        "personas": people[:3],
        "lenses": lenses,
        "count": 1 + len(people[:3]) + len(lenses),
    }
    return {
        "goal": goal,
        "target": route["target"],
        "kind": resolved_kind,
        "plan": route,
        "crew": crew,
        "guardrails": list(GUARDRAILS),
        "prompt": _brief(goal, crew, route, lenses),
        "run": {
            **route.get("run", {}),
            "goal": goal,
            "requires_authorisation": True,
        },
    }
