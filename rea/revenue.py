"""REA revenue engine.

Two useful things, both grounded in real work rather than a fake money glitch:

1. Scan public Hacker News demand signals for a capability.
2. Turn that capability into a productised offer with a price hypothesis,
   proof asset, first-10-customer route and explicit risks.

The scanner reads public Algolia/Hacker News search results. It does not
scrape private profiles, build a lead list, send messages or promise income.
"""

from __future__ import annotations

import html
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HN_SEARCH = "https://hn.algolia.com/api/v1/search"
MAX_HITS = 10
MAX_DAYS = 3650
MAX_TEXT = 280
USER_AGENT = "REA/1.0 (local revenue research; public Hacker News search)"

MODELS = (
    {
        "id": "automation-sprint",
        "name": "AI workflow automation sprint",
        "buyer": "Operations, finance and back-office teams with repetitive manual work.",
        "price": "$3k–$12k fixed fee",
        "timeline": "2 weeks",
        "promise": "a working automation for one high-volume workflow, with a measured before/after",
        "deliverables": [
            "Current-state workflow map with volumes and failure points",
            "One working automation in the buyer's environment",
            "Before/after time and error measurement",
            "Runbook, ownership notes and a rollback path",
        ],
        "proof": "A redacted workflow teardown showing the manual step, the failure cost and the measured after-state.",
        "channels": [
            "Hacker News threads where operators describe manual reconciliation or reporting",
            "LinkedIn search for operations, finance and shared-services leaders",
            "Warm network of operators who already know your work",
        ],
        "risk": "Do not promise headcount reduction or a specific ROI before measuring the workflow.",
    },
    {
        "id": "evidence-pack",
        "name": "Audit evidence pack",
        "buyer": "Security, compliance and finance teams facing an audit or customer questionnaire.",
        "price": "$5k–$20k fixed fee",
        "timeline": "10 working days",
        "promise": "a reproducible evidence pack that answers the control questions without inventing confidence",
        "deliverables": [
            "Control-to-evidence matrix with owners and dates",
            "Redacted sample evidence and collection method",
            "Gap list ranked by audit impact",
            "Reusable request and review checklist",
        ],
        "proof": "A one-page control map from an owned lab or a redacted prior engagement.",
        "channels": [
            "Hacker News and security communities discussing audit preparation",
            "Compliance and security leaders preparing for customer diligence",
            "Accountants and virtual CISOs who need technical evidence",
        ],
        "risk": "Never claim certification, legal advice or complete coverage; scope the controls in writing.",
    },
    {
        "id": "legacy-rescue",
        "name": "Legacy system rescue retainer",
        "buyer": "Companies running unsupported line-of-business software with no original author.",
        "price": "$4k–$15k/month",
        "timeline": "Monthly retainer, 3-month minimum",
        "promise": "a maintained path for a system everyone else has abandoned",
        "deliverables": [
            "Dependency and runtime map",
            "Known-failure register and recovery runbook",
            "Prioritised stabilisation backlog",
            "Monthly change and risk review",
        ],
        "proof": "A read-only teardown of an owned legacy app with a recovery map and evidence trail.",
        "channels": [
            "Operations and IT leaders in regulated or family-owned businesses",
            "Managed-service providers inheriting undocumented systems",
            "Job posts asking for maintainers of an old stack",
        ],
        "risk": "Do not take on a system you cannot recover; require backups and an owner on the buyer side.",
    },
    {
        "id": "firmware-teardown",
        "name": "Device and firmware teardown",
        "buyer": "Hardware vendors, repair networks and diligence teams that need to understand a device.",
        "price": "$5k–$30k per device",
        "timeline": "2–4 weeks",
        "promise": "a static map of the update path, controls and compatibility risks without touching production",
        "deliverables": [
            "Firmware container and update-path map",
            "Control and signature-boundary findings",
            "Component and licence inventory",
            "Repair, compatibility or diligence memo",
        ],
        "proof": "A redacted teardown of a device you own, with verified hashes and a clean evidence chain.",
        "channels": [
            "Repair networks and independent hardware vendors",
            "Procurement and diligence teams evaluating device portfolios",
            "Owner-authorised lab work only",
        ],
        "risk": "Written permission and an owned device are mandatory; never bypass a control or publish extracted secrets.",
    },
    {
        "id": "agent-audit",
        "name": "Agentic workflow audit",
        "buyer": "Teams deploying AI agents into customer support, research or operations.",
        "price": "$2.5k–$10k fixed fee",
        "timeline": "1 week",
        "promise": "a failure map of the agent workflow, its tools, permissions and human handoffs",
        "deliverables": [
            "Agent/tool permission map",
            "Prompt, retrieval and handoff failure inventory",
            "Eval set for the highest-risk paths",
            "Guardrail and observability recommendations",
        ],
        "proof": "A redacted agent trace with one failure reproduced and one guardrail verified.",
        "channels": [
            "AI engineering and operations communities discussing agent failures",
            "Teams hiring their first AI operations lead",
            "Agencies shipping agents without an evaluation owner",
        ],
        "risk": "Do not test production agents with destructive actions; use a sandbox and written scope.",
    },
    {
        "id": "technical-diligence",
        "name": "Technical diligence sprint",
        "buyer": "Investors, acquirers and founders preparing for a technical review.",
        "price": "$8k–$35k per sprint",
        "timeline": "2–3 weeks",
        "promise": "a decision-ready view of the code, controls, dependencies and hidden operating risk",
        "deliverables": [
            "Architecture and dependency map",
            "Control, licence and concentration risks",
            "Evidence-backed questions for management",
            "Prioritised 100-day technical plan",
        ],
        "proof": "A redacted diligence memo from an owned or permissioned codebase.",
        "channels": [
            "Investors and operators evaluating small software businesses",
            "Founders preparing a data room",
            "Accountants and brokers who need a technical translator",
        ],
        "risk": "Keep material non-public information inside the agreed data room; do not reuse it.",
    },
)

MODEL_BY_ID = {model["id"]: model for model in MODELS}


def _clean(value: str, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _iso(timestamp: int | float | None) -> str:
    try:
        return datetime.fromtimestamp(float(timestamp or 0), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def _plain(value: str) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_TEXT]


def _fetch_json(url: str, timeout: int = 8) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(1_000_000)
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("unexpected response from the demand source")
    return data


def models_payload() -> dict:
    return {
        "models": [dict(model) for model in MODELS],
        "note": (
            "Price bands are starting hypotheses, not market data. Validate them with real buyers "
            "before quoting. No revenue is guaranteed."
        ),
    }


def _signal_reason(text: str, capability: str, buyer: str) -> str:
    haystack = f"{text} {capability} {buyer}".lower()
    if any(term in haystack for term in ("looking for", "need help", "hire", "freelance",
                                         "consultant", "contract", "budget", "willing to pay")):
        return "Explicit request for help or paid expertise."
    if any(term in haystack for term in ("manual", "spreadsheet", "reconcile", "legacy",
                                         "undocumented", "audit", "migration", "failed")):
        return "Repeated operational pain with a measurable cost."
    return "Public discussion adjacent to the capability; read the thread before treating it as demand."


def scan_demand(capability: str, buyer: str = "", limit: int = 8,
                days: int = 365) -> dict:
    capability = _clean(capability, 80)
    buyer = _clean(buyer, 80)
    if not capability:
        raise ValueError("capability is required")
    try:
        limit = int(limit)
        days = int(days)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit and days must be integers") from exc
    limit = max(1, min(limit, MAX_HITS))
    days = max(30, min(days, MAX_DAYS))
    cutoff = int(time.time()) - days * 86400

    queries = [capability]
    if buyer:
        queries.append(f"{capability} {buyer}")
    if "consult" not in capability.lower():
        queries.append(f"{capability} consultant")
    queries = list(dict.fromkeys(queries))[:3]

    found: dict[str, dict] = {}
    errors: list[str] = []
    for query in queries:
        params = urllib.parse.urlencode({
            "query": query,
            "hitsPerPage": str(MAX_HITS),
            "numericFilters": f"created_at_i>{cutoff}",
        })
        try:
            payload = _fetch_json(HN_SEARCH + "?" + params)
        except Exception as exc:  # noqa: BLE001 - surface a source failure
            errors.append(f"{query}: {type(exc).__name__}: {exc}")
            continue
        for hit in payload.get("hits", []):
            if not isinstance(hit, dict):
                continue
            object_id = str(hit.get("objectID") or "")
            if not object_id or object_id in found:
                continue
            tags = hit.get("_tags") or []
            kind = "comment" if "comment" in tags else ("story" if "story" in tags else "post")
            text = _plain(hit.get("comment_text") or hit.get("story_text") or "")
            title = _clean(hit.get("title") or hit.get("story_title") or "", 140)
            if not text and not title:
                continue
            if not text:
                text = _clean(hit.get("url") or "", MAX_TEXT)
            found[object_id] = {
                "id": object_id,
                "title": title or ("Hacker News " + kind),
                "kind": kind,
                "source": "Hacker News",
                "url": f"https://news.ycombinator.com/item?id={object_id}",
                "created": _iso(hit.get("created_at_i")),
                "snippet": text,
                "points": int(hit.get("points") or 0),
                "comments": int(hit.get("num_comments") or 0),
                "query": query,
                "why": _signal_reason(text, capability, buyer),
            }

    signals = sorted(found.values(), key=lambda item: item.get("created") or "", reverse=True)[:limit]
    if not signals and errors:
        raise RuntimeError("demand source unavailable: " + errors[0])
    return {
        "ok": True,
        "capability": capability,
        "buyer": buyer,
        "window_days": days,
        "signals": signals,
        "queries": queries,
        "summary": (
            f"{len(signals)} public Hacker News signals in the last {days} days. "
            "Read the threads, quote the pain and reply with a useful diagnosis."
        ),
        "note": (
            "Public search results only. This is not a lead list, not private data and not an "
            "automatic outreach tool. No income is guaranteed."
        ),
        "warnings": errors,
        "generated": datetime.now(tz=timezone.utc).isoformat(),
    }


def build_offer(capability: str, buyer: str = "", model_id: str = "automation-sprint",
                price: str = "", proof: str = "") -> dict:
    capability = _clean(capability, 100)
    buyer = _clean(buyer, 100)
    if not capability:
        raise ValueError("capability is required")
    model = MODEL_BY_ID.get(str(model_id or "").strip())
    if not model:
        raise ValueError("unknown revenue model")

    audience = buyer or model["buyer"]
    title = f"{capability} · {model['name']}"
    first_10 = [
        "Publish one redacted proof asset that shows the problem, method and measured result.",
        "Find ten public threads where the buyer describes the same pain; save the URL and the exact words.",
        "Reply to three threads with a diagnosis and one useful next step — no pitch, no calendar link.",
        "Offer a paid 45-minute evidence review to two people who reply; keep the fee small enough to say yes.",
        "Convert one review into a fixed-scope sprint with a written acceptance test.",
        "Ask every buyer for one introduction to another operator with the same workflow.",
    ]
    outreach = [
        "Lead with the artifact, not a calendar link.",
        "Name the cost of the current workflow in one sentence.",
        "Ask for a 20-minute evidence review, not a demo.",
        "No scraped personal data, no bulk spam and no fake urgency.",
    ]
    return {
        "ok": True,
        "offer": {
            "title": title,
            "model_id": model["id"],
            "model": model["name"],
            "capability": capability,
            "buyer": audience,
            "promise": f"In {model['timeline']}, deliver {model['promise']}.",
            "price": _clean(price, 80) or model["price"],
            "pricing_basis": (
                "Starting hypothesis, not market data. Test it with five buyer conversations "
                "before quoting it as a standard price."
            ),
            "timeline": model["timeline"],
            "deliverables": list(model["deliverables"]),
            "proof": _clean(proof, 220) or model["proof"],
            "first_10": first_10,
            "outreach": outreach,
            "channels": list(model["channels"]),
            "risks": [
                model["risk"],
                "A public post is a signal, not a buyer; verify budget, authority and timing.",
                "Do not promise a specific revenue outcome, headcount reduction or compliance certification.",
            ],
            "next_action": (
                "Publish the proof asset, then reply to three public threads with a diagnosis. "
                "Measure replies, not impressions."
            ),
        },
        "note": (
            "This is a commercial hypothesis and delivery plan, not a guarantee of income. "
            "The work still has to be sold and delivered."
        ),
        "generated": datetime.now(tz=timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    print(json.dumps(models_payload(), indent=1))
