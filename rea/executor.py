#!/usr/bin/env python3
"""Execute one baselined Improve plan against a local git work tree.

The console never applies a change on its own. An operator presses Apply, the
plan must already have a recorded baseline, the target must be a clean local
git work tree, and the patch must come from the plan itself. The executor:

1. previews with ``git apply --check``;
2. applies the patch and records the ``changed`` state;
3. runs the plan's verification command, if one is recorded;
4. marks the plan ``verified`` only when that command exits zero;
5. reverses the patch and records ``rolled_back`` when it does not.

Every step is written to the plan history, and the reverse is available as an
explicit rollback action. Network targets and safety-critical classes are never
executed automatically.
"""

from __future__ import annotations

import hashlib
import subprocess
import threading
from pathlib import Path

import engine
import improvements

# Automatic execution is limited to local software and web work trees. Phones,
# TVs, vehicles, appliances, alarms, smart devices, networks and firmware stay
# human-applied: the class is a safety boundary, not an authorisation.
LOCAL_CLASSES = {"software", "web"}
DEFAULT_TIMEOUT = 120
MAX_OUTPUT = 2000
EXEC_LOCK = threading.RLock()


class ExecutionError(ValueError):
    """The plan cannot be executed as recorded."""


def _excerpt(value: object, limit: int = MAX_OUTPUT) -> str:
    text = engine.redact(str(value or "")).strip()
    return text[-limit:] if len(text) > limit else text


def _digest(patch: str) -> str:
    return hashlib.sha256(patch.encode("utf-8")).hexdigest()[:12]


def _git(root: Path, *args: str, timeout: int = DEFAULT_TIMEOUT,
         stdin: str | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            input=stdin, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
    except FileNotFoundError as exc:
        raise ExecutionError("git is not available on this machine") from exc
    except subprocess.TimeoutExpired as exc:
        raise ExecutionError("git timed out") from exc


def _shell(root: Path, command: str, timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            command, shell=True, cwd=str(root), capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        raise ExecutionError(f"verification timed out after {timeout}s")


def _patch_text(plan: dict) -> str:
    patch = str(plan.get("patch") or "")
    if not patch.strip():
        raise ExecutionError("record the exact patch in the plan before applying it")
    return patch


def _work_tree(plan: dict) -> Path:
    if str(plan.get("system_class") or "") not in LOCAL_CLASSES:
        raise ExecutionError(
            "automatic apply is limited to local software or web plans; "
            "this class stays a human-applied change")
    target = str(plan.get("target") or "").strip()
    if not target or "://" in target:
        raise ExecutionError("the target must be a local path, not a URL")
    path = Path(target).expanduser()
    if not path.exists():
        raise ExecutionError("the target path does not exist on this machine")
    start = path if path.is_dir() else path.parent
    top = _git(start, "rev-parse", "--show-toplevel")
    if top.returncode != 0 or not (top.stdout or "").strip():
        raise ExecutionError("automatic apply needs a local git work tree")
    return Path((top.stdout or "").strip()).resolve()


def _require_clean(root: Path) -> None:
    status = _git(root, "status", "--porcelain")
    if status.returncode != 0:
        raise ExecutionError("could not read the git status: " + _excerpt(status.stderr, 400))
    if (status.stdout or "").strip():
        raise ExecutionError(
            "the work tree has uncommitted changes; commit or stash them first so the reverse is exact")


def _files(patch: str) -> list[str]:
    files: list[str] = []
    for line in patch.splitlines():
        if line.startswith("+++ b/"):
            name = line[6:].strip()
            if name and name != "/dev/null" and name not in files:
                files.append(name)
    return files


def _fit(root: Path, patch: str, files: list[str]) -> str:
    """Match the patch's line endings to the work tree git will apply it to."""
    cr, lf = chr(13), chr(10)
    if not files:
        return patch.replace(cr + lf, lf)
    probe = _git(root, "ls-files", "--eol", "--", *files)
    out = probe.stdout or ""
    if "w/crlf" in out and "w/lf" not in out:
        return patch.replace(cr + lf, lf).replace(lf, cr + lf)
    return patch.replace(cr + lf, lf)


def _apply(root: Path, patch: str, *, check: bool = False, reverse: bool = False) -> subprocess.CompletedProcess:
    """Send the diff as bytes: text-mode stdin rewrites newlines on Windows."""
    args = ["git", "-C", str(root), "apply"]
    if check:
        args.append("--check")
    if reverse:
        args.append("--reverse")
    args.append("-")
    try:
        done = subprocess.run(args, input=patch.encode("utf-8"), capture_output=True, timeout=60)
    except FileNotFoundError as exc:
        raise ExecutionError("git is not available on this machine") from exc
    except subprocess.TimeoutExpired as exc:
        raise ExecutionError("git apply timed out") from exc
    return subprocess.CompletedProcess(
        done.args, done.returncode,
        (done.stdout or b"").decode("utf-8", "replace"),
        (done.stderr or b"").decode("utf-8", "replace"),
    )


def _current_head(root: Path) -> str:
    head = _git(root, "rev-parse", "HEAD")
    return (head.stdout or "").strip() if head.returncode == 0 else ""


def preview(plan: dict) -> dict:
    """Validate the recorded change without touching the work tree."""
    root = _work_tree(plan)
    patch = _patch_text(plan)
    _require_clean(root)
    files = _files(patch)
    fitted = _fit(root, patch, files)
    check = _apply(root, fitted, check=True)
    if check.returncode != 0:
        raise ExecutionError("the patch does not apply cleanly: " +
                             (_excerpt(check.stderr or check.stdout, 600) or "unknown git error"))
    return {
        "ok": True,
        "root": str(root),
        "head": _current_head(root),
        "files": files,
        "digest": _digest(patch),
        "verify_command": str(plan.get("verify_command") or ""),
        "note": f"Patch {_digest(patch)} applies cleanly to {len(files) or 'the recorded'} file(s).",
    }


def apply(plan_id: object) -> dict:
    """Apply a baselined plan, verify it, and reverse it automatically on failure."""
    with EXEC_LOCK:
        plan = improvements.get(plan_id)
        if plan is None:
            raise KeyError("plan not found")
        if str(plan.get("status") or "") != "baselined":
            raise ExecutionError("record the baseline before applying the change")
        root = _work_tree(plan)
        patch = _patch_text(plan)
        _require_clean(root)
        files = _files(patch)
        fitted = _fit(root, patch, files)
        check = _apply(root, fitted, check=True)
        if check.returncode != 0:
            raise ExecutionError("the patch does not apply cleanly: " +
                                 (_excerpt(check.stderr or check.stdout, 600) or "unknown git error"))
        head = _current_head(root)
        applied = _apply(root, fitted)
        if applied.returncode != 0:
            raise ExecutionError("git apply failed: " + _excerpt(applied.stderr, 600))
        plan = improvements.record_execution(
            plan_id,
            {
                "at": improvements._now(),
                "root": str(root),
                "head": head,
                "patch_digest": _digest(patch),
                "files": files,
                "verify_command": str(plan.get("verify_command") or ""),
                "verify_exit": None,
                "output": _excerpt(applied.stdout or applied.stderr),
                "applied": True,
                "reverted": False,
            },
            status="changed",
            note=f"Applied patch {_digest(patch)} to {len(files)} file(s) in {root}.",
        )
        verify = str(plan.get("verify_command") or "").strip()
        if not verify:
            plan["execution_note"] = ("Patch applied. No verification command was recorded; "
                                      "run the check and press Verify, or roll back.")
            return plan
        try:
            timeout = max(5, min(600, int(plan.get("verify_timeout") or DEFAULT_TIMEOUT)))
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT
        result = _shell(root, verify, timeout)
        output = _excerpt((result.stdout or "") + (result.stderr or ""))
        record = dict(improvements.get(plan_id).get("execution") or {})
        record.update({"verify_exit": result.returncode, "output": output,
                       "verify_at": improvements._now()})
        if result.returncode == 0:
            return improvements.record_execution(
                plan_id, record, status="verified",
                note=f"Verification passed (exit 0). {output[:300]}".strip())
        reverse = _apply(root, fitted, reverse=True)
        record["reverted"] = reverse.returncode == 0
        record["revert_output"] = _excerpt(reverse.stderr or reverse.stdout)
        note = (f"Verification failed (exit {result.returncode}); "
                + ("the patch was reversed automatically. " if record["reverted"]
                   else "automatic reversal failed, restore the baseline manually. ")
                + output[:300])
        return improvements.record_execution(plan_id, record, status="rolled_back", note=note.strip())


def rollback(plan_id: object) -> dict:
    """Reverse the patch recorded by the last apply."""
    with EXEC_LOCK:
        plan = improvements.get(plan_id)
        if plan is None:
            raise KeyError("plan not found")
        status = str(plan.get("status") or "")
        if status not in {"changed", "verified"}:
            raise ExecutionError("only an applied plan can be rolled back")
        record = dict(plan.get("execution") or {})
        if not record.get("applied") or record.get("reverted"):
            raise ExecutionError("no applied patch is recorded for this plan")
        root = _work_tree(plan)
        if record.get("root") and Path(record["root"]).resolve() != root:
            raise ExecutionError("the plan's work tree has moved; restore it manually")
        patch = _patch_text(plan)
        files = _files(patch)
        fitted = _fit(root, patch, files)
        check = _apply(root, fitted, check=True, reverse=True)
        if check.returncode != 0:
            raise ExecutionError(
                "the patch cannot be reversed cleanly; restore the baseline manually: " +
                (_excerpt(check.stderr or check.stdout, 600) or "unknown git error"))
        reversed_patch = _apply(root, fitted, reverse=True)
        if reversed_patch.returncode != 0:
            raise ExecutionError("git apply --reverse failed: " + _excerpt(reversed_patch.stderr, 600))
        record["reverted"] = True
        record["reverted_at"] = improvements._now()
        return improvements.record_execution(
            plan_id, record, status="rolled_back",
            note=f"Patch {record.get('patch_digest') or _digest(patch)} reversed; the baseline is restored.")


def eligible(plan: dict) -> bool:
    """What the console offers as a one-click execution."""
    return bool(
        str(plan.get("status") or "") == "baselined"
        and str(plan.get("system_class") or "") in LOCAL_CLASSES
        and str(plan.get("patch") or "").strip()
        and str(plan.get("target") or "").strip()
        and "://" not in str(plan.get("target") or "")
    )
