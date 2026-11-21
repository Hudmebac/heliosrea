"""The Teardown Room brand and commercial kit.

This module no longer invents a content calendar. A story is produced from a
completed, authorised REA dossier by ``studio.py``. The brand, production stack,
public donation link and contact address live here because they are stable
across stories.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

CONTACT_EMAIL = os.environ.get("REA_CONTACT_EMAIL", "mysurnameandfirsname@gmail.com").strip()

CHANNEL = {
    "name": "The Teardown Room",
    "handle": "@teardownroom",
    "tagline": "Open the box. Map the system. Show the seam.",
    "description": (
        "The Teardown Room is an evidence-led reverse-engineering channel. We open devices, "
        "apps and workflows we own or have written permission to inspect, map how they actually "
        "work, and show the seam where a repair, rebuild or better design becomes possible. "
        "Every story is built from a dossier with locators, confidence and a stated limit."
    ),
    "audience": (
        "Builders, repair shops, hardware founders, IT operators and the technically curious "
        "who want to understand the system instead of guessing."
    ),
    "promise": (
        "Every episode leaves you with a map, a proof point and one thing you can inspect on "
        "your own equipment."
    ),
    "positioning": (
        "Not a hacking channel and not a gadget unboxing channel. A teardown room: evidence, "
        "scope, diagrams and a working result."
    ),
    "not_for": "Exploit tutorials, stolen data, bypassing controls or 'review' units you do not own.",
    "cadence": (
        "One evidence file per completed investigation, plus a monthly field note when there is "
        "something useful to say. Do not invent a calendar before the evidence exists."
    ),
    "pillars": [
        {"name": "Teardown", "detail": "Open an owned device, app or protocol and map what is actually inside."},
        {"name": "The Seam", "detail": "Show the exact place where behaviour can be changed or repaired."},
        {"name": "Rebuild", "detail": "Recreate a small piece and prove it works, without the original tooling."},
        {"name": "Field Notes", "detail": "Checklists, mistakes, evidence habits and the zero-cost kit."},
    ],
    "alternatives": ["The Teardown Room", "Seam Work", "Reverse Engineered"],
    "handle_note": "Check @teardownroom in YouTube Studio. If taken, try @theteardownroom or @teardownroomlab.",
}

STRIPE = {
    "account_description": (
        "The Teardown Room publishes evidence-led reverse-engineering teardowns and field notes "
        "for builders, repairers and operators. Optional support is a one-time donation."
    ),
    "statement_descriptor": "HELIOSTD",
    "donation_name": "HeliosTD Donation",
    "donation_description": (
        "Optional one-time support for the next evidence file. It buys no deliverable and does "
        "not change the publication gate."
    ),
    "donation_price": "£5 default one-time (the Stripe link currently permits a custom amount)",
    "donation_link": "https://buy.stripe.com/bJe9AS53ybBVfZv6R14wM03",
    "steps": [
        "Open HeliosTD Donation in Stripe → Product catalog.",
        "Keep the link as an optional one-time donation; there is no product pack or subscription.",
        "The public donation link is already wired; collect email only if the donor opts in separately.",
        "If you replace the link, use only a public https://buy.stripe.com/ URL. Never a secret or restricted key.",
    ],
}

STACK = [
    {"tool": "Phone or existing camera", "cost": "£0", "use": "Face, hands and device shots."},
    {"tool": "OBS Studio", "cost": "£0", "use": "Screen capture, scenes and clean recordings."},
    {"tool": "DaVinci Resolve", "cost": "£0", "use": "Edit, captions and colour."},
    {"tool": "Audacity", "cost": "£0", "use": "Voice cleanup and noise reduction."},
    {"tool": "Canva Free / Figma Free", "cost": "£0", "use": "Thumbnails and diagrams."},
    {"tool": "YouTube Audio Library", "cost": "£0", "use": "Copyright-safe music and effects."},
    {"tool": "YouTube", "cost": "£0", "use": "Hosting, search and audience."},
    {"tool": "Stripe Payment Links", "cost": "No monthly fee; transaction fees apply", "use": "Take optional £5 donations without a custom store."},
]

RULES = [
    "Only open devices, apps and accounts you own or have written permission to inspect.",
    "Redact serial numbers, keys, customer data and anything that identifies a third party.",
    "Do not publish exploits, bypass steps or instructions that defeat a control.",
    "Do not buy review units, stock footage, ads or software to start. Use what you already own.",
    "No revenue is guaranteed. The first sale comes from a real buyer and delivered work.",
    "No dossier, no publication. A story must be built from a completed, authorised investigation.",
]

PRINCIPLES = [
    "Evidence first: every published claim cites a finding ID, a locator and a confidence.",
    "The seam is the story: show the exact place where behaviour can change, not a montage.",
    "Show the limit: a blind probe, an empty path and an unknown are part of the report.",
    "Ask after proof: the optional donation supports the work; it does not buy a claim or a result.",
]


def payload() -> dict:
    return {
        "channel": dict(CHANNEL),
        "stripe": dict(STRIPE),
        "contact_email": CONTACT_EMAIL,
        "stack": [dict(item) for item in STACK],
        "rules": list(RULES),
        "principles": list(PRINCIPLES),
        "note": (
            "The studio no longer ships a hypothetical slate. Publish a real dossier from the "
            "Engine Room, then cut the video from the evidence and the limits."
        ),
        "generated": datetime.now(tz=timezone.utc).isoformat(),
    }
