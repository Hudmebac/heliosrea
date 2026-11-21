"""Local mailing list and monthly field-note store.

The store is a single JSON file under ``rea/data/`` (ignored by git). It holds
opted-in contacts, their preferences and the monthly issues. No email is sent
by this module: the owner reviews the list, copies the issue text and sends it
from the address configured in ``media.CONTACT_EMAIL``.

The public site never reads this file. Only the owner-only Mailroom API does.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import secrets
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import media

STORE_PATH = Path(__file__).resolve().parent / "data" / "mailroom.json"
LOCK = threading.RLock()

TOPICS = ("monthly", "evidence")
STATUSES = ("active", "paused", "unsubscribed")
ISSUE_STATUSES = ("draft", "ready", "sent")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _now() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def _path(path: str | Path | None = None) -> Path:
    return Path(path) if path else STORE_PATH


def _empty() -> dict:
    return {
        "version": 1,
        "owner_email": media.CONTACT_EMAIL,
        "contacts": [],
        "issues": [],
        "updated_at": "",
    }


def _clean(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def _normalise_email(value: object) -> str:
    email = str(value or "").strip().lower()
    if len(email) > 254 or not EMAIL_RE.fullmatch(email):
        raise ValueError("enter a valid email address")
    return email


def _normalise_topics(value: object) -> list[str]:
    if isinstance(value, str):
        raw = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        raw = [str(part).strip() for part in value]
    else:
        raw = []
    topics = [topic for topic in TOPICS if topic in raw]
    if not topics:
        raise ValueError("choose at least one update type")
    return topics


def _normalise_month(value: object) -> str:
    month = str(value or "").strip()
    if not MONTH_RE.fullmatch(month):
        raise ValueError("month must use YYYY-MM")
    return month


def _month_label(month: str) -> str:
    try:
        return datetime.strptime(month, "%Y-%m").strftime("%B %Y")
    except ValueError:
        return month


def _issue_id(month: str) -> str:
    return f"ISS-{month}"


def load(path: str | Path | None = None) -> dict:
    """Read the store; return an empty store when it does not exist yet."""
    target = _path(path)
    with LOCK:
        if not target.is_file():
            return _empty()
        try:
            data = json.loads(target.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"mailroom store is unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("mailroom store must be a JSON object")
    data.setdefault("version", 1)
    data.setdefault("owner_email", media.CONTACT_EMAIL)
    data.setdefault("contacts", [])
    data.setdefault("issues", [])
    data.setdefault("updated_at", "")
    if not isinstance(data["contacts"], list) or not isinstance(data["issues"], list):
        raise ValueError("mailroom store has invalid collections")
    return data


def save(data: dict, path: str | Path | None = None) -> None:
    target = _path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data["version"] = 1
    data["owner_email"] = media.CONTACT_EMAIL
    data["updated_at"] = _now()
    raw = json.dumps(data, ensure_ascii=False, indent=1)
    with LOCK:
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(raw, "utf-8")
        tmp.replace(target)


def _public_contact(contact: dict) -> dict:
    return {
        "id": contact.get("id"),
        "name": contact.get("name", ""),
        "status": contact.get("status", "active"),
        "topics": list(contact.get("topics") or []),
        "source": contact.get("source", ""),
        "donated": bool(contact.get("donated")),
        "created_at": contact.get("created_at", ""),
        "updated_at": contact.get("updated_at", ""),
    }


def _admin_contact(contact: dict) -> dict:
    item = _public_contact(contact)
    item["email"] = contact.get("email", "")
    item["notes"] = contact.get("notes", "")
    return item


def subscribe(
    email: object,
    name: object = "",
    topics: object = ("monthly",),
    source: object = "field-notes",
    donated: bool = False,
    consent: bool = False,
    path: str | Path | None = None,
) -> dict:
    """Create or refresh an opted-in contact. Consent must be explicit."""
    if consent is not True:
        raise ValueError("consent is required")
    address = _normalise_email(email)
    chosen = _normalise_topics(topics)
    data = load(path)
    now = _now()
    match = next((item for item in data["contacts"] if item.get("email") == address), None)
    if match:
        match.update({
            "name": _clean(name, 80),
            "status": "active",
            "topics": chosen,
            "source": _clean(source, 80) or "field-notes",
            "donated": bool(donated) or bool(match.get("donated")),
            "updated_at": now,
            "consent_at": now,
        })
        contact = match
    else:
        contact = {
            "id": f"CNT-{datetime.now(tz=timezone.utc):%Y%m%d}-{secrets.token_hex(3)}",
            "email": address,
            "name": _clean(name, 80),
            "status": "active",
            "topics": chosen,
            "source": _clean(source, 80) or "field-notes",
            "donated": bool(donated),
            "notes": "",
            "token": secrets.token_urlsafe(18),
            "created_at": now,
            "updated_at": now,
            "consent_at": now,
            "unsubscribed_at": "",
        }
        data["contacts"].append(contact)
    save(data, path)
    return _public_contact(contact)


def add_contact(
    email: object,
    name: object = "",
    topics: object = ("monthly",),
    source: object = "manual",
    donated: bool = False,
    note: object = "",
    path: str | Path | None = None,
) -> dict:
    """Owner entry. It still records an explicit consent timestamp for the audit trail."""
    contact = subscribe(email, name, topics, source, donated, consent=True, path=path)
    if note:
        set_status(contact["id"], "active", note=note, path=path)
    return contact


def list_contacts(
    query: object = "",
    status: object = "",
    path: str | Path | None = None,
) -> list[dict]:
    data = load(path)
    needle = str(query or "").strip().lower()
    wanted = str(status or "").strip().lower()
    rows = []
    for item in data["contacts"]:
        if wanted and item.get("status") != wanted:
            continue
        haystack = " ".join(str(item.get(key) or "") for key in ("email", "name", "source", "notes")).lower()
        if needle and needle not in haystack:
            continue
        rows.append(_admin_contact(item))
    rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return rows


def stats(path: str | Path | None = None) -> dict:
    data = load(path)
    counts = {status: 0 for status in STATUSES}
    for contact in data["contacts"]:
        status = str(contact.get("status") or "active")
        if status in counts:
            counts[status] += 1
    return {
        "total": len(data["contacts"]),
        "active": counts["active"],
        "paused": counts["paused"],
        "unsubscribed": counts["unsubscribed"],
        "issues": len(data["issues"]),
        "drafts": sum(1 for issue in data["issues"] if issue.get("status") == "draft"),
        "ready": sum(1 for issue in data["issues"] if issue.get("status") == "ready"),
        "sent": sum(1 for issue in data["issues"] if issue.get("status") == "sent"),
    }


def set_status(
    contact_id: object,
    status: object,
    note: object = "",
    path: str | Path | None = None,
) -> dict:
    wanted = str(status or "").strip().lower()
    if wanted not in STATUSES:
        raise ValueError("status must be active, paused or unsubscribed")
    data = load(path)
    contact = next((item for item in data["contacts"] if item.get("id") == contact_id), None)
    if not contact:
        raise KeyError("contact not found")
    contact["status"] = wanted
    contact["updated_at"] = _now()
    if note:
        contact["notes"] = _clean(note, 500)
    if wanted == "unsubscribed":
        contact["unsubscribed_at"] = _now()
    else:
        contact["unsubscribed_at"] = ""
    save(data, path)
    return _admin_contact(contact)


def export_csv(
    status: object = "",
    path: str | Path | None = None,
) -> str:
    rows = list_contacts("", status, path=path)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=[
        "id", "email", "name", "status", "topics", "source", "donated",
        "created_at", "updated_at", "notes",
    ])
    writer.writeheader()
    for row in rows:
        writer.writerow({**row, "topics": ", ".join(row.get("topics") or [])})
    return buffer.getvalue()


def _issue_public(issue: dict) -> dict:
    return {
        "id": issue.get("id"),
        "month": issue.get("month"),
        "title": issue.get("title"),
        "intro": issue.get("intro"),
        "sections": list(issue.get("sections") or []),
        "status": issue.get("status"),
        "created_at": issue.get("created_at"),
        "updated_at": issue.get("updated_at"),
        "sent_at": issue.get("sent_at", ""),
    }


def _default_sections(publication: dict | None, facts: list[dict] | None) -> list[dict]:
    facts = facts or []
    if not publication:
        return [
            {
                "h": "No new evidence file this month",
                "p": [
                    "The publication gate did not pass a new evidence file this month. "
                    "No finding is invented to fill the gap.",
                    "The evidence library and the method are still open. The next note will "
                    "arrive only when there is something real to add.",
                ],
            }
        ]
    target = str(publication.get("target") or "the recorded target")
    dossier = str(publication.get("dossier_id") or "")
    findings = int(publication.get("findings") or 0)
    seams = int(publication.get("edits") or 0)
    verdict = str(publication.get("verdict") or "").strip()
    sections = [
        {
            "h": "What was inspected",
            "p": [
                f"{target}. Dossier {dossier}. {findings} evidence records and {seams} "
                "mapped change surfaces were recorded.",
                verdict or "The public story states the scope and the limits.",
            ],
        }
    ]
    if facts:
        target_fact = next((fact for fact in facts if fact.get("group") == "Target facts"), facts[0])
        sections.append({
            "h": "One finding worth checking",
            "p": [
                f"{target_fact.get('code')} · {target_fact.get('title')}.",
                str(target_fact.get("summary") or "Open the fact file for the locator and confidence."),
            ],
        })
        seam = next((fact for fact in facts if fact.get("group") == "Change surfaces"), None)
        if seam:
            sections.append({
                "h": "One seam to watch",
                "p": [
                    f"{seam.get('code')} · {seam.get('title')}.",
                    "A seam is a place where behaviour can change. It is not a claim that a "
                    "change was made or that a repair is safe.",
                ],
            })
    sections.append({
        "h": "The limit",
        "p": [
            "This note is a pointer into the evidence, not a complete assessment. Read the "
            "fact file and the appendix before acting on a finding.",
        ],
    })
    return sections


def draft_issue(
    month: object,
    title: object = "",
    intro: object = "",
    publication: dict | None = None,
    facts: list[dict] | None = None,
    path: str | Path | None = None,
) -> dict:
    """Create or refresh one monthly draft from a real publication, or an honest empty note."""
    month = _normalise_month(month)
    data = load(path)
    existing = next((item for item in data["issues"] if item.get("month") == month), None)
    if existing and existing.get("status") == "sent":
        raise ValueError("this issue is already marked sent; create a new month instead")
    now = _now()
    sections = _default_sections(publication, facts)
    issue = existing or {
        "id": _issue_id(month),
        "month": month,
        "created_at": now,
        "sent_at": "",
    }
    issue.update({
        "title": _clean(title, 120) or f"Field notes — {_month_label(month)}",
        "intro": _clean(intro, 500) or (
            "One evidence detail, one seam and one honest limit. This is the monthly "
            "field note from The Teardown Room."
        ),
        "sections": sections,
        "status": "draft" if not existing else existing.get("status", "draft"),
        "updated_at": now,
    })
    if not existing:
        data["issues"].append(issue)
    save(data, path)
    return _issue_public(issue)


def set_issue_status(
    issue_id: object,
    status: object,
    path: str | Path | None = None,
) -> dict:
    wanted = str(status or "").strip().lower()
    if wanted not in ISSUE_STATUSES:
        raise ValueError("issue status must be draft, ready or sent")
    data = load(path)
    issue = next((item for item in data["issues"] if item.get("id") == issue_id), None)
    if not issue:
        raise KeyError("issue not found")
    issue["status"] = wanted
    issue["updated_at"] = _now()
    issue["sent_at"] = _now() if wanted == "sent" else ""
    save(data, path)
    return _issue_public(issue)


def find_issue(issue_id: object, path: str | Path | None = None) -> dict | None:
    data = load(path)
    issue = next((item for item in data["issues"] if item.get("id") == issue_id), None)
    return _issue_public(issue) if issue else None


def issue_markdown(issue: dict) -> str:
    lines = [
        f"# {issue.get('title')}",
        "",
        f"**{_month_label(str(issue.get('month') or ''))}**",
        "",
        str(issue.get("intro") or ""),
        "",
    ]
    for section in issue.get("sections") or []:
        lines += [f"## {section.get('h')}", ""]
        lines += [str(paragraph) for paragraph in section.get("p") or []]
        lines.append("")
    lines += [
        "---",
        "",
        "You are receiving this because you asked for field notes from The Teardown Room.",
        f"Reply to this email or contact {media.CONTACT_EMAIL} to unsubscribe.",
        "",
    ]
    return "\n".join(lines)


def public_issues(path: str | Path | None = None, limit: int = 12) -> list[dict]:
    data = load(path)
    rows = [
        _issue_public(issue) for issue in data["issues"]
        if issue.get("status") in {"ready", "sent"}
    ]
    rows.sort(key=lambda item: str(item.get("month") or ""), reverse=True)
    return rows[:limit]


def public_payload(path: str | Path | None = None) -> dict:
    issues = public_issues(path)
    return {
        "owner_email": media.CONTACT_EMAIL,
        "issues": issues,
        "count": len(issues),
        "note": (
            "No email is sent automatically by this local build. The owner reviews the list, "
            "copies the issue and sends it from the configured address."
        ),
    }


def payload(
    query: object = "",
    status: object = "",
    path: str | Path | None = None,
) -> dict:
    data = load(path)
    issues = [_issue_public(issue) for issue in data["issues"]]
    issues.sort(key=lambda item: str(item.get("month") or ""), reverse=True)
    return {
        "owner_email": media.CONTACT_EMAIL,
        "stats": stats(path),
        "contacts": list_contacts(query, status, path=path),
        "issues": issues,
        "export_url": "/api/mailroom/export.csv",
        "local_only": True,
        "note": (
            "This view is available only over loopback. It contains contact data and must not "
            "be published or shared. No message is sent automatically."
        ),
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local REA mailing list")
    parser.add_argument("--store", default=str(STORE_PATH))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="show contacts and issue counts")
    export = sub.add_parser("export", help="export contacts as CSV")
    export.add_argument("--out", default="mailroom-contacts.csv")
    draft = sub.add_parser("draft", help="draft this month's field note from the latest publication")
    draft.add_argument("--month", required=True, help="YYYY-MM")
    draft.add_argument("--title", default="")
    args = parser.parse_args(argv)
    store = Path(args.store)
    if args.command == "list":
        print(json.dumps({"stats": stats(store), "contacts": list_contacts(path=store)}, indent=2))
        return 0
    if args.command == "export":
        Path(args.out).write_text(export_csv(path=store), "utf-8")
        print(f"Wrote {args.out}")
        return 0
    if args.command == "draft":
        import studio  # local import keeps the public site independent of the store
        pubs = studio.published()
        publication = pubs[0] if pubs else None
        facts = studio.monthly_facts() if publication else []
        issue = draft_issue(args.month, args.title, publication=publication, facts=facts, path=store)
        print(json.dumps(issue, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(_main())
