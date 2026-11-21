"""REA autonomy planner.

Turns a target, an authorised range stage and a depth into a concrete route:
ordered probe phases, the reason for each step, the fallback when a probe is
empty, and the stop conditions that end the run.

Planning is read-only. Nothing is contacted or opened until the operator
attests ownership/permission and starts the investigation API.
"""

from __future__ import annotations

import engine
import playbook

DEPTHS = ("quick", "standard", "deep")
DEPTH_LABEL = {"quick": "Quick", "standard": "Standard", "deep": "Deep"}
PHASE_ORDER = ("Map the surface", "Read the artifact", "Map the protocol", "Map the edge")
PHASE_BY_FAMILY = {
    "Surface": "Map the surface",
    "Code": "Map the surface",
    "Docs": "Map the surface",
    "History": "Map the surface",
    "Sources": "Map the surface",
    "Verification": "Map the surface",
    "Binary": "Read the artifact",
    "Controls": "Read the artifact",
    "Firmware": "Read the artifact",
    "Traffic": "Read the artifact",
    "Protocol": "Map the protocol",
    "Web": "Map the protocol",
    "Network": "Map the edge",
    "Internet": "Map the edge",
}
WHY = {
    "Surface": "Boundary first: inventory the artifact and its entry points.",
    "Code": "Recover call structure, ownership and the seams that can change.",
    "Docs": "Recover intended behaviour, compatibility and operational assumptions.",
    "History": "Use commit history to find changes, reversions and hidden intent.",
    "Sources": "Cross-check the artifact against a primary source you can cite.",
    "Verification": "Find the proof surface that says whether a change works.",
    "Binary": "Read structure without executing the target.",
    "Controls": "Map the control boundary; a blocked control is evidence, not a challenge.",
    "Firmware": "Recover the update format, container and root filesystem layout.",
    "Traffic": "Read requests, schemas and TLS metadata without decrypting content.",
    "Protocol": "Map requests, schemas and state transitions from the surface you have.",
    "Web": "Map routes, technology and the client/server boundary.",
    "Network": "Map local exposure without a port scan.",
    "Internet": "Map the public edge, certificate identity and HTTP policy.",
}
DEFAULT_STOP = (
    "Target is not owned by you or not covered by written permission.",
    "A control, signature or cryptographic boundary blocks the next step; record it as a finding.",
    "A change needs a recovery path or a maintenance window you do not have.",
    "A step would require authentication bypass, credential replay, brute force or stealth.",
    "The evidence stops being reproducible; stop and narrow the scope.",
)
KIND_LABEL = {
    "folder": "local folder",
    "file": "local artifact",
    "repo": "GitHub repo",
    "website": "website",
    "wifi": "WiFi & LAN",
    "internet": "internet exposure",
    "har": "captured traffic",
}


def _range_stage(stage_id: str) -> dict | None:
    wanted = str(stage_id or "").strip()
    if not wanted:
        return None
    return next((stage for stage in playbook.RANGE if stage["id"] == wanted), None)


def _resolve(target: str, kind: str | None, stage: dict | None) -> tuple[str, str, dict]:
    target = str(target or "").strip()
    requested = str(kind or "").strip().lower()
    if requested in {"", "auto"}:
        requested = ""
    if not target and stage:
        target = str((stage.get("start") or {}).get("target") or "").strip()
    if not target:
        raise ValueError("Add a target for the route. A stage alone does not identify one.")

    if requested in {"wifi", "internet"}:
        classified = engine._network_classification(target, requested)
        return target, requested, classified
    if requested:
        classified = engine.classify(target)
        if requested != classified["kind"] and not (
            requested == "repo" and classified["kind"] == "website"
        ):
            raise ValueError(
                f"target looks like a {classified['kind']}, not a {requested}"
            )
        return target, requested, classified
    classified = engine.classify(target)
    return target, classified["kind"], classified


def _base_queue(kind: str, sweep: bool) -> list[str]:
    if kind == "internet":
        return list(engine.ORDER.get(kind, []))
    if kind == "wifi":
        return ["net.local", "net.devices"] if sweep else ["net.local"]
    return list(engine.ORDER.get(kind, []))


def _deep_extra(kind: str, queue: list[str], sweep: bool) -> list[str]:
    seen = set(queue)
    extra = [
        pid for pid, meta in engine.PROBES.items()
        if kind in meta["kinds"] and pid not in seen
    ]
    if kind == "wifi" and not sweep:
        extra = [pid for pid in extra if pid != "net.devices"]
    extra.sort(key=lambda pid: (engine.PROBES[pid]["family"], pid))
    return extra


def _fallbacks(pid: str, kind: str) -> list[dict]:
    meta = engine.PROBES[pid]
    out: list[dict] = []
    for candidate in meta.get("empty_next") or ():
        next_meta = engine.PROBES.get(candidate)
        if next_meta and kind in next_meta["kinds"]:
            out.append({"id": candidate, "label": next_meta["label"]})
    return out


def build_plan(target: str, kind: str | None = None, stage_id: str = "",
               depth: str = "standard", network: bool = True,
               sweep: bool = False) -> dict:
    """Return a route. This function never probes, reads or writes the target."""
    depth = str(depth or "standard").strip().lower()
    if depth not in DEPTHS:
        raise ValueError("depth must be quick, standard or deep")
    stage = _range_stage(stage_id)
    target, resolved_kind, classified = _resolve(target, kind, stage)

    queue = _base_queue(resolved_kind, bool(sweep))
    if depth == "quick":
        queue = queue[:3]
    elif depth == "deep":
        queue.extend(_deep_extra(resolved_kind, queue, bool(sweep)))
    queue = list(dict.fromkeys(queue))

    steps: list[dict] = []
    for pid in queue:
        meta = engine.PROBES.get(pid)
        if not meta or resolved_kind not in meta["kinds"]:
            continue
        blocked = ""
        if meta["network"] and not network:
            blocked = "Network probes are disabled for this route."
        steps.append({
            "id": pid,
            "label": meta["label"],
            "family": meta["family"],
            "description": meta["description"],
            "why": WHY.get(meta["family"], "Read the next piece of evidence."),
            "network": bool(meta["network"]),
            "available": not blocked,
            "blocked_reason": blocked,
            "if_empty": _fallbacks(pid, resolved_kind),
        })

    phases: list[dict] = []
    for title in PHASE_ORDER:
        phase_steps = [step for step in steps
                       if PHASE_BY_FAMILY.get(step["family"], "Map the surface") == title]
        if phase_steps:
            phases.append({
                "title": title,
                "steps": phase_steps,
                "count": len(phase_steps),
                "network": sum(1 for step in phase_steps if step["network"]),
            })

    stop = list((stage or {}).get("stop") or DEFAULT_STOP)
    if not network:
        stop.insert(0, "Network probes are disabled; only local evidence is in scope.")
    if resolved_kind == "wifi" and not sweep:
        stop.insert(0, "LAN device sweep is off; net.devices is not on this route.")
    if stage and (stage.get("start") or {}).get("kind") != resolved_kind:
        stop.insert(0, "The selected stage starts from a different target kind; this is a custom route.")

    auth = (stage or {}).get("authorisation") or (
        "Owned system or written permission. Non-destructive mapping only; "
        "no authentication bypass, brute force, credential replay or stealth."
    )
    success = (stage or {}).get("success") or (
        "A reproducible dossier: evidence, change surfaces, sources and next moves."
    )
    network_count = sum(1 for step in steps if step["network"])
    local_count = len(steps) - network_count
    notes = [
        "The plan is read-only. Nothing runs until you attest authorisation and press Run route.",
        "A blocked control is a finding. It is not a challenge to defeat.",
    ]
    if stage:
        notes.append(
            f"Stage {stage['n']} — {stage['title']}: use its evidence and stop conditions as the contract."
        )
    if not network and network_count:
        notes.append(
            f"{network_count} network step(s) are shown but will be skipped in this route."
        )

    return {
        "target": target,
        "kind": resolved_kind,
        "kind_label": KIND_LABEL.get(resolved_kind, resolved_kind),
        "url": classified.get("url") or "",
        "repo": classified.get("repo") or "",
        "local_path": classified.get("local_path") or "",
        "stage": ({
            "id": stage["id"],
            "n": stage["n"],
            "title": stage["title"],
            "level": stage["level"],
            "authorisation": stage["authorisation"],
            "success": stage["success"],
        } if stage else None),
        "depth": depth,
        "depth_label": DEPTH_LABEL[depth],
        "network": bool(network),
        "sweep": bool(sweep),
        "authorisation": auth,
        "success": success,
        "summary": (
            f"{len(steps)} probes · {len(phases)} phases · "
            f"{local_count} local / {network_count} network · {DEPTH_LABEL[depth]} depth"
        ),
        "counts": {
            "probes": len(steps),
            "phases": len(phases),
            "local": local_count,
            "network": network_count,
            "blocked": sum(1 for step in steps if not step["available"]),
            "families": len({step["family"] for step in steps}),
        },
        "phases": phases,
        "stop": stop,
        "notes": notes,
        "created": engine.now_iso(),
        "run": {
            "endpoint": "/api/investigations",
            "requires_authorisation": True,
            "payload": {
                "target": target,
                "kind": resolved_kind,
                "stage": (stage or {}).get("id", ""),
                "depth": depth,
                "network": bool(network),
                "sweep": bool(sweep),
            },
        },
    }


def plan_text(plan: dict) -> str:
    """Plain-text route, used by the Copy route action and tests."""
    lines = [
        f"# REA autonomy route — {plan['target']}",
        f"Kind: {plan['kind_label']} · Depth: {plan['depth_label']}",
        f"Authorisation: {plan['authorisation']}",
        f"Summary: {plan['summary']}",
        "",
    ]
    for phase in plan["phases"]:
        lines.append(f"## {phase['title']}")
        for step in phase["steps"]:
            mark = "" if step["available"] else f" [BLOCKED: {step['blocked_reason']}]"
            lines.append(f"- {step['label']} (`{step['id']}`){mark}")
            lines.append(f"  Why: {step['why']}")
            if step["if_empty"]:
                lines.append("  If empty: " + ", ".join(item["id"] for item in step["if_empty"]))
        lines.append("")
    lines.append("## Stop conditions")
    lines.extend(f"- {item}" for item in plan["stop"])
    lines.append("")
    lines.append("## Notes")
    lines.extend(f"- {item}" for item in plan["notes"])
    return "\n".join(lines)
