#!/usr/bin/env python3
"""Crew decision: pick one move, draft the plan and advise the operator.

The crew does not guess at random. It runs the deterministic suggestion engine,
takes the highest-ranked move, and records why that move won. When the seam is
a simple Python entry point, it also drafts the exact patch and a read-only
verification command, so the plan can be executed and rolled back without the
operator writing the diff.

Every decision ends in advice, not a fait accompli: the plan is a draft, the
operator still approves the baseline, and the executor can reverse the patch.
"""

from __future__ import annotations

import ast
import difflib
import subprocess
from pathlib import Path

import executor
import improvements

try:  # the crew roster lives beside the package, not inside it
    import crew  # type: ignore
except Exception:  # pragma: no cover - the roster is optional
    crew = None

MAX_SOURCE = 200_000
LENSES = (
    ("architect", "Systems Architect",
     "Keep the change inside the mapped seam; no opportunistic extras."),
    ("qa-red-team", "Red Team",
     "Verify independently. A failed check reverses the patch automatically."),
    ("app-orchestrator", "App Orchestrator",
     "One reversible step, with the rollback recorded before execution."),
)


def _persona(persona_id: str, fallback: str) -> dict:
    person = None
    if crew is not None:
        try:
            person = crew.get(persona_id)
        except Exception:
            person = None
    guardrails = list((person or {}).get("guardrails") or [])
    return {
        "id": persona_id,
        "name": (person or {}).get("name") or fallback,
        "guardrail": guardrails[0] if guardrails else "",
    }


def lenses() -> list[dict]:
    out = []
    for persona_id, fallback, note in LENSES:
        person = _persona(persona_id, fallback)
        out.append({**person, "note": note})
    return out


def _work_tree(target: object) -> Path | None:
    path = Path(str(target or "")).expanduser()
    if not path.exists():
        return None
    start = path if path.is_dir() else path.parent
    try:
        done = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0 or not (done.stdout or "").strip():
        return None
    return Path((done.stdout or "").strip()).resolve()


def _is_main_guard(node: ast.If) -> bool:
    test = node.test
    if not isinstance(test, ast.Compare):
        return False
    sides = [test.left, *test.comparators]
    has_name = any(isinstance(item, ast.Name) and item.id == "__name__" for item in sides)
    has_main = any(isinstance(item, ast.Constant) and item.value == "__main__" for item in sides)
    return has_name and has_main


def guard_transform(text: str) -> tuple[str | None, int]:
    """Move bare top-level calls into main() and add an __main__ guard.

    Only expression calls are moved. Assignments, loops, conditionals, context
    managers and imports are left alone, because moving those could change
    behaviour that the verification command cannot see.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None, 0
    calls: list[ast.Expr] = []
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue  # module docstring
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(node, ast.If) and _is_main_guard(node):
            return None, 0
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            calls.append(node)
            continue
        return None, 0
    if not calls:
        return None, 0
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    remove: set[int] = set()
    for node in calls:
        for number in range(node.lineno, (node.end_lineno or node.lineno) + 1):
            remove.add(number)
    kept = [line for number, line in enumerate(lines, 1) if number not in remove]
    while kept and not kept[-1].strip():
        kept.pop()
    body: list[str] = []
    for node in calls:
        chunk = lines[node.lineno - 1:(node.end_lineno or node.lineno)]
        body.extend(("    " + line).rstrip() if line.strip() else "" for line in chunk)
    new_text = ("\n".join(kept).rstrip("\n") + "\n\n\ndef main() -> None:\n" +
                "\n".join(body) + '\n\n\nif __name__ == "__main__":\n    main()\n')
    try:
        ast.parse(new_text)
    except SyntaxError:
        return None, 0
    return new_text, len(calls)


def _diff(before: str, after: str, rel: str) -> str:
    diff = "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{rel}", tofile=f"b/{rel}", n=3))
    if diff and not diff.endswith("\n"):
        diff += "\n"
    return diff


def draft_patch(target: object, system_class: object, seam: dict | None) -> dict | None:
    """Draft a patch only for a seam the crew can change safely and check."""
    if str(system_class or "") not in executor.LOCAL_CLASSES:
        return None
    root = _work_tree(target)
    if root is None:
        return None
    locator = str((seam or {}).get("locator") or "")
    rel = locator.split(":")[0].strip()
    if not rel.lower().endswith(".py") or ".." in Path(rel).parts:
        return None
    file = (root / rel).resolve()
    try:
        file.relative_to(root)
    except ValueError:
        return None
    if not file.is_file():
        return None
    try:
        text = file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if len(text) > MAX_SOURCE:
        return None
    new_text, moved = guard_transform(text)
    if new_text is None:
        return None
    patch = _diff(text, new_text, rel)
    if not patch:
        return None
    return {
        "patch": patch,
        "verify_command": f'python -m py_compile "{rel}"',
        "files": [rel],
        "moved": moved,
        "summary": f"Wrap {moved} top-level call(s) in main() and add an __main__ guard.",
    }


def _advice(chosen: dict, plan: dict, recipe: dict | None) -> str:
    lines = [
        f"The crew chose {chosen['title']} ({chosen['intervention_label']}) "
        f"for {chosen['system_label']}.",
        f"Why: {chosen['why']}",
        f"Plan: {plan['id']} · status {plan['status']}.",
    ]
    if recipe:
        lines.append(f"Patch: drafted automatically. {recipe['summary']}")
        lines.append(f"Verification command: {recipe['verify_command']}")
    else:
        lines.append(
            "Patch: no safe automatic recipe matched this seam. Paste a diff into the plan "
            "or make the change by hand; the baseline and rollback still apply.")
    lines.append(f"Rollback: {chosen['rollback']}")
    lines.append(
        "The patch is reversed automatically if verification fails, and an applied plan "
        "can be rolled back at any time.")
    return "\n\n".join(lines)


def decide(dossier_id: object, goal: object, system_class: object,
           owner: object = "", path: Path | None = None) -> dict:
    """Choose one suggestion, draft its plan and explain the call."""
    result = improvements.suggest(dossier_id, goal, system_class, limit=4, path=path)
    suggestions = result.get("suggestions") or []
    if not suggestions:
        return {
            "advice": "No unblocked seam in this dossier could support a decision. "
                      "Record more evidence or choose another dossier.",
            "suggestion": None, "plan": None, "recipe": None, "lenses": lenses(),
        }
    chosen = suggestions[0]
    recipe = draft_patch(chosen.get("target"), chosen.get("system_class"), chosen.get("seam"))
    plan, suggestion = improvements.approve_suggestion(
        chosen["id"], owner or "Crew recommendation",
        patch=(recipe or {}).get("patch") or "",
        verify_command=(recipe or {}).get("verify_command") or "",
        path=path,
    )
    advice = _advice(chosen, plan, recipe)
    decision = {
        "at": improvements._now(),
        "goal": str(goal or ""),
        "suggestion_id": suggestion["id"],
        "plan_id": plan["id"],
        "title": chosen["title"],
        "intervention": chosen["intervention"],
        "intervention_label": chosen["intervention_label"],
        "system_class": chosen["system_class"],
        "why": chosen["why"],
        "risk": chosen.get("risk"),
        "confidence": chosen.get("confidence"),
        "recipe": recipe,
        "advice": advice,
    }
    plan = improvements.set_decision(plan["id"], decision, path=path)
    return {
        "advice": advice,
        "suggestion": suggestion,
        "plan": plan,
        "recipe": recipe,
        "lenses": lenses(),
        "note": "Crew decision recorded. The plan is a draft and nothing has been changed.",
    }
