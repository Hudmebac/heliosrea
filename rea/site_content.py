"""Public pages for The Teardown Room.

This module holds the editorial site, not the evidence engine. It renders a
multi-page static publication from the real publication manifests and the fact
payloads supplied by ``studio.py``. No customer testimonial is invented here: the
proof page uses the project brief, the recorded dossier verdict and the
editorial rule, and says plainly that no customer testimonials exist yet.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from urllib.parse import quote

import field_notes
import media
import playbook

STRIPE = media.STRIPE
BRAND = media.CHANNEL
CONTACT_EMAIL = media.CONTACT_EMAIL
REPO_URL = "https://github.com/Hudmebac/heliosrea"
ZIP_URL = "https://github.com/Hudmebac/heliosrea/archive/refs/heads/main.zip"


# ------------------------------------------------------------------ helpers --

def _e(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _trim(value: object, limit: int = 210) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, "utf-8")
    tmp.replace(path)


NAV = (
    ("start", "Start here", "start/"),
    ("evidence", "Evidence", "evidence/"),
    ("facts", "Fact files", "facts/"),
    ("guides", "Field guides", "guides/"),
    ("method", "Method", "method/"),
    ("watch", "Field notes", "watch/"),
    ("download", "Download", "download/"),
    ("proof", "Proof", "proof/"),
    ("newsletter", "Monthly notes", "newsletter/"),
    ("pricing", "Support", "pricing/"),
)


def _header(root: str, active: str) -> str:
    links = "".join(
        f'<a class="{" ".join(filter(None, ["nav-start" if key == "start" else "", "on" if key == active else ""]))}" href="{root}{path}">{label}</a>'
        for key, label, path in NAV
    )
    return f"""<header class="top"><div class="brand"><a href="{root}index.html"><b>THE TEARDOWN ROOM</b></a>
<span>Open the box. Map the system. Show the seam.</span></div>
<nav class="nav" aria-label="Primary navigation">{links}<button class="nav-search" type="button" data-search-open aria-haspopup="dialog" aria-controls="search-panel">Search <kbd>/</kbd></button><a class="nav-buy" href="{_e(STRIPE['donation_link'])}" rel="noreferrer">Support · £5</a></nav></header>"""


def _footer(root: str) -> str:
    return f"""<footer class="foot"><div class="footer-grid">
<div><b>THE TEARDOWN ROOM</b><p>Evidence-led teardowns of devices, apps and workflows we own or have written permission to inspect. Every claim carries a finding ID, a locator, a confidence and a stated limit.</p></div>
<div><h4>Read</h4><a href="{root}start/">Start here</a><a href="{root}evidence/">Evidence library</a><a href="{root}facts/">Fact files</a><a href="{root}proof/">Proof</a></div>
<div><h4>Learn</h4><a href="{root}guides/">Field guides</a><a href="{root}method/">Method</a><a href="{root}glossary/">Glossary</a><a href="{root}crew/">Crew</a></div>
<div><h4>Room</h4><a href="{root}download/">Download the room</a><a href="{root}watch/">Ten field notes</a><a href="{root}newsletter/">Monthly notes</a><a href="{root}pricing/">Support · £5</a><a href="mailto:{_e(CONTACT_EMAIL)}">Email the room</a></div>
</div><p class="footer-rule">No dossier, no publication. Fixtures, demos, example hosts and private network captures are refused. Secret-shaped values are redacted before publication. The £5 link is a public Stripe Payment Link; no secret key is stored in this site. The field-note list is opt-in and never sold.</p><p class="footer-rule">Official site: <a href="https://heliosrea.netlify.app/">heliosrea.netlify.app</a>. The only official support link is the Stripe £5 link in the header. Forks and copies are not operated by the room and cannot change this site, its payment link or its data.</p></footer>"""


def _search_panel(root: str) -> str:
    return f"""<div class="search-panel" id="search-panel" hidden>
<div class="search-box" role="dialog" aria-modal="true" aria-labelledby="search-title">
<div class="search-head"><b id="search-title">Search the room</b><button type="button" class="search-close" data-search-close aria-label="Close search">Close</button></div>
<label class="sr-only" for="search-input">Search pages and fact files</label>
<input id="search-input" type="search" placeholder="Try: locator, seam, redaction, glossary…" autocomplete="off">
<p class="search-hint">Search titles and descriptions across the evidence library, fact files, guides and method.</p>
<div class="search-results" id="search-results" aria-live="polite"></div>
</div></div>"""


def _page(title: str, description: str, root: str, active: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en-GB" data-root="{root}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#070a0f">
<title>{_e(title)} — The Teardown Room</title>
<meta name="description" content="{_e(description)}">
<meta property="og:title" content="{_e(title)}">
<meta property="og:description" content="{_e(description)}">
<meta property="og:type" content="website">
<link rel="stylesheet" href="{root}assets/site.css">
<script src="{root}assets/site.js" defer></script></head>
<body><a class="skip" href="#main">Skip to content</a><div class="wrap">
{_header(root, active)}
<main id="main" tabindex="-1">{body}</main>
{_footer(root)}
{_search_panel(root)}
</div></body></html>"""


def _chips(items: list[tuple[str, object]]) -> str:
    return '<div class="meta">' + "".join(
        f'<span class="chip">{_e(label)} <b>{_e(value)}</b></span>' for label, value in items
    ) + "</div>"


def _link(label: str, href: str, style: str = "") -> str:
    cls = f' class="btn {style}"'.rstrip() if style else ' class="btn"'
    rel = ' rel="noreferrer"' if href.startswith(("http://", "https://")) else ""
    return f'<a{cls} href="{_e(href)}"{rel}>{_e(label)}</a>'


def _subscribe_form(source: str, donation_context: bool = False, compact: bool = False) -> str:
    owner = _e(CONTACT_EMAIL)
    subject = quote("Field notes request")
    body = quote("Please add me to the monthly field notes.\n\nEmail:\nName:\n")
    mailto = f"mailto:{owner}?subject={subject}&body={body}"
    donation = "" if not donation_context else """<label class="check"><input type="checkbox" name="donated" value="yes"> I have just left a £5 donation.</label>"""
    return f"""<form class="subscribe-form{' compact' if compact else ''}" data-source="{_e(source)}" data-owner-email="{owner}" novalidate>
<div class="form-grid"><label>Email <span class="req">required</span><input type="email" name="email" maxlength="254" autocomplete="email" placeholder="you@example.com" required></label>
<label>First name <span class="opt">optional</span><input type="text" name="name" maxlength="80" autocomplete="given-name" placeholder="First name"></label></div>
<fieldset><legend>Send me</legend><label class="check"><input type="checkbox" name="topics" value="monthly" checked> Monthly field notes</label>
<label class="check"><input type="checkbox" name="topics" value="evidence"> New evidence files</label></fieldset>
{donation}
<label class="check"><input type="checkbox" name="consent" required> I agree to receive the updates I selected. I can unsubscribe at any time.</label>
<button class="btn gold" type="submit">Request the field notes</button>
<p class="form-note">The form saves your request in the local Mailroom. It does not auto-send email. Your address is not published or sold.</p>
<div class="form-result" role="status" aria-live="polite"></div>
<noscript><p class="form-note">JavaScript is off. Email <a href="{mailto}">{owner}</a> with the subject “Field notes request”.</p></noscript>
</form>"""


def _table(headers: list[str], rows: list[list[object]]) -> str:
    head = "".join(f"<th>{_e(item)}</th>" for item in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{_e(cell)}</td>" for cell in row) + "</tr>" for row in rows
    )
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _prose(sections: list[dict]) -> str:
    out: list[str] = []
    for index, section in enumerate(sections, start=1):
        para = "".join(f"<p>{_e(text)}</p>" for text in section.get("p", []))
        bullets = ""
        if section.get("ul"):
            bullets = "<ul>" + "".join(f"<li>{_e(item)}</li>" for item in section["ul"]) + "</ul>"
        out.append(
            f'<section class="prose-block" id="s{index}"><h2>{_e(section.get("h"))}</h2>'
            f"{para}{bullets}</section>"
        )
    return "".join(out)


# --------------------------------------------------------------- learning --

VIDEO_CAPTIONS: tuple[dict, ...] = (
    {"head": "One real file. Ten findings.", "body": "A source tree we own. Seven places where behaviour could change.", "say": "Let's start with one real file. Ten findings, and seven places where behaviour could change, in a source tree we own."},
    {"head": "Evidence first. Story second.", "body": "Every claim keeps its ID, locator, confidence and limit.", "say": "The rule is simple. Evidence first, story second. Every claim keeps its ID, where to find it, how confident we are, and its limit."},
    {"head": "A small owned source tree.", "body": "Twelve files. Three access modes. Written authorisation.", "say": "The target is a small source tree. Twelve files, three access modes, and written authorisation."},
    {"head": "F-001 · The shape.", "body": "Four languages. No binaries. A map, not a vulnerability.", "say": "The first finding is its shape. Four languages, no binaries. Think of it as a map, not a vulnerability."},
    {"head": "E-01 · A source seam.", "body": "Baseline first. Change one module. Compare. A seam is not a change.", "say": "The first seam is the source. Take a baseline, change one module, then compare. A seam is only a place where change is possible. It is not a change we made."},
    {"head": "No dossier. No publication.", "body": "10 records. 7 seams. 13 probes. Fixtures refused.", "say": "Ten records, seven seams, thirteen probes. No dossier, no publication. Fixtures and demos are refused."},
    {"head": "What this does not prove.", "body": "No runtime beyond the probe. No vulnerability. No client work.", "say": "Here's the honest part. This says nothing about runtime beyond the probe. It is not a vulnerability claim, and it is not client work."},
    {"head": "The receipt.", "body": "A SHA-256 hash. Full value on the page.", "say": "The page also carries a receipt. A hash ties the story to the dossier. You don't need to memorise it. It is there so someone else can check."},
    {"head": "Read. Check. Keep the limit.", "body": "That is the room.", "say": "Read the evidence. Check the locator. Keep the limit. That is the room."},
)

GLOSSARY: tuple[tuple[str, str], ...] = (
    ("Dossier", "The local record of an investigation: target, scope, authorisation, probes, findings, seams, hashes and limits."),
    ("Finding", "One recorded observation with an ID, family, title, locator and confidence. A finding is not automatically a vulnerability."),
    ("Fact file", "A stable public page that expands one finding or seam, with the locator, a verification path and the limit."),
    ("Locator", "The exact file, line, request, probe result or inventory scope that supports a claim."),
    ("Confidence", "A recorded judgement about the strength of an observation, not a probability and not a truth score."),
    ("Change surface", "A place where behaviour can be altered or repaired. Also called a seam. It is not a claim that a change was made."),
    ("Seam", "The short name for a change surface: source, configuration, protocol, binary, edge content or firmware."),
    ("Evidence ladder", "The outside-in probe sequence: identify the surface, read manifests and docs, map code or requests, then inspect only when justified."),
    ("Publication gate", "The check that refuses unauthorised, synthetic, thin or fixture-based dossiers before a public page can be generated."),
    ("Appendix", "The full evidence record, including test-only observations, duplicates, empty probes and errors. The story is a selection; the appendix is the audit trail."),
    ("Digest", "A SHA-256 hash of the dossier. It is a receipt that the public file came from a specific record, not a proof that every claim is correct."),
    ("Redaction", "Replacing secret-shaped or private values at the source while keeping the finding, locator and limit. A blur is not a redaction."),
    ("Paper edit", "A shot script written from the dossier before recording. It names what to show, not what to claim."),
    ("Probe status", "Signal, empty, error, skipped or retry. The status is part of the evidence and must stay visible."),
    ("Operator", "The named human who owns the target or has written permission, sets the stop condition and signs off publication."),
)


def _fact_row(fact: dict, label: str) -> str:
    for row in (fact.get("table") or {}).get("rows") or []:
        if row and row[0] == label:
            return str(row[1])
    return ""


def _fact_limit(fact: dict) -> str:
    for section in fact.get("body") or []:
        if section.get("h") == "What this does not prove":
            return " ".join(section.get("p") or [])
    return str(fact.get("note") or "Read the fact file for the stated limit.")


def _path_cards(root: str, pubs: list[dict], compact: bool = False) -> str:
    latest = f"{root}publications/{_e(pubs[0].get('slug'))}/" if pubs else f"{root}evidence/"
    more = "" if compact else """<p class="small">Each route ends at the same standard: a finding ID, a locator, a confidence and a limit. The route changes; the standard does not.</p>"""
    return f"""<div class="path-grid">
<article class="path-card curious"><span class="path-label">Curious · 2 minutes</span><h3>I want the short version</h3>
<p>Start with the narrated captioned explainer, then read one real fact and see what the room refuses to claim.</p>
<ol class="path-list"><li>Watch one of the ten field notes.</li><li>Read one fact file in 60 seconds.</li><li>Open the glossary when a term gets in the way.</li></ol>
<div class="path-actions">{_link("Watch the field notes", f"{root}watch/", "gold")}{_link("Read one fact", f"{root}facts/")}</div></article>
<article class="path-card beginner"><span class="path-label">Beginner · 30 minutes</span><h3>I want to learn the method</h3>
<p>Follow the same sequence the room uses: scope, evidence, redaction, verification, then publication.</p>
<ol class="path-list"><li>Scope and authorise the target.</li><li>Run the evidence ladder and keep every status.</li><li>Redact, verify, then publish a static file.</li></ol>
<div class="path-actions">{_link("Start with scope", f"{root}guides/scope-and-authorise.html", "gold")}{_link("All guides", f"{root}guides/")}</div></article>
<article class="path-card expert"><span class="path-label">Expert · straight to the receipt</span><h3>I already do this</h3>
<p>Skip the introduction. Go to the dossier, the claim-to-evidence table, the appendix and the known limits.</p>
<ol class="path-list"><li>Check the dossier hash and the scope.</li><li>Inspect a fact and its locator.</li><li>Read the appendix, including the empty probes.</li></ol>
<div class="path-actions">{_link("Open the latest file", latest, "gold")}{_link("Read the method", f"{root}method/")}{_link("Glossary", f"{root}glossary/")}</div></article></div>{more}"""


def _video_block(root: str, note: dict) -> str:
    preset = field_notes.VOICE_PRESETS[note["voice"]]
    base = f"{root}assets/field-notes/{note['number']}/"
    caption_id = f"video-caption-{note['number']}"
    return f"""<figure class="video-block" id="field-note-{note['number']}">
<video controls preload="metadata" playsinline poster="{base}poster.png" aria-describedby="{caption_id}">
<source src="{base}field-note.mp4" type="video/mp4">
<track kind="captions" src="{base}field-note.vtt" srclang="en" label="English" default>
Your browser does not support the video element. <a href="{base}field-note.mp4">Download the MP4</a>.
</video>
<figcaption id="{caption_id}"><b>Field note {note['number']} · {_e(preset['label'])} · narrated, captioned brief.</b> Original diagrams, a local synthetic {_e(preset['locale'])} voice treatment and an original ambient bed; no third-party footage, music or audio. <a href="{root}watch/{note['number']}/">Open note {note['number']} and transcript</a>.</figcaption>
</figure>"""


def _external_resources() -> str:
    return """<section><div class="section-head"><div><span class="eyebrow">Further reading and watching</span><h2>References we learned from</h2>
<p>These are external resources, not endorsements and not our work. The Teardown Room is independent; we adapted public patterns such as worked examples, visible evidence and clear limits, not their copy or branding.</p></div></div>
<div class="cards-2"><div class="card"><h3>REA</h3><p>The upstream reverse-engineering project and its public guide set. It is a separate MIT-licensed project by morluto.</p><a class="btn" href="https://github.com/morluto/rea" rel="noopener noreferrer">REA on GitHub ↗</a><a class="btn" href="https://rea.tools/" rel="noopener noreferrer">rea.tools ↗</a></div>
<div class="card"><h3>External video explainers</h3><p>Two public videos about REA. We did not use their footage, audio or wording in our explainer.</p><a class="btn" href="https://www.youtube.com/watch?v=FnB9tHKBJXQ" rel="noopener noreferrer">How To Use REA To Clone Any Software In Minutes ↗</a><a class="btn" href="https://www.youtube.com/watch?v=Xu-mG1D8ix4" rel="noopener noreferrer">Reverse Engineer Anything (REA) Explained ↗</a></div></div></section>"""


def start_html(pubs: list[dict], facts: list[dict]) -> str:
    first = next((fact for fact in facts if fact.get("code") not in {"SCOPE", "PROBES"}), facts[0] if facts else {})
    title = str(first.get("title") or "No fact file yet")
    summary = str(first.get("summary") or "A fact file appears when a real publication passes the gate.")
    locator = _fact_row(first, "Locator") or "No locator recorded yet"
    confidence = f"{first.get('confidence')}%" if first.get("confidence") is not None else "not recorded"
    limit = _fact_limit(first) if first else "No public claim has passed the gate yet."
    fact_url = f"../facts/{_e(first.get('slug'))}.html" if first.get("slug") else "../facts/"
    body = f"""
<div class="breadcrumb"><a href="../index.html">Home</a> / Start here</div>
<span class="eyebrow">Choose your way in</span><h1>Three routes. One evidence standard.</h1>
<p class="dek">You do not need to be a reverse engineer to use this room, and you do not need a tour if you already are one. Pick the route that matches your question. Every route ends at a claim you can check.</p>
{_chips([('evidence files', len(pubs)), ('fact files', len(facts)), ('routes', 3), ('hidden gate', 'strict')])}
{_path_cards("../", pubs)}

<section><div class="section-head"><div><span class="eyebrow">Field notes</span><h2>Ten short briefs. One evidence standard.</h2>
<p>Ten narrated, captioned visual explainers generated from the real dossier. The series uses three local Windows OneCore voices in ten presenter treatments, with original diagrams and an original ambient bed. They are a door into the evidence, not a replacement for it.</p></div><a href="../watch/">All ten field notes →</a></div>
<div class="split"><div>{_video_block("../", field_notes.FIELD_NOTES[0])}</div><div class="card"><h3>What the series is</h3><ul class="rule-list"><li>Ten original visual briefs from dossier {_e(pubs[0].get('dossier_id') if pubs else 'not yet published')}.</li><li>Different subjects, card accents and presenter treatments.</li><li>A local synthetic voice in each note, with player mute controls.</li><li>An original ambient bed under every voice.</li><li>A pointer to the receipt, the fact files and the limits.</li></ul><h3>What it is not</h3><ul class="rule-list"><li>It is not a recorded teardown or a client case study.</li><li>It is not ten different people; it is three local voices treated ten ways.</li><li>It does not use third-party footage, music, audio or branding.</li></ul></div></div></section>

<section><div class="section-head"><div><span class="eyebrow">Try the evidence reader</span><h2>One claim, four things to check</h2>
<p>This is a real fact file, not a demo fixture. Switch between the claim, the locator, the confidence and the limit. The full file is one click away.</p></div><a href="{fact_url}">Open {_e(first.get('code') or 'the fact')} →</a></div>
<div class="evidence-reader" data-evidence-reader>
<div class="evidence-tabs" role="tablist" aria-label="Parts of a fact file">
<button type="button" role="tab" aria-selected="true" data-evidence-tab="claim">Claim</button>
<button type="button" role="tab" aria-selected="false" data-evidence-tab="locator">Locator</button>
<button type="button" role="tab" aria-selected="false" data-evidence-tab="confidence">Confidence</button>
<button type="button" role="tab" aria-selected="false" data-evidence-tab="limit">Limit</button></div>
<div class="evidence-panel" role="tabpanel" data-evidence-panel="claim"><span class="eyebrow">{_e(first.get('code') or 'Fact')} · claim</span><h3>{_e(title)}</h3><p>{_e(summary)}</p></div>
<div class="evidence-panel" role="tabpanel" data-evidence-panel="locator" hidden><span class="eyebrow">Where to look</span><h3>{_e(locator)}</h3><p>The locator is the route back to the recorded observation. If it cannot be opened or reproduced, the claim is not ready to lead a story.</p></div>
<div class="evidence-panel" role="tabpanel" data-evidence-panel="confidence" hidden><span class="eyebrow">How strong is it?</span><h3>{_e(confidence)}</h3><p>Confidence is a recorded judgement, not a probability. A high-confidence observation still needs a limit and a verification path.</p></div>
<div class="evidence-panel" role="tabpanel" data-evidence-panel="limit" hidden><span class="eyebrow">What it does not prove</span><h3>Keep the limit attached</h3><p>{_e(limit)}</p></div>
<p class="small"><a href="{fact_url}">Read the whole fact file</a> · <a href="../glossary/">Look up a term</a></p></div></section>

<section><div class="section-head"><div><span class="eyebrow">How to move through the room</span><h2>Read, check, then go deeper</h2></div><a href="../method/">The full method →</a></div>
<ol class="steps"><li><b>Read the claim.</b> Start with the latest evidence file or one fact file. Do not start with the appendix.</li>
<li><b>Check the receipt.</b> Open the locator, the confidence and the dossier hash. The appendix is where the empty probes live.</li>
<li><b>Learn the method.</b> The field guides turn the same sequence into a checklist you can use on an authorised target.</li>
<li><b>Choose a next step.</b> Watch one field note, read the proof page, or request the monthly notes. No step requires a donation.</li></ol></section>

{_external_resources()}
"""
    return _page("Start here", "Choose a route for the curious, the beginner or the expert. Start with one of ten narrated field notes, a real fact file or the full evidence method.",
                 "../", "start", body)


def _note_card(note: dict, root: str = "../") -> str:
    preset = field_notes.VOICE_PRESETS[note["voice"]]
    base = f"{root}assets/field-notes/{note['number']}/"
    return f"""<article class="note-card" style="--note-accent:{preset['accent']}">
<a class="note-thumb" href="{root}watch/{note['number']}/"><img loading="lazy" src="{base}poster.png" alt="Poster for field note {note['number']}: {_e(note['title'])}"></a>
<div class="note-copy"><span class="note-number">FIELD NOTE {note['number']}</span>
<h3><a href="{root}watch/{note['number']}/">{_e(note['title'])}</a></h3>
<p>{_e(note['dek'])}</p>
<div class="meta"><span class="chip">{_e(preset['label'])}</span><span class="chip">narrated MP4</span><span class="chip">captions</span></div>
</div></article>"""


def _transcript(note: dict) -> str:
    return "".join(
        f"<div class=\"transcript-line\"><span>{index:02d}</span><div><b>{_e(caption['head'])}</b><p>{_e(caption.get('say') or caption['body'])}</p></div></div>"
        for index, caption in enumerate(note["captions"], start=1)
    )


def watch_html(pubs: list[dict]) -> str:
    pub = pubs[0] if pubs else {}
    dossier = pub.get("dossier_id") or "not yet published"
    featured = field_notes.FIELD_NOTES[0]
    transcript = _transcript(featured)
    cards = "".join(_note_card(note) for note in field_notes.FIELD_NOTES)
    body = f"""
<div class="breadcrumb"><a href="../index.html">Home</a> / Field notes</div>
<span class="eyebrow">Field notes · ten short evidence briefs</span><h1>Ten field notes. Ten ways into the evidence.</h1>
<p class="dek">A series of narrated, captioned visual briefs built from the published dossier. Each note has its own subject, card accent and presenter treatment. The detail still lives in the fact files and appendix.</p>
{_chips([('notes', len(field_notes.FIELD_NOTES)), ('base voices', 3), ('treatments', len(field_notes.FIELD_NOTES)), ('source', dossier)])}
<h2 id="all-notes">All ten notes</h2>
<p class="small">Every note is listed here. Open any card for its player, transcript, captions and downloads.</p>
<div class="note-grid">{cards}</div>
<h2>Start with note 01</h2>
{_video_block("../", featured)}
<div class="split"><div class="card"><h3>How the voices work</h3><ul class="rule-list"><li>Three installed local Windows OneCore voices: George, Hazel and Susan.</li><li>George is the male voice; Hazel and Susan are female.</li><li>Ten treatments made by changing pace, pitch, tone and the room bed.</li><li>No external TTS, cloned person or third-party audio.</li><li>Each note labels the voice treatment on its page.</li></ul></div>
<div class="card"><h3>What stays constant</h3><ul class="rule-list"><li>Original diagrams generated from the public evidence.</li><li>The finding, locator, confidence and limit stay attached.</li><li>Spoken lines avoid hashes and finding IDs; those remain on the page.</li><li>Captions in English and a full transcript for every note.</li><li>No automatic upload to YouTube or any other platform.</li></ul></div></div>
<h2>Read the transcript for note 01</h2>
<div class="transcript">{transcript}</div>
<div class="card"><h3>Check the source behind the series</h3><p>Open the evidence file, then follow the findings and seams into the fact files and the full appendix. The hash is the receipt; it is not a guarantee that every claim is correct.</p>{_link("Open the evidence file", f"../publications/{_e(pub.get('slug'))}/" if pub.get('slug') else "../evidence/", "gold")}{_link("Open the shot script", f"../publications/{_e(pub.get('slug'))}/video-script.md" if pub.get('slug') else "../evidence/")}{_link("Read the video guide", "../guides/shoot-the-teardown-video.html")}</div>
<h2>Downloads</h2>
<p class="small"><a href="../assets/field-notes/01/field-note.mp4">Note 01 MP4</a> · <a href="../assets/field-notes/01/field-note.vtt">WebVTT captions</a> · <a href="../assets/field-notes/01/poster.png">Poster</a> · <a href="../assets/field-notes/01/transcript.md">Transcript</a></p>
{_external_resources()}
"""
    return _page("Field notes — ten evidence briefs", "Ten narrated captioned field notes from the real evidence dossier, with ten presenter treatments, full transcripts and downloads.",
                 "../", "watch", body)


def watch_note_html(note: dict, pubs: list[dict]) -> str:
    pub = pubs[0] if pubs else {}
    preset = field_notes.VOICE_PRESETS[note["voice"]]
    dossier = pub.get("dossier_id") or "not yet published"
    root = "../../"
    base = f"{root}assets/field-notes/{note['number']}/"
    transcript = _transcript(note)
    source_links = "".join(
        _link(label, root + href.format(slug=pub.get("slug") or ""), "gold" if index == 0 else "")
        for index, (label, href) in enumerate(note["links"])
    )
    index = field_notes.FIELD_NOTES.index(note)
    previous = field_notes.FIELD_NOTES[index - 1] if index > 0 else None
    following = field_notes.FIELD_NOTES[index + 1] if index + 1 < len(field_notes.FIELD_NOTES) else None
    previous_html = (
        f'<a href="{root}watch/{previous["number"]}/"><span>Previous</span><b>← Note {previous["number"]} · {_e(previous["title"])}</b></a>'
        if previous else f'<a href="{root}watch/"><span>Series</span><b>← All ten field notes</b></a>'
    )
    following_html = (
        f'<a class="next" href="{root}watch/{following["number"]}/"><span>Next</span><b>Note {following["number"]} · {_e(following["title"])} →</b></a>'
        if following else f'<a class="next" href="{root}watch/"><span>Series</span><b>All ten field notes →</b></a>'
    )
    what = "".join(f"<li>{_e(item)}</li>" for item in note["what"])
    nots = "".join(f"<li>{_e(item)}</li>" for item in note["not"])
    body = f"""
<div class="breadcrumb"><a href="{root}index.html">Home</a> / <a href="{root}watch/">Field notes</a> / Note {note['number']}</div>
<span class="eyebrow">Field note {note['number']} · {_e(preset['label'])} · {_e(preset['locale'])}</span><h1>{_e(note['title'])}</h1>
<p class="dek">{_e(note['dek'])}</p>
{_chips([('format', 'narrated MP4'), ('voice', preset['label']), ('base', preset['locale']), ('source', dossier)])}
{_video_block(root, note)}
<div class="split"><div class="card"><h3>What this note covers</h3><ul class="rule-list">{what}</ul></div>
<div class="card"><h3>What it is not</h3><ul class="rule-list">{nots}</ul></div></div>
<h2>Read the transcript</h2>
<div class="transcript">{transcript}</div>
<div class="card"><h3>Check the source</h3><p>Every spoken line points back to the published record. The hash is a receipt, not a guarantee that every claim is correct.</p>{source_links}</div>
<h2>Downloads</h2>
<p class="small"><a href="{base}field-note.mp4">MP4</a> · <a href="{base}field-note.vtt">WebVTT captions</a> · <a href="{base}poster.png">Poster</a> · <a href="{base}transcript.md">Transcript</a> · <a href="{base}youtube.md">YouTube metadata draft</a></p>
<nav class="note-nav" aria-label="Field note navigation">{previous_html}{following_html}</nav>
"""
    return _page(f"Field note {note['number']}: {note['title']}",
                 str(note["dek"]), root, "watch", body)


def glossary_html() -> str:
    terms = "".join(
        f'<section class="glossary-term" id="{_e(term.lower().replace(" ", "-"))}"><h3>{_e(term)}</h3><p>{_e(meaning)}</p></section>'
        for term, meaning in GLOSSARY
    )
    body = f"""
<div class="breadcrumb"><a href="../start/">Start here</a> / Glossary</div>
<span class="eyebrow">Glossary</span><h1>The words the room uses, without the fog.</h1>
<p class="dek">A short shared vocabulary for readers, operators and experts. If a term is doing more work than the evidence can support, it is probably hiding a weak claim.</p>
{_chips([('terms', len(GLOSSARY)), ('level', 'plain language'), ('links', 'fact files and method')])}
<div class="glossary-grid">{terms}</div>
<div class="note"><b>Still stuck?</b> Start with the <a href="../start/">three routes</a>, open the <a href="../method/">method</a>, or search the room for the exact term.</div>
"""
    return _page("Glossary", "Plain-language definitions of dossier, finding, locator, confidence, seam, gate, redaction and the other terms used in The Teardown Room.",
                 "../", "glossary", body)


def _search_index(pubs: list[dict], facts: list[dict]) -> list[dict]:
    entries = [
        {"title": "Start here", "url": "start/", "type": "Route", "description": "Three ways into the room for the curious, the beginner and the expert."},
        {"title": "Field notes: ten evidence briefs", "url": "watch/", "type": "Series", "description": "Ten narrated captioned field notes with ten presenter treatments, full transcripts and downloads."},
        {"title": "Evidence library", "url": "evidence/", "type": "Library", "description": "Every published evidence file, with counts, seams and limits."},
        {"title": "Fact files", "url": "facts/", "type": "Evidence", "description": "One claim per page, with the locator, confidence and limit."},
        {"title": "Field guides", "url": "guides/", "type": "Learn", "description": "Practical guides to scope, evidence, redaction, verification, story and publishing."},
        {"title": "Method", "url": "method/", "type": "Learn", "description": "The five stages, the publication gate, probe statuses and the refusals."},
        {"title": "Glossary", "url": "glossary/", "type": "Learn", "description": "Plain-language definitions of the room's evidence vocabulary."},
        {"title": "Proof", "url": "proof/", "type": "Trust", "description": "What is proven, what is not, and the testimonial policy."},
        {"title": "Crew", "url": "crew/", "type": "Trust", "description": "The six production lanes and the hand-offs between them."},
        {"title": "Monthly field notes", "url": "newsletter/", "type": "Room", "description": "Opt in to a short monthly note with one finding, one seam and one limit."},
        {"title": "Support", "url": "pricing/", "type": "Room", "description": "The optional £5 donation and the separate field-note opt-in."},
        {"title": "Download", "url": "download/", "type": "Room", "description": "Download the Engine Room source and run it locally with Python 3.10+."},
    ]
    entries += [
        {"title": f"Field note {note['number']}: {note['title']}", "url": f"watch/{note['number']}/", "type": "Video", "description": str(note["dek"])}
        for note in field_notes.FIELD_NOTES
    ]
    entries += [
        {"title": str(pub.get("title") or "Evidence file"), "url": f"publications/{pub.get('slug')}/", "type": "Evidence file", "description": str(pub.get("verdict") or "")}
        for pub in pubs
    ]
    entries += [
        {"title": str(fact.get("title") or "Fact file"), "url": f"facts/{fact.get('slug')}.html", "type": str(fact.get("group") or "Fact"), "description": str(fact.get("summary") or "")}
        for fact in facts
    ]
    entries += [
        {"title": str(guide.get("title") or "Field guide"), "url": f"guides/{guide.get('slug')}.html", "type": "Field guide", "description": str(guide.get("dek") or "")}
        for guide in GUIDES
    ]
    return entries


# ------------------------------------------------------------------- content --

GUIDES: list[dict] = [
    {
        "slug": "scope-and-authorise",
        "title": "Scope and authorise a teardown",
        "dek": "The boundary comes before the tool. A one-page scope, a written permission and a stop condition are what make the rest of the work publishable.",
        "level": "Start here",
        "time": "8 min",
        "sections": [
            {
                "h": "Write the boundary before you touch the target",
                "p": [
                    "A teardown is not a licence to explore. It is a bounded inspection with an owner, a purpose and a stop condition. If the boundary is vague, the evidence is vague too.",
                    "Put the scope on one page and keep it with the dossier. The page should say what is in scope, what is out of scope, who authorised it, how long it runs and what happens to the captured data.",
                ],
                "ul": [
                    "Target: the exact device, host, repository, account or workflow.",
                    "Owner: the person or organisation that can grant permission.",
                    "Authorisation: the written record, date and any conditions.",
                    "Depth: passive read, offline analysis, runtime observation or a controlled change.",
                    "Data: what may be captured, where it is stored and when it is deleted.",
                    "Stop: the conditions that end the work immediately.",
                ],
            },
            {
                "h": "Authorisation is a record, not a feeling",
                "p": [
                    "You need ownership or written permission. For an employer's system, check the policy and the person who can approve the work. For a client, name the system, the date, the allowed methods and the person who signed.",
                    "Do not treat a public URL as permission to probe. Do not treat a device you found, borrowed or bought second-hand as permission to inspect the previous owner's data. If the authorisation is unclear, stop and write down why.",
                ],
            },
            {
                "h": "Choose a stop condition you will actually honour",
                "p": [
                    "Good stop conditions are specific. 'If I reach production credentials, I stop and preserve the evidence without using them.' 'If the device contains another person's account, I stop and do not copy the data.' 'If a step could interrupt a service, I stop and ask for a maintenance window.'",
                    "A stop condition is not a failure. It is a finding about the boundary. Record it in the dossier and keep it in the published limits.",
                ],
            },
            {
                "h": "The first ten minutes",
                "p": [
                    "Before a deep probe, record the target identity, a hash where possible, the date and the tools. Read the supplied documentation. Take a read-only baseline. Do not change a setting just because you can.",
                ],
                "ul": [
                    "Write the scope note and store it with the run.",
                    "Record the target name, version and owner.",
                    "Hash the artifact or snapshot the source tree.",
                    "List the questions you are trying to answer.",
                    "Run the shallowest probe that can answer the first question.",
                ],
            },
        ],
    },
    {
        "slug": "run-the-evidence-ladder",
        "title": "Run the evidence ladder",
        "dek": "A repeatable sequence of probes, with statuses that tell you what is known, what is empty and what failed. Depth is earned, not assumed.",
        "level": "Core method",
        "time": "9 min",
        "sections": [
            {
                "h": "The ladder is a sequence, not a scan",
                "p": [
                    "The evidence ladder moves from the outside in: identify the surface, read the manifests and documentation, map the code or request surface, then inspect the artifact or runtime only when the earlier steps justify it.",
                    "Each probe writes a status and a result. The next probe is chosen from the result, not from a checklist of impressive-sounding tools.",
                ],
            },
            {
                "h": "Learn the five statuses",
                "p": [
                    "The status is part of the evidence. A negative result is still a recorded result, and it changes what a reader should believe.",
                ],
                "ul": [
                    "Signal: the probe returned a usable observation with a locator.",
                    "Empty: the probe ran and found nothing to report, such as no manifest or no git history.",
                    "Error: the probe failed; record the error and decide whether a retry is safe.",
                    "Skipped: the probe was deliberately not run, often because the network was disabled.",
                    "Retry: the probe was attempted again after a transient failure; say why.",
                ],
            },
            {
                "h": "Use the real command line",
                "p": [
                    "Run the engine against the scoped target. Start offline unless the scope explicitly allows network calls. Use the server when you want the console, but keep the dossier as the source of truth.",
                ],
                "ul": [
                    'python rea/engine.py <target> --offline --json --depth deep',
                    'python rea/engine.py <target> --offline --depth shallow',
                    'python rea/server.py',
                    'python -m unittest discover -s rea -p "test_*.py"',
                ],
            },
            {
                "h": "Do not scale a weak run",
                "p": [
                    "More probes do not rescue a bad scope. If the target is a source tree, a source read is the right depth. If it is a closed device, a firmware and runtime path may be justified. If the evidence is empty, write the next question instead of adding noise.",
                    "A deep run should leave a probe ledger a stranger can read. If the ledger cannot explain why a step happened, the step is not evidence yet.",
                ],
            },
        ],
    },
    {
        "slug": "redact-before-you-publish",
        "title": "Redact secrets and private data",
        "dek": "Publication is a second pass. Replace sensitive values at the source, keep the finding, and never rely on a blur that can be reversed.",
        "level": "Safety gate",
        "time": "7 min",
        "sections": [
            {
                "h": "Redaction is part of the evidence chain",
                "p": [
                    "A finding can be published without the value that proves it. Keep the finding ID, the locator, the confidence and the shape of the observation. Replace the secret-shaped value with a marker such as [redacted].",
                    "Do not publish a screenshot and hope the important part is unreadable. Crop, replace or recreate the image. Metadata, window titles, file paths and notification popups can leak more than the main content.",
                ],
            },
            {
                "h": "The classes to check",
                "p": [
                    "Build a redaction pass into the publication gate. The operator should be able to say what was removed and why without reproducing the value.",
                ],
                "ul": [
                    "Credentials: keys, tokens, passwords, cookies, session IDs and recovery codes.",
                    "Identifiers: serial numbers, MAC addresses, device IDs, account IDs and licence keys.",
                    "People: names, email addresses, phone numbers, faces and customer records.",
                    "Infrastructure: private IPs, SSIDs, hostnames, internal paths and network topology.",
                    "Commercial: pricing, supplier terms, unreleased plans and third-party code you cannot license.",
                ],
            },
            {
                "h": "Replace, do not blur",
                "p": [
                    "Source-level redaction is testable. The public page, the appendix, the video script and the card should all be generated from the redacted record. If a value appears in the dossier, mark it for redaction before packaging.",
                    "Check filenames and alt text too. A file called customer-acme-token.txt is still a leak even if the pixels are clean.",
                ],
            },
            {
                "h": "A redaction check you can repeat",
                "p": [
                    "Before publishing, search the generated output for the known sensitive values and their prefixes. Then read the page as a stranger. If you would not put it on a screen in a public room, do not put it on the site.",
                ],
                "ul": [
                    "Search the public HTML, Markdown and SVG for known values.",
                    "Inspect screenshots at full size, not in a thumbnail.",
                    "Remove EXIF and source-file metadata from images.",
                    "Record the redaction decision in the dossier, not in a private chat.",
                    "If in doubt, omit the value and keep the finding.",
                ],
            },
        ],
    },
    {
        "slug": "verify-a-dossier",
        "title": "Verify a dossier before it becomes public",
        "dek": "The gate is a reading task. Check the claim, the locator, the confidence and the limit before the studio writes a single public word.",
        "level": "Publication gate",
        "time": "10 min",
        "sections": [
            {
                "h": "The gate is deliberately strict",
                "p": [
                    "A completed dossier is not automatically a publishable story. The studio requires an authorisation record, a complete run, a real target, at least three evidence records and at least one mapped change surface.",
                    "Fixtures, demos, example domains and private network captures are refused. A thin run stays in the Engine Room until it has enough evidence to support a public claim.",
                ],
            },
            {
                "h": "Build a claim-to-evidence table",
                "p": [
                    "Every sentence that sounds like a claim should point to a finding ID, a locator and a confidence. If the sentence cannot survive that mapping, rewrite it or remove it.",
                ],
                "ul": [
                    "Claim: the plain-language statement you want to publish.",
                    "Evidence: the finding ID and the exact file, line, request or probe result.",
                    "Confidence: a judgement about the strength of the observation.",
                    "Limit: what the observation does not establish.",
                    "Redaction: what was removed and why.",
                ],
            },
            {
                "h": "Confidence is not a probability",
                "p": [
                    "The confidence number is a judgement, not a calibrated probability. A 95% finding is a direct observation with a strong locator. An 80% finding is strong but may depend on an inference. A 65% finding needs corroboration before it leads a story.",
                    "Do not average confidence into a single score and call it truth. Show the individual findings and the limits.",
                ],
            },
            {
                "h": "Read the appendix like an adversary",
                "p": [
                    "Look for test-only data, duplicate findings, fixture URLs, absolute paths and empty probes presented as success. Check that the public story selects production evidence while the appendix retains the full record.",
                    "A named human signs off the publication. The studio can prepare the page; it cannot grant permission or take responsibility for the claim.",
                ],
            },
        ],
    },
    {
        "slug": "turn-evidence-into-a-story",
        "title": "Turn a dossier into a story",
        "dek": "A story is a selection, not a dump. Lead with scope, finding, seam, proof and limit, and let the appendix carry the rest.",
        "level": "Editorial",
        "time": "8 min",
        "sections": [
            {
                "h": "Use a five-part spine",
                "p": [
                    "The public story has five jobs: say what was inspected, show the strongest findings, name the seam where behaviour can change, prove the claim with the dossier, and state the limit. If a section does not serve one of those jobs, cut it.",
                    "The story is not a diary of every probe. The appendix is the diary. The story is the argued path through it.",
                ],
            },
            {
                "h": "Select production evidence, not the loudest evidence",
                "p": [
                    "Sort findings by confidence and deduplicate. Remove test-only observations from the public story but keep them in the appendix. Do not let a fixture URL or an absolute local path appear in a public page or video script.",
                    "A smaller, accurate set is stronger than a long list of weak observations. Four strong facts and three clean seams can carry a file; ten noisy facts cannot.",
                ],
            },
            {
                "h": "Write headlines that survive the locator",
                "p": [
                    "A headline is a claim. If a reader clicks through, the locator must support it. Use concrete nouns and verbs: 'One environment switch', 'Two entry-point candidates', 'README claims do not match the run'.",
                    "Avoid 'shocking', 'secret' and 'they don't want you to know'. The evidence is interesting because it is specific, not because it is dressed up.",
                ],
            },
            {
                "h": "Give every finding a fact file",
                "p": [
                    "A fact file expands one finding into what was recorded, why it matters, how to verify it and what it does not prove. It gives a reader a stable URL for the detail without forcing them through the whole story.",
                    "The story links to the fact files. The fact files link back to the appendix. The appendix retains the full record.",
                ],
            },
            {
                "h": "The slop test",
                "p": [
                    "Read the page as a sceptic. Ask: where is that written? What was actually run? What would make this false? If the page cannot answer, it is not ready.",
                ],
                "ul": [
                    "Every claim has a finding ID or a recorded source.",
                    "Every number has a unit and a context.",
                    "Every screenshot is of the real artifact and is redacted.",
                    "The limits section is specific, not a disclaimer.",
                    "No customer quote is invented, paraphrased or borrowed.",
                ],
            },
        ],
    },
    {
        "slug": "shoot-the-teardown-video",
        "title": "Shoot the teardown video",
        "dek": "Record the proof before you write the edit. A paper edit, real screen capture and a clear limit beat a cinematic montage with nothing behind it.",
        "level": "Production",
        "time": "9 min",
        "sections": [
            {
                "h": "Start with a paper edit",
                "p": [
                    "The studio generates a shot script from the dossier. Read it before you record. Mark the shots you can actually get, the claims you can show and the places where the evidence is too thin for video.",
                    "If a shot needs a claim that is not in the dossier, run another investigation. Do not fill the gap with stock footage or a dramatic re-enactment.",
                ],
            },
            {
                "h": "Use the zero-cost kit",
                "p": [
                    "A phone, OBS, DaVinci Resolve, Audacity and free assets are enough for the first cut. Buy gear only when a real production constraint proves you need it.",
                ],
                "ul": [
                    "Phone or existing camera for hands, device and room shots.",
                    "OBS Studio for screen capture and clean scenes.",
                    "DaVinci Resolve for the edit, captions and colour.",
                    "Audacity for voice cleanup and noise reduction.",
                    "A free thumbnail tool and copyright-safe music.",
                ],
            },
            {
                "h": "Capture the evidence, not the vibe",
                "p": [
                    "Record the dossier opening on a finding ID. Show the hash card. Show the probe ledger, including one empty or error row. Show the seam on the artifact. Keep serials, names and private data out of frame.",
                    "A static screen with a clear locator is stronger than a moving shot that proves nothing. Let the evidence carry the pace.",
                ],
            },
            {
                "h": "Narrate the limit",
                "p": [
                    "The closing beat is not a sales pitch. Say what was tested, what was not, and what question comes next. Then offer the field guide and one fixed-scope teardown without promising a result.",
                    "Put the dossier ID and hash in the description. Link the evidence page and the appendix. Make the video a door into the evidence, not a replacement for it.",
                ],
            },
        ],
    },
    {
        "slug": "publish-a-static-evidence-site",
        "title": "Publish a static evidence site",
        "dek": "A portable page set with no database, no tracking scripts and no deployment surprise. Generate, inspect, link-check, then put it online.",
        "level": "Publishing",
        "time": "8 min",
        "sections": [
            {
                "h": "Why static",
                "p": [
                    "The site is a set of files generated from the dossier. There is no database to leak, no admin panel to patch and no runtime that can rewrite the evidence. The manifest and hash make the output auditable.",
                    "Static does not mean thin. The same generator can produce an evidence library, fact files, long-form guides, method notes, crew pages, a proof page and pricing.",
                ],
            },
            {
                "h": "The minimum page set",
                "p": [
                    "A single story page is not a publication. A reader needs a way in, a way to check the claim and a way to learn the method.",
                ],
                "ul": [
                    "Home: what the room is, the latest evidence and the main routes.",
                    "Evidence library: every published file with counts and scope.",
                    "Fact files: one page per finding and seam, with verification steps.",
                    "Field guides: the method in readable, reusable form.",
                    "Method and crew: the gate, the roles and the rules.",
                    "Proof: what can be shown now and what is not proven yet.",
                    "Pricing: the field guide, the tip and the paid teardown process.",
                ],
            },
            {
                "h": "Check the links before the domain",
                "p": [
                    "Run the site locally through the same `/published/` route the reader will use. Click every navigation item, every fact file and every download. Check the 404 path, the relative asset path and the mobile layout.",
                    "Do not deploy from a dossier that failed the gate. Do not publish a page with an absolute local path, a test fixture URL or a secret-shaped value.",
                ],
            },
            {
                "h": "Deploy deliberately",
                "p": [
                    "Any static host can serve the folder. Deployment is a separate authorised action: choose the host, set the domain, upload the generated files and verify the live URLs. Keep the previous bundle so you can roll back.",
                    "A deploy is not a publication decision. The gate happens before the upload, not after a bad page is already live.",
                ],
            },
        ],
    },
    {
        "slug": "sell-without-hype",
        "title": "Sell without hype",
        "dek": "The offer follows the proof. One buyer, one painful workflow, one measurable deliverable and no invented social proof.",
        "level": "Commercial",
        "time": "8 min",
        "sections": [
            {
                "h": "Build an offer ladder",
                "p": [
                    "The free evidence file earns attention. There is no paid pack. The optional £5 donation supports the next file, and the monthly field notes are opt-in.",
                    "Do not add a subscription, a course or a mastermind because they sound like revenue. Add an offer only when a real buyer asks for it.",
                ],
            },
            {
                "h": "Name one buyer and one pain",
                "p": [
                    "A buyer is a role with a budget and a problem: an independent repair shop facing an undocumented device, a security lead preparing for diligence, an operator inheriting an abandoned app. One role, one workflow, one before/after.",
                    "Write the buyer's public words into the offer. Do not invent a market size or a customer quote.",
                ],
            },
            {
                "h": "Price the scope, not the dream",
                "p": [
                    "A scoped teardown needs a written scope: target, questions, depth, deliverables, exclusions, timeline and price. No success fee, no guaranteed finding, no access to systems you cannot legally inspect.",
                    "There is one public £5 donation link. Do not add a paid pack or subscription to make the page look bigger. Never put a secret or restricted Stripe key in the site.",
                ],
            },
            {
                "h": "Outreach is diagnosis, not spam",
                "p": [
                    "Lead with a specific observation about the buyer's public problem. Offer the relevant evidence file. Ask one question. Do not send a bulk pitch, buy a list or borrow a logo.",
                    "Ten thoughtful conversations beat a thousand emails. The first sale comes from a real buyer and delivered work, not from a launch tweet.",
                ],
            },
            {
                "h": "Testimonials need permission",
                "p": [
                    "Publish a testimonial only when it is attributable, specific, given with written permission and connected to work you actually delivered. Say whether the person paid. Never write a quote for a customer.",
                    "Until then, the proof page should say there are no customer testimonials. That is more credible than three anonymous five-star cards.",
                ],
            },
        ],
    },
    {
        "slug": "write-a-monthly-field-note",
        "title": "Write a monthly field note",
        "dek": "One useful observation, one seam, one limit and a clear way to leave. A monthly note is a discipline, not a content quota.",
        "level": "Publishing",
        "time": "7 min",
        "sections": [
            {
                "h": "One useful thing, not a digest",
                "p": [
                    "A monthly note is not a feed and not a newsletter full of links. It is one short argument from the evidence: what was observed, why it matters, how to check it and what remains unknown.",
                    "If there is no new evidence, say that. A note that admits the room is between files is more useful than a manufactured finding.",
                ],
                "ul": [
                    "One finding or one change surface from a published dossier.",
                    "One practical action the reader can take on their own equipment.",
                    "One limit that stops the note becoming a promise.",
                    "One link to the fact file or evidence appendix.",
                ],
            },
            {
                "h": "Start from the dossier, not the calendar",
                "p": [
                    "Open the latest publication and select a fact file that has a locator and a confidence. Use the recorded wording, then cut it to the length of a note. Do not introduce a new claim because the month needs a theme.",
                    "The monthly note is a route back to the public evidence, not a replacement for it. Keep the dossier ID and the limit visible.",
                ],
            },
            {
                "h": "Make the list a choice",
                "p": [
                    "Ask for explicit consent, explain what will arrive and offer a way out in every message. Do not buy a list, scrape an address book or add someone because they donated.",
                    "A donation and a subscription are separate choices. The £5 link can sit next to the form, but the form needs its own unchecked consent box and the donor must be able to decline.",
                ],
            },
            {
                "h": "Keep a rhythm without a quota",
                "p": [
                    "Draft the note from the latest evidence, review it against the gate, then send it from the public contact address. If the month has no evidence, publish a short honest note rather than filling the gap.",
                    "Keep the sent issue in the archive so a new reader can see the standard. Do not edit a sent issue silently; add a correction if something was wrong.",
                ],
                "ul": [
                    "Draft: generated from the latest published fact files.",
                    "Review: check the claim, locator, confidence and limit.",
                    "Send: the owner sends from the configured address; the local build does not auto-send.",
                    "Archive: keep the issue, the date and any correction.",
                ],
            },
            {
                "h": "Measure replies, not vanity",
                "p": [
                    "A useful note produces a question, a correction or a reader trying the method. A large open rate with no replies is not evidence of value.",
                    "Record unsubscribes without argument. A clean list of people who chose to be there is worth more than a large list that never asked.",
                ],
            },
        ],
    },
]


# ------------------------------------------------------------------- cards --

def _pub_card(pub: dict, root: str, compact: bool = False) -> str:
    slug = str(pub.get("slug") or "")
    image = f"{root}publications/{_e(slug)}/card.svg"
    paragraphs = "" if compact else f"<p>{_e(pub.get('verdict') or '')}</p>"
    return f"""<article class="card story-card">
<a href="{root}publications/{_e(slug)}/"><img src="{image}" alt="Evidence card for {_e(pub.get('title'))}"></a>
<div><span class="eyebrow">{_e(pub.get('kind_label') or 'Evidence file')}</span>
<h2><a href="{root}publications/{_e(slug)}/">{_e(pub.get('title'))}</a></h2>
{paragraphs}
<div class="meta"><span class="chip">{int(pub.get('findings') or 0)} evidence</span>
<span class="chip">{int(pub.get('edits') or 0)} seams</span>
<span class="chip">{_e((pub.get('published_at') or '')[:10])}</span></div>
<a class="btn" href="{root}publications/{_e(slug)}/">Read the evidence file</a>
<a class="btn" href="{root}facts/">Open the fact files</a></div></article>"""


def _fact_card(fact: dict, root: str) -> str:
    return f"""<article class="card fact-card">
<span class="fact-code">{_e(fact.get('code'))}</span>
<span class="eyebrow">{_e(fact.get('group'))}</span>
<h3><a href="{root}facts/{_e(fact.get('slug'))}.html">{_e(fact.get('title'))}</a></h3>
<p>{_e(_trim(fact.get('summary'), 190))}</p>
<div class="meta"><span class="chip">confidence <b>{_e(fact.get('confidence'))}%</b></span>
<span class="chip">{_e(fact.get('kind'))}</span></div></article>"""


def _guide_card(guide: dict, root: str) -> str:
    return f"""<article class="card guide-card">
<span class="eyebrow">{_e(guide.get('level'))} · {_e(guide.get('time'))}</span>
<h3><a href="{root}guides/{_e(guide.get('slug'))}.html">{_e(guide.get('title'))}</a></h3>
<p>{_e(guide.get('dek'))}</p>
<a class="btn" href="{root}guides/{_e(guide.get('slug'))}.html">Read the guide</a></article>"""


def _price_cards(root: str = "") -> str:
    return f"""<div class="price-grid price-single">
<article class="card price"><span class="eyebrow">Optional support</span><div class="amount">£5</div>
<h3>HeliosTD donation</h3><p>Support the next evidence file with a one-time £5 donation. It is not a pack, a subscription or a deliverable.</p>
<ul><li>One-time donation</li><li>No paid pack</li><li>No subscription</li><li>No priority access</li></ul>
<a class="btn gold" href="{_e(STRIPE['donation_link'])}" rel="noreferrer">Leave a £5 donation</a>
<a class="btn" href="{root}pricing/#after-donation">Request monthly field notes</a></article>
</div>"""


def _quote_cards(pubs: list[dict]) -> str:
    verdict = (pubs[0].get("verdict") if pubs else "No public dossier verdict yet.")
    return f"""<div class="cards-3 quote-grid">
<figure class="quote"><blockquote>“I want the App to be the engine room, for me and the Crew, this will not be shared, but the created outputs should be publishable to a website with real evidence not mock items, it has to be quality.”</blockquote><figcaption>Founder brief · 21 November 2026</figcaption></figure>
<figure class="quote"><blockquote>“{_e(verdict)}”</blockquote><figcaption>Recorded verdict · dossier {_e(pubs[0].get('dossier_id') if pubs else 'not yet published')}</figcaption></figure>
<figure class="quote"><blockquote>“No dossier, no publication. A story must be built from a completed, authorised investigation.”</blockquote><figcaption>Editorial rule · The Teardown Room</figcaption></figure>
</div>"""


def _field_notes(facts: list[dict]) -> str:
    selected = [fact for fact in facts if fact.get("code") not in {"SCOPE", "PROBES"}][:6]
    if not selected:
        return '<div class="empty"><b>No field notes yet.</b><p>They appear when a published dossier has selected fact files.</p></div>'
    cards = "".join(
        f'<figure class="quote"><blockquote>“{_e(_trim(fact.get("summary"), 240))}”</blockquote>'
        f'<figcaption>{_e(fact.get("code"))} · {_e(fact.get("group"))} · <a href="../facts/{_e(fact.get("slug"))}.html">open fact file</a></figcaption></figure>'
        for fact in selected
    )
    return '<div class="cards-3 quote-grid">' + cards + "</div>"


def _lane_cards(lanes: list[dict]) -> str:
    cards = []
    for lane in lanes:
        roles = ", ".join(person.get("name", "") for person in lane.get("crew", []))
        cards.append(
            f'<article class="card lane"><span class="eyebrow">{_e(lane.get("name"))}</span>'
            f'<h3>{_e(lane.get("output"))}</h3><p class="small">{_e(roles)}</p></article>'
        )
    return '<div class="cards-3">' + "".join(cards) + "</div>"


# -------------------------------------------------------------------- pages --

def home_html(pubs: list[dict], facts: list[dict], lanes: list[dict]) -> str:
    evidence_count = sum(int(pub.get("findings") or 0) for pub in pubs)
    latest = _pub_card(pubs[0], "", compact=False) if pubs else (
        '<div class="empty"><b>No evidence file has passed the gate yet.</b>'
        '<p>The room will not publish a fixture, a demo or a thin dossier. The first file appears when a real investigation is complete.</p></div>'
    )
    fact_cards = "".join(_fact_card(fact, "") for fact in facts[:6]) or (
        '<div class="empty"><b>No fact files yet.</b><p>Fact files are generated from selected production findings in a published dossier.</p></div>'
    )
    guide_cards = "".join(_guide_card(guide, "") for guide in GUIDES[:6])
    body = f"""
<section class="hero"><span class="eyebrow">Evidence-led reverse engineering</span>
<h1>The box is open. The receipts are on the table.</h1>
<p class="dek">An evidence-led teardown room for devices, apps and workflows we own or have written permission to inspect. Every claim carries a finding ID, a locator, a confidence and a limit. No stock teardowns. No invented testimonials. No dossier, no publication.</p>
<div class="hero-actions">{_link('Read the latest evidence file', 'evidence/', 'gold')}{_link('Choose your way in', 'start/')}{_link('Watch the ten field notes', 'watch/')}</div>
<div class="stat-grid">
<div class="stat"><b>{len(pubs)}</b><span>evidence files</span></div>
<div class="stat"><b>{evidence_count}</b><span>evidence records</span></div>
<div class="stat"><b>{len(facts)}</b><span>fact files</span></div>
<div class="stat"><b>{len(GUIDES)}</b><span>field guides</span></div>
<div class="stat"><b>6</b><span>crew lanes</span></div>
</div></section>

<section><div class="section-head"><div><span class="eyebrow">Choose your way in</span><h2>Curious, beginner or expert?</h2>
<p>Three routes, one standard. The short version, the method, or the receipt. You do not have to read the room in the same order as anyone else.</p></div><a href="start/">Open the start page →</a></div>
{_path_cards("", pubs)}</section>

<section><div class="section-head"><div><span class="eyebrow">Field notes · ten briefs</span><h2>Ten ways into the same evidence</h2>
<p>Different subjects, different card accents and ten presenter treatments from three local voices. Every note keeps the limit attached. Start with the opening brief or pick the question that brought you here.</p></div><a href="watch/">Open the series →</a></div>
<div class="split"><div>{_video_block("", field_notes.FIELD_NOTES[0])}</div><div class="card"><h3>In the series</h3><ol class="path-list"><li><a href="watch/02/">The publication gate</a></li><li><a href="watch/03/">The shape of the file</a></li><li><a href="watch/04/">A seam is a place, not a change</a></li><li><a href="watch/05/">The empty probe is evidence</a></li><li><a href="watch/06/">Redact the value. Keep the finding.</a></li><li><a href="watch/07/">Confidence is not a probability</a></li><li><a href="watch/08/">The receipt is not the proof</a></li><li><a href="watch/09/">A story is a selection</a></li><li><a href="watch/10/">What the room refuses</a></li></ol><div class="path-actions">{_link("All ten field notes", "watch/", "gold")}</div></div></div></section>

<section class="split"><div class="card"><span class="eyebrow">What this is</span><h2>A working evidence room</h2>
<p>A place to open an owned device, app or workflow, map what is actually there and show the seam where a repair, rebuild or better design becomes possible. The public file is generated from a local dossier, not written from memory.</p>
<p>The room includes the evidence library, per-finding fact files, long-form field guides, the publication method, the crew roles and a proof page that says what is known and what is not.</p></div>
<div class="card"><span class="eyebrow">What this is not</span><h2>Not a hacking shop</h2>
<p>No exploits, stolen data, control bypasses, borrowed credibility or five-star quotes with no name attached. No “review” unit we do not own. No promise that a teardown will produce a finding or that a buyer will appear.</p>
<p>If the evidence is thin, the gate blocks it. If a testimonial does not exist, the page says so. If a probe returned nothing, the limit stays visible.</p></div></section>

<section><div class="section-head"><div><span class="eyebrow">Latest evidence</span><h2>Start with the file that passed the gate</h2></div><a href="evidence/">All evidence files →</a></div>
{latest}</section>

<section><div class="section-head"><div><span class="eyebrow">Fact files</span><h2>One page per claim you can check</h2>
<p>Each fact file expands a single finding or change surface: what was recorded, why it matters, how to verify it and what it does not prove.</p></div><a href="facts/">All {len(facts)} fact files →</a></div>
<div class="cards-3">{fact_cards}</div></section>

<section><div class="section-head"><div><span class="eyebrow">Field guides</span><h2>The method, written down</h2>
<p>{len(GUIDES)} practical guides covering scope, evidence, redaction, verification, story, video, static publishing, monthly field notes and selling without hype.</p></div><a href="guides/">All {len(GUIDES)} guides →</a></div>
<div class="cards-3">{guide_cards}</div></section>

<section><div class="section-head"><div><span class="eyebrow">The room in five moves</span><h2>From a real target to a published file</h2></div><a href="method/">Read the full method →</a></div>
<ol class="steps"><li><b>Scope.</b> Name the owner, the permission, the depth and the stop condition.</li>
<li><b>Probe.</b> Run the evidence ladder and record signal, empty, error, skipped and retry.</li>
<li><b>Verify.</b> Map each claim to a finding ID, locator, confidence and limit. Redact before publishing.</li>
<li><b>Package.</b> Generate the story, evidence appendix, fact files, video script, card and hash.</li>
<li><b>Publish and share.</b> Put the static file online, then offer the optional £5 donation and the opt-in monthly field notes. No pack, no subscription.</li></ol></section>

<section><div class="section-head"><div><span class="eyebrow">Proof, not performance</span><h2>What can be shown today</h2>
<p>These are field notes and recorded evidence, not customer testimonials. The proof page states that distinction plainly.</p></div><a href="proof/">Read the proof page →</a></div>
{_quote_cards(pubs)}</section>

<section><div class="section-head"><div><span class="eyebrow">Crew</span><h2>Six lanes, one evidence chain</h2>
<p>The room is built for an operator and a crew: evidence, verification, story, video, publish and sell.</p></div><a href="crew/">Meet the crew →</a></div>
{_lane_cards(lanes)}</section>

<section id="download"><div class="section-head"><div><span class="eyebrow">Download</span><h2>Run the room on your own machine</h2>
<p>The Engine Room is local-first and free to run: Python 3.10+, no account, no key. Download the source, start the console on loopback, and keep every dossier on your own disk.</p></div><a href="download/">Download and setup →</a></div>
<div class="card"><div class="hero-actions">{_link('Download ZIP', ZIP_URL, 'gold')}{_link('Open GitHub', REPO_URL)}</div>
<p class="small">Windows: run <code>REA.bat</code>. Any OS: <code>python rea/server.py</code>. Then open <code>127.0.0.1:4711</code>. Authorised targets only.</p></div></section>

<section><div class="section-head"><div><span class="eyebrow">Support</span><h2>One optional £5 donation. Nothing else to buy.</h2>
<p>There is no paid pack and no subscription. If the evidence is useful, you can leave a one-time donation. It buys no deliverable.</p></div><a href="pricing/">Read the support page →</a></div>
{_price_cards()}</section>

<section id="field-notes"><div class="section-head"><div><span class="eyebrow">Monthly field notes</span><h2>One useful note, once a month</h2>
<p>A short evidence note from the room: one finding, one seam, one limit and a link to check it. Opt in; leave whenever you want. No daily feed.</p></div><a href="newsletter/">Read the field notes →</a></div>
<div class="card">{_subscribe_form("home")}</div></section>

<section><span class="eyebrow">Questions</span><h2>Before you go deeper</h2>
<div class="faq">
<details><summary>Is this a hacking site?</summary><p>No. The room only inspects targets the operator owns or has written permission to inspect. It publishes evidence, repairs and design seams, not exploits or bypass steps.</p></details>
<details><summary>Why is there only one evidence file?</summary><p>The publication gate refuses thin, synthetic or unauthorised dossiers. One real file is more useful than twenty mock ones. New files appear only when a real investigation is complete.</p></details>
<details><summary>Are the testimonials real?</summary><p>There are no customer testimonials yet. The proof page uses a real project brief, a recorded dossier verdict and an editorial rule, each labelled. It will publish a customer quote only with a name, permission and a link to the delivered work.</p></details>
<details><summary>What is a fact file?</summary><p>A stable page for one finding or change surface. It gives the observation, the locator, the confidence, a verification path and the limit. It is not a marketing summary.</p></details>
<details><summary>Is there a paid pack?</summary><p>No. There is no paid pack. There is one optional £5 donation link. The evidence, fact files, method and field guides are free to read.</p></details>
<details><summary>How do I get updates?</summary><p>Use the monthly field-note form. It is opt-in, asks only for an email address and an optional first name, and stores the request locally. You can unsubscribe at any time.</p></details>
<details><summary>Are the field notes finished?</summary><p>There are ten finished short narrated captioned field notes generated from the real dossier. They use three local Windows OneCore voices in ten presenter treatments. There is no recorded teardown video and no client footage. The notes are a door into the evidence, not a replacement for it.</p></details>
</div></section>

<section><span class="eyebrow">The rules</span><h2>What the room will not do</h2>
<div class="card"><ul class="rule-list">{''.join(f'<li>{_e(rule)}</li>' for rule in media.RULES)}</ul></div></section>
"""
    return _page("Evidence, fact files and field guides",
                 "Evidence-led teardowns, per-finding fact files and practical field guides. Every public claim has a finding ID, a locator, a confidence and a limit.",
                 "", "home", body)


def evidence_html(pubs: list[dict], facts: list[dict]) -> str:
    cards = "".join(_pub_card(pub, "../") for pub in pubs) or (
        '<div class="empty"><b>No evidence file has been published yet.</b>'
        '<p>The studio refuses a fixture, a demo or a thin dossier. A story appears here only after a completed, authorised investigation passes the gate.</p></div>'
    )
    fact_links = "".join(
        f'<li><a href="../facts/{_e(fact.get("slug"))}.html">{_e(fact.get("code"))} · {_e(fact.get("title"))}</a></li>'
        for fact in facts[:9]
    ) or "<li>Fact files appear after the first publication.</li>"
    body = f"""
<span class="eyebrow">Evidence library</span><h1>Every public file, with its limits attached</h1>
<p class="dek">This is the shelf, not a feed. Each file is generated from a completed, authorised dossier. The counts below are the recorded counts, not an estimate.</p>
{_chips([('files', len(pubs)), ('evidence records', sum(int(p.get('findings') or 0) for p in pubs)), ('scope', 'authorised only')])}
<div class="hero-actions">{_link('Watch the ten field notes', '../watch/', 'gold')}{_link('Start with the short route', '../start/')}</div>
<div class="cards">{cards}</div>

<h2>What every evidence file contains</h2>
<div class="cards-2">
<div class="card"><h3>The story</h3><p>A plain-language path through the strongest production findings: scope, evidence, seam, proof and limit.</p></div>
<div class="card"><h3>The fact files</h3><p>One stable page for each selected finding and change surface, with a verification path and the limit.</p></div>
<div class="card"><h3>The appendix</h3><p>The full record, including test-only observations, duplicates, empty probes and errors. It is the audit trail.</p></div>
<div class="card"><h3>The video and script</h3><p>A short captioned explainer generated from the dossier, plus the longer paper edit with timings, shot directions and the evidence IDs to show.</p></div>
<div class="card"><h3>The card and hash</h3><p>A share card and a SHA-256 digest of the dossier. The hash is the receipt that the page came from a specific record.</p></div>
<div class="card"><h3>The limits</h3><p>What was not tested, what returned nothing and what question comes next. A block is a finding, not a challenge.</p></div>
</div>

<h2>Read in this order</h2>
<ol class="steps"><li><b>Read the latest story.</b> Start with the claim and the seam.</li>
<li><b>Open the fact files.</b> Check the finding, locator and verification path.</li>
<li><b>Open the appendix.</b> Read the full probe ledger, including the empty and error rows.</li>
<li><b>Open the video and script.</b> Watch the short explainer, then read the longer paper edit.</li></ol>

<h2>Fact files from this shelf</h2>
<div class="card"><ul class="rule-list">{fact_links}</ul></div>

<div class="note"><b>Why the shelf is short:</b> the room does not publish a file because a page is empty. It publishes when a real dossier has enough evidence and at least one mapped seam. The gate is the reason the shelf can be trusted.</div>
"""
    return _page("Evidence library", "Every published REA evidence file, with evidence counts, seams, fact files and stated limits.",
                 "../", "evidence", body)


def facts_html(facts: list[dict]) -> str:
    groups = ("Target facts", "Change surfaces", "Run facts")
    sections = []
    for group in groups:
        items = [fact for fact in facts if fact.get("group") == group]
        if not items:
            continue
        sections.append(
            f'<h2>{_e(group)}</h2><p class="small">{_e(_GROUP_NOTES.get(group, ""))}</p>'
            f'<div class="cards-3">{"".join(_fact_card(fact, "../") for fact in items)}</div>'
        )
    body = f"""
<span class="eyebrow">Fact files</span><h1>One claim per page, with the receipt</h1>
<p class="dek">A fact file is the smallest useful unit of the evidence room. It takes one finding or one change surface and shows what was recorded, why it matters, how to verify it and what it does not prove.</p>
{_chips([('fact files', len(facts)), ('groups', len(groups)), ('source', 'published dossiers')])}
<div class="cards-2"><div class="card"><h3>What you will find</h3><p>The exact observation, the source locator, the confidence, the related seam and a plain-language limit. No new claim is introduced.</p></div>
<div class="card"><h3>What you will not find</h3><p>No invented exploit, no score out of ten, no anonymous customer quote and no “trust me” summary. If the dossier did not record it, the fact file does not say it.</p></div></div>
{''.join(sections) or '<div class="empty"><b>No fact files yet.</b><p>Build a publication from a completed dossier to generate them.</p></div>'}
<h2>How to read a fact file</h2>
<ol class="steps"><li><b>Read the claim.</b> It should be one sentence you can check.</li>
<li><b>Follow the locator.</b> Open the file, line or probe result named on the page.</li>
<li><b>Check the confidence.</b> A direct observation is stronger than an inference.</li>
<li><b>Read the limit.</b> It tells you what the observation does not establish.</li></ol>
"""
    return _page("Fact files", "Per-finding fact files from the real evidence dossier: observation, locator, confidence, verification and limit.",
                 "../", "facts", body)


_GROUP_NOTES = {
    "Target facts": "What the target is made of and where its public surfaces begin. These are maps, not vulnerabilities.",
    "Change surfaces": "The recorded places where behaviour can be altered or repaired. A seam is not a claim that a change was made.",
    "Run facts": "The frame around the evidence: scope, authorisation, counts, probe statuses and the questions that remain.",
}


def guides_html() -> str:
    cards = "".join(_guide_card(guide, "../") for guide in GUIDES)
    body = f"""
<span class="eyebrow">Field guides</span><h1>The method, without the mystique</h1>
<p class="dek">{len(GUIDES)} guides for running an evidence-led teardown: scope, evidence, redaction, verification, story, video, static publishing, monthly field notes and selling. Written to be used on the next target, not admired on a shelf.</p>
{_chips([('guides', len(GUIDES)), ('format', 'long-form'), ('level', 'operator')])}
<div class="cards-2"><div class="card"><h3>New to the room?</h3><p>Start with the <a href="../start/">three audience routes</a>: watch the narrated explainer, read one fact file, or go straight to the dossier. Then use the guides in order: scope → evidence → redaction → verification → story → video → publish → field notes → support.</p></div>
<div class="card"><h3>No exploit content</h3><p>These guides cover evidence, repair and publication on authorised targets. They do not teach control bypass, credential theft or unauthorised access.</p></div></div>
<div class="cards-3">{cards}</div>
<h2>What every guide assumes</h2>
<ul class="rule-list"><li>You own the target or have written permission to inspect it.</li>
<li>You will redact secrets and third-party data before publishing.</li>
<li>You will state the limits, including the probes that returned nothing.</li>
<li>You will not invent a result, a customer quote or a revenue claim.</li></ul>
"""
    return _page("Field guides", "Practical guides to scoped, evidence-led teardowns, publishing without hype and writing a monthly field note.",
                 "../", "guides", body)


def download_html() -> str:
    body = f"""
<span class="eyebrow">Download</span><h1>Run the room on your own machine.</h1>
<p class="dek">The Engine Room is a local-first Python console. Download the source, run it on your own computer, and point it only at systems you own or have written permission to inspect. No account, no API key and no cloud dependency.</p>
{_chips([("requires", "Python 3.10+"), ("runs on", "Windows · macOS · Linux"), ("data", "stays local")])}
<div class="hero-actions">{_link('Download ZIP', ZIP_URL, 'gold')}{_link('Open the GitHub repository', REPO_URL)}</div>

<div class="download-grid">
<div class="card"><h3>What you get</h3>
<ul class="rule-list">
<li>The Engine Room console and the fixed probe ladder.</li>
<li>Local dossiers, Improve plans and the crew decision layer.</li>
<li>The gated executor: apply, verify and reverse a local patch.</li>
<li>The static site generator, the crew roster and the Mailroom.</li>
</ul></div>
<div class="card"><h3>What is not included</h3>
<ul class="rule-list">
<li>No owner data: no dossiers, contacts, payment settings or notes.</li>
<li>No hosted service and no automatic updates.</li>
<li>No licence has been declared yet, so default copyright applies.</li>
<li>No guarantee that a probe finds a seam.</li>
</ul></div>
</div>

<div class="note"><b>What the download contains:</b> the Engine Room, the full source, a local preview of this public website at <code>/published/</code>, and an empty Mailroom at <code>/mailroom/</code>. It contains no dossiers, contact list, payment configuration or owner notes, and it cannot change this site or its data.</div>

<h2>Install in four steps</h2>
<ol class="steps">
<li><b>Get the code.</b> Download the ZIP and unpack it, or clone with Git:<pre class="shell"><code>git clone {REPO_URL}.git</code></pre></li>
<li><b>Open the folder.</b><pre class="shell"><code>cd heliosrea</code></pre></li>
<li><b>Start the room.</b> On Windows run <code>REA.bat</code>. On any OS run:<pre class="shell"><code>python rea/server.py</code></pre></li>
<li><b>Open the console.</b> Visit <a href="http://127.0.0.1:4711/">http://127.0.0.1:4711/</a>. The server binds to loopback only.</li>
</ol>

<div class="note"><b>Before your first run:</b> you need Python 3.10 or newer on your PATH. Nothing is installed with pip, and no account or key is needed. The console writes dossiers to <code>rea/data/</code> on your machine.</div>

<h2>What to do first</h2>
<p>Read the <a href="../start/">short start route</a>, then run one investigation against a small folder you own. Open <b>Improve</b>, write one outcome, and let the crew choose a move. The plan is a draft; the patch is applied only when you confirm it.</p>

<h2>Safety and limits</h2>
<div class="cards-2">
<div class="card"><h3>Authorised targets only</h3><p>Only inspect systems you own or have written permission to assess. The console refuses unauthorised investigations and blocks fixture or demo targets from publication.</p></div>
<div class="card"><h3>Reversible by design</h3><p>Automatic execution is limited to software or web plans on a clean local git work tree. Verification failure reverses the patch, and every applied plan can be rolled back.</p></div>
</div>

<h2>Keep up with the room</h2>
<p>The <a href="../watch/">ten field notes</a> are the shortest route into the evidence. The <a href="../newsletter/">monthly note</a> is opt-in and free.</p>

<div class="note"><b>Repository:</b> <a href="{REPO_URL}" rel="noreferrer">{REPO_URL}</a><br><b>Download:</b> <a href="{ZIP_URL}" rel="noreferrer">main branch ZIP</a></div>
"""
    return _page("Download",
                 "Download the Engine Room source, run it locally with Python 3.10+, and inspect only targets you own or have written permission to assess.",
                 "../", "download", body)


def method_html() -> str:
    body = f"""
<span class="eyebrow">Method</span><h1>Evidence first. Story second. Sale last.</h1>
<p class="dek">The room has one job: turn an authorised investigation into a public file that a sceptical reader can check. This page is the operating method behind every guide, fact file and story.</p>

<div class="cards-2"><div class="card"><h3>The promise</h3><p>Every public claim has a finding ID, a locator, a confidence and a limit. The page is generated from a dossier, not written from memory.</p></div>
<div class="card"><h3>The refusal</h3><p>No fixture, demo, example host or private network capture is published automatically. A thin dossier stays in the Engine Room.</p></div></div>

<h2>The five stages</h2>
<div class="prose">
<section class="prose-block"><h3>1. Scope</h3><p>Name the target, owner, permission, depth, data handling and stop condition. The scope is one page and it travels with the dossier. If the permission is unclear, the run does not start.</p></section>
<section class="prose-block"><h3>2. Evidence</h3><p>Run the ladder from the outside in. Record signal, empty, error, skipped and retry. Choose the next probe from the result, not from a tool list. Keep the raw locator for every finding.</p></section>
<section class="prose-block"><h3>3. Verify</h3><p>Map the claim to the evidence. Check duplicates, fixture data and absolute paths. Redact secrets and private data at the source. Read the appendix like an adversary.</p></section>
<section class="prose-block"><h3>4. Package</h3><p>The studio writes the story, fact files, evidence appendix, video script, card and manifest. The public story selects production evidence; the appendix keeps the full record.</p></section>
<section class="prose-block"><h3>5. Publish and share</h3><p>Put the static folder online only after the gate and a link check. Offer the optional £5 donation and the opt-in field notes next to the proof. There is no paid pack and no promised outcome.</p></section>
</div>

<h2>What the publication gate checks</h2>
<div class="card"><ul class="rule-list"><li>Operator attested ownership or written permission.</li>
<li>Investigation status is complete.</li>
<li>Target is not a fixture, demo, example domain or private network capture.</li>
<li>At least three evidence records and one mapped change surface.</li>
<li>Findings retain an ID, locator and confidence; secrets are redacted.</li></ul></div>

<h2>Probe statuses</h2>
{_table(["Status", "What it means", "What to do next"], [
    ["signal", "A usable observation with a locator.", "Map it to a finding and a confidence."],
    ["empty", "The probe ran and found nothing to report.", "Keep it visible; write the next question."],
    ["error", "The probe failed.", "Record the error; retry only if it is safe."],
    ["skipped", "Deliberately not run.", "Say why, especially if the network was disabled."],
    ["retry", "Attempted again after a transient failure.", "Record the reason and the final result."],
])}

<h2>Confidence is a judgement</h2>
{_table(["Range", "How to read it", "Publication use"], [
    ["90–100%", "Direct observation with a strong locator.", "Can lead a story if the limit is stated."],
    ["75–89%", "Strong evidence, possibly one inference.", "Use with corroboration and a clear locator."],
    ["60–74%", "Useful but incomplete.", "Supporting evidence, not the headline."],
    ["Below 60%", "Weak or heavily inferred.", "Keep in the appendix until another probe confirms it."],
])}

<h2>What the room refuses</h2>
<div class="cards-2"><div class="card"><h3>Targets</h3><ul class="rule-list"><li>Devices, accounts or systems without ownership or written permission.</li><li>Production systems without a maintenance window and a rollback.</li><li>Private WiFi/LAN captures or personal data as public content.</li><li>Example domains, fixtures, demos and synthetic dossiers.</li></ul></div>
<div class="card"><h3>Content</h3><ul class="rule-list"><li>Exploit steps, credential theft or control bypass.</li><li>Invented findings, market sizes or customer quotes.</li><li>Absolute local paths or test fixture URLs in a public story.</li><li>Guaranteed income, guaranteed findings or fake urgency.</li></ul></div></div>

<h2>Glossary</h2>
{_table(["Term", "Meaning"], [
    ["Dossier", "The local record of an investigation: target, scope, probes, findings, seams and limits."],
    ["Finding", "One recorded observation with an ID, family, title, locator and confidence."],
    ["Change surface", "A place where behaviour can be altered or repaired. A seam, not a change that was made."],
    ["Fact file", "A public page expanding one finding or seam, with verification and limits."],
    ["Gate", "The publication check that refuses unauthorised, synthetic or thin work."],
    ["Appendix", "The full evidence record, including test-only observations and duplicates."],
    ["Paper edit", "A shot script built from the dossier before any video is recorded."],
    ["Manifest", "The publication metadata and hash that identifies the generated file."],
])}

<div class="note"><b>The honest limit:</b> the method reduces slop; it does not make an investigation complete. A confident page can still be wrong. The locator and the limit are there so a reader can check.</div>
"""
    return _page("Method", "The five-stage REA method: scope, evidence, verification, packaging and publication, with the gate and refusals.",
                 "../", "method", body)


def crew_html(lanes: list[dict]) -> str:
    lane_cards = _lane_cards(lanes)
    roles = _table(
        ["Role", "What they own", "Tools"],
        [[person.get("name"), person.get("focus"), ", ".join(person.get("tools") or ())]
         for person in playbook.PROFESSIONS],
    )
    body = f"""
<span class="eyebrow">Crew</span><h1>Six lanes, one evidence chain</h1>
<p class="dek">The room is designed for an operator and a crew. The crew does not replace the evidence; each lane has a hand-off and a named output.</p>
{_chips([('lanes', len(lanes)), ('professions', len(playbook.PROFESSIONS)), ('hand-off', 'evidence → publish')])}
{lane_cards}
<h2>How the lanes hand off</h2>
<ol class="steps"><li><b>Evidence → Verification.</b> The dossier, locators and raw observations move to the fact-checker. Nothing is public yet.</li>
<li><b>Verification → Story and Video.</b> The claim-to-evidence table becomes the editorial spine and the shot list. Limits travel with both.</li>
<li><b>Story and Video → Publish and Share.</b> The package is generated, link-checked and only then offered next to the optional donation and the field-note signup.</li></ol>
<h2>All {len(playbook.PROFESSIONS)} professions</h2>
<p class="small">A lane can call on these roles. The list is a capability map, not a claim that every role is staffed on every file.</p>
{roles}
<div class="note"><b>Rule:</b> a named human signs off the publication. The crew can prepare, challenge and package the evidence; it cannot grant permission or take responsibility for the claim.</div>
"""
    return _page("Crew", "The six production lanes and seventeen professions behind The Teardown Room, including the monthly field-note hand-off.",
                 "../", "crew", body)


def proof_html(pubs: list[dict], facts: list[dict]) -> str:
    verdict = (pubs[0].get("verdict") if pubs else "No public dossier verdict yet.")
    dossier = (pubs[0].get("dossier_id") if pubs else "not yet published")
    body = f"""
<span class="eyebrow">Proof and feedback</span><h1>Testimonials, without theatre</h1>
<p class="dek">There are no customer testimonials yet. This page will not invent them. What it can show today is the project brief, the recorded verdict, the editorial rule and six evidence notes from the real dossier—each labelled for what it is.</p>
<div class="note"><b>Current status:</b> no paid customer testimonial has been published. The room is new. A quote appears here only with a name, permission and a link to the delivered work.</div>
{_quote_cards(pubs)}
<h2 id="field-notes">More field notes from the dossier</h2>
<p class="small">These are recorded observations from dossier {_e(dossier)}, not customer testimonials. Each links to a fact file with the locator, confidence and limit.</p>
{_field_notes(facts)}
<h2>What a testimonial must include</h2>
<div class="card"><ul class="rule-list"><li>The person's name and role, or a documented reason for anonymity.</li><li>Written permission to publish the quote and the attribution.</li><li>The date and the work it refers to.</li><li>A link to the evidence file, guide or delivered output.</li><li>Whether the person paid, and whether anything was discounted.</li><li>The original wording, with edits disclosed.</li></ul></div>
<h2>Proof ledger</h2>
{_table(["Claim", "What backs it", "Status"], [
    ["Every public evidence claim has a finding ID and locator.", "The story, fact files and appendix generated from the dossier.", "Verified in this publication"],
    ["Test-only observations stay out of the public story.", "The editorial selection and the appendix split.", "Verified by the build and tests"],
    ["Fixture, demo and example targets are refused.", "The publication gate in `studio.py`.", "Implemented"],
    ["The page has a hash receipt.", "The dossier SHA-256 in the page and manifest.", "Verified"],
    ["Customers are paying for the work.", "No customer testimonial or sales record is published yet.", "Not proven"],
    ["Ten finished field notes exist.", "Ten short narrated captioned visual briefs generated from the dossier with three local voices and ten presenter treatments; no recorded teardown footage.", "Verified"],
    ["A recorded teardown video exists.", "No screen-recorded investigation or client footage is published.", "Not proven"],
])}
<h2>What is not proven yet</h2>
<div class="cards-2"><div class="card"><h3>No customer quote</h3><p>There is no named buyer saying the field guide or a teardown was worth the money. That is a gap, not a secret.</p></div>
<div class="card"><h3>No revenue claim</h3><p>The Stripe links are live Payment Links, but this page does not claim a sale, a conversion rate or a monthly income.</p></div>
<div class="card"><h3>No recorded teardown video</h3><p>Ten short generated field notes exist, but there is no screen-recorded investigation or client footage. That is a separate production step.</p></div>
<div class="card"><h3>No external client case study</h3><p>The first file is a real self-teardown of the REA source tree. It is evidence, but it is not a client engagement.</p></div></div>
<h2>How a future quote gets published</h2>
<ol class="steps"><li><b>Deliver the work.</b> A paid guide or a scoped teardown with a real output.</li>
<li><b>Ask for a specific quote.</b> What changed, what was useful and what was not.</li>
<li><b>Get written permission.</b> Name, role, date and the exact wording.</li>
<li><b>Link the proof.</b> Add the relevant evidence file or delivery note.</li>
<li><b>Publish or decline.</b> No editing to sound more enthusiastic; no anonymous five-star cards.</li></ol>
<p class="small">Public submission is not open yet. Quotes are collected manually so the attribution and permission can be checked. If you are a buyer with a real experience, the operator can add it through the private Engine Room.</p>
"""
    return _page("Testimonials and proof", "An honest proof page: no invented customer quotes, a real founder brief, the recorded dossier verdict and a clear testimonial policy.",
                 "../", "proof", body)


def pricing_html() -> str:
    body = f"""
<span class="eyebrow">Support</span><h1>One optional £5 donation. No pack. No subscription.</h1>
<p class="dek">The evidence, fact files, method and field guides are free. There is no paid pack to buy. If the work is useful, you can leave a one-time £5 donation. It buys no deliverable and does not change the publication gate.</p>
{_price_cards("../")}
<div class="note"><b>This is not a product:</b> the donation does not unlock a download, a private report, priority access or a finding. The public evidence stays public.</div>
<h2>What the £5 supports</h2>
<div class="cards-3"><div class="card"><h3>Evidence time</h3><p>More time to run the ladder, check locators and write the limits instead of publishing a thin page.</p></div>
<div class="card"><h3>The free library</h3><p>Hosting and maintenance for the evidence files, fact files, method, crew page and field guides.</p></div>
<div class="card"><h3>The monthly note</h3><p>A short field note when there is a real finding to share. If there is nothing new, the note says so.</p></div></div>
<h2 id="after-donation">After you donate: request the monthly field notes</h2>
<p>Donating and subscribing are separate choices. A donation does not add you to the list. If you want the monthly note, use this form and tick the consent box. You can leave at any time.</p>
<div class="card">{_subscribe_form("pricing-donation", donation_context=True)}</div>
<h2>What the field notes contain</h2>
<div class="cards-2"><div class="card"><h3>One finding</h3><p>A recorded observation from a published fact file, with its confidence and a link to check it.</p></div>
<div class="card"><h3>One seam and one limit</h3><p>Where behaviour can change, and what the evidence still does not establish.</p></div></div>
<h2>Privacy and control</h2>
<p class="small">The form asks for an email address, an optional first name, your update choices and consent. The local build stores that request in <code>rea/data/mailroom.json</code>, outside the public site. It does not store an IP address, open a tracking pixel or sell the list. The owner reviews the list and sends the note from the contact address; this build does not auto-send email.</p>
<h2>Questions</h2>
<div class="faq"><details><summary>Is the £5 a pack?</summary><p>No. There is no paid pack. The £5 link is a one-time donation with no deliverable attached.</p></details>
<details><summary>Is this a subscription?</summary><p>No. The donation is one-time. The field notes are a separate opt-in list and do not require a donation.</p></details>
<details><summary>Will I be added because I donated?</summary><p>No. The Stripe donation and the field-note form are separate. Only the form, with a ticked consent box, adds you to the list.</p></details>
<details><summary>How do I unsubscribe?</summary><p>Reply to any field note or email <a href="mailto:{_e(CONTACT_EMAIL)}">{_e(CONTACT_EMAIL)}</a>. You can also ask the owner to pause or remove your address.</p></details>
<details><summary>Can I get a refund?</summary><p>Donations are voluntary. If a payment went wrong, use the Stripe receipt or contact the room. No refund policy is invented on this page.</p></details>
<details><summary>Do you take on any target?</summary><p>No. Any inspection must be owned or authorised, the scope must be safe and the question must be answerable without exploiting a third party.</p></details></div>
<div class="note"><b>No income claim:</b> the room is new. The proof page states that no customer testimonial or sales record is published yet. There is no claim that a donation will produce a finding or an income.</div>
<p class="small">Questions about a donation or the list: <a href="mailto:{_e(CONTACT_EMAIL)}">{_e(CONTACT_EMAIL)}</a>.</p>
"""
    return _page("Support and field notes", "One optional £5 donation, no pack and no subscription. Opt in separately to the monthly field notes.",
                 "../", "pricing", body)


def _issue_card(issue: dict) -> str:
    sections = "".join(
        f"<section class=\"issue-section\"><h4>{_e(section.get('h'))}</h4>" +
        "".join(f"<p>{_e(paragraph)}</p>" for paragraph in section.get("p") or []) + "</section>"
        for section in issue.get("sections") or []
    )
    status = str(issue.get("status") or "ready")
    label = "Sent" if status == "sent" else "Ready"
    return f"""<article class="card issue-card"><div class="meta"><span class="chip">{_e(issue.get('month'))}</span><span class="chip">{label}</span></div>
<h3>{_e(issue.get('title'))}</h3><p>{_e(issue.get('intro'))}</p>
<details><summary>Read the field note</summary>{sections}</details></article>"""


def newsletter_html(issues: list[dict]) -> str:
    archive = "".join(_issue_card(issue) for issue in issues) or (
        '<div class="empty"><b>No field note is ready yet.</b><p>The first issue is drafted from a real publication and marked ready by the owner. No placeholder issue is published to fill the page.</p></div>'
    )
    body = f"""
<span class="eyebrow">Monthly field notes</span><h1>One useful note a month. No feed, no filler.</h1>
<p class="dek">A short note from the evidence room: one finding, one seam, one limit and a link to check it. The list is opt-in. A donation is not a subscription, and a subscription is not a donation.</p>
{_chips([('issues ready', len(issues)), ('cadence', 'monthly aim'), ('privacy', 'opt-in only')])}
<div class="split"><div class="card"><h2>What arrives</h2><ul class="rule-list"><li>One recorded finding with a fact-file link.</li><li>One seam and the limit that goes with it.</li><li>One useful method or checklist.</li><li>Nothing when there is no real evidence to share.</li></ul></div>
<div class="card"><h2>What never happens</h2><ul class="rule-list"><li>No bought list and no scraped address.</li><li>No adding donors without a separate consent tick.</li><li>No tracking pixel and no sold data.</li><li>No claim that a note will make you money.</li></ul></div></div>
<div class="card"><h2>Request the field notes</h2>{_subscribe_form("newsletter")}</div>
<h2>Archive</h2>
<p class="small">Only issues marked ready or sent are listed. Drafts stay in the owner's Mailroom.</p>
{archive}
<h2>Privacy and control</h2>
<p class="small">The form stores your email, optional first name, update choices, consent time and source in a local JSON file outside the public site. It does not collect an IP address or use an analytics script. To leave, reply to a note or email <a href="mailto:{_e(CONTACT_EMAIL)}">{_e(CONTACT_EMAIL)}</a>.</p>
<h2>Questions</h2>
<div class="faq"><details><summary>How often will it arrive?</summary><p>The aim is one note a month. If there is no new evidence, the room does not invent an issue. The archive shows what has actually been marked ready.</p></details>
<details><summary>Do I need to donate?</summary><p>No. The field notes are free and separate from the optional £5 donation.</p></details>
<details><summary>Can I choose only new evidence files?</summary><p>Yes. The form lets you choose monthly field notes, new evidence files, or both.</p></details>
<details><summary>Is the email sent automatically?</summary><p>No. This local build stores the list and drafts the issue; the owner reviews and sends it from the contact address. That is stated here instead of pretending an email service exists.</p></details></div>
"""
    return _page("Monthly field notes", "Opt in to a monthly evidence note from The Teardown Room: one finding, one seam, one limit and a link to check it.",
                 "../", "newsletter", body)


def fact_html(fact: dict, pubs: list[dict]) -> str:
    chips = _chips([
        ("confidence", f"{fact.get('confidence')}%"),
        ("dossier", fact.get("dossier_id")),
        ("group", fact.get("group")),
    ])
    sections = _prose(fact.get("body") or [])
    table = _table(fact["table"]["headers"], fact["table"]["rows"]) if fact.get("table") else ""
    link_html = "".join(_link(label, href) for label, href in fact.get("links") or [])
    note = f'<div class="note">{_e(fact.get("note"))}</div>' if fact.get("note") else ""
    body = f"""
<div class="breadcrumb"><a href="../facts/">Fact files</a> / <a href="../evidence/">Evidence library</a></div>
<span class="eyebrow">{_e(fact.get('eyebrow'))}</span><h1>{_e(fact.get('title'))}</h1>
<p class="dek">{_e(fact.get('summary'))}</p>
{chips}
{table}
{sections}
{note}
<div class="card"><h3>Keep checking</h3>{link_html}</div>
"""
    return _page(str(fact.get("title") or "Fact file"), str(fact.get("summary") or "A fact file from the evidence room."),
                 "../", "facts", body)


def guide_html(guide: dict, previous: dict | None = None, following: dict | None = None) -> str:
    toc = "".join(
        f'<li><a href="#s{index}">{_e(section.get("h"))}</a></li>'
        for index, section in enumerate(guide.get("sections") or [], start=1)
    )
    sections = _prose(guide.get("sections") or [])
    body = f"""
<div class="breadcrumb"><a href="../guides/">Field guides</a> / <a href="../method/">Method</a></div>
<span class="eyebrow">{_e(guide.get('level'))} · {_e(guide.get('time'))}</span><h1>{_e(guide.get('title'))}</h1>
<p class="dek">{_e(guide.get('dek'))}</p>
<div class="meta"><span class="chip">field guide</span><span class="chip">operator level</span><span class="chip">checklist at the end</span></div>
<div class="toc"><b>In this guide</b><ol>{toc}</ol></div>
{sections}
<div class="card"><h3>Use it on the next file</h3>
<p>Open the <a href="../method/">method</a>, run the gate, then publish from the <a href="../evidence/">evidence library</a>. The guide is a route, not a substitute for authorisation.</p>
{_link('Read the method', '../method/')}{_link('Open the fact files', '../facts/')}</div>
"""
    nav = ""
    if previous or following:
        nav = '<div class="guide-nav">' + (
            f'<a href="{previous["slug"]}.html"><span>Previous guide</span><b>← {_e(previous["title"])}</b></a>' if previous else '<span></span>'
        ) + (
            f'<a class="next" href="{following["slug"]}.html"><span>Next guide</span><b>{_e(following["title"])} →</b></a>' if following else '<span></span>'
        ) + '</div>'
        body += nav
    return _page(str(guide.get("title") or "Field guide"), str(guide.get("dek") or "A practical REA field guide."),
                 "../", "guides", body)


# ------------------------------------------------------------------- writer --

SITE_JS = r"""(() => {
  const forms = document.querySelectorAll("form.subscribe-form");
  forms.forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const result = form.querySelector(".form-result");
      const button = form.querySelector('button[type="submit"]');
      const email = (form.querySelector('[name="email"]') || {}).value || "";
      const name = (form.querySelector('[name="name"]') || {}).value || "";
      const topics = Array.from(form.querySelectorAll('[name="topics"]:checked')).map((item) => item.value);
      const consent = (form.querySelector('[name="consent"]') || {}).checked === true;
      const donated = (form.querySelector('[name="donated"]') || {}).checked === true;
      const owner = form.dataset.ownerEmail || "";
      const subject = encodeURIComponent("Field notes request");
      const body = encodeURIComponent("Please add me to the monthly field notes.\n\nEmail: " + email + "\nName: " + name + "\n");
      const fallback = "mailto:" + owner + "?subject=" + subject + "&body=" + body;
      const showFallback = (message) => {
        result.className = "form-result err";
        result.textContent = message + " Email " + owner + " directly: ";
        const link = document.createElement("a");
        link.href = fallback;
        link.textContent = owner;
        result.appendChild(link);
        result.appendChild(document.createTextNode("."));
      };
      if (!email.trim()) { result.className = "form-result err"; result.textContent = "Enter an email address."; return; }
      if (!topics.length) { result.className = "form-result err"; result.textContent = "Choose at least one update type."; return; }
      if (!consent) { result.className = "form-result err"; result.textContent = "Tick the consent box before requesting the notes."; return; }
      if (button) { button.disabled = true; button.textContent = "Saving…"; }
      try {
        const response = await fetch("/api/newsletter/subscribe", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            email: email.trim(), name: name.trim(), topics: topics,
            consent: true, donated: donated, source: form.dataset.source || "field-notes"
          })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || "the request could not be saved");
        form.reset();
        result.className = "form-result ok";
        result.textContent = data.message || "Request saved. The owner can now see it in the Mailroom.";
        if (data.mailto) {
          const link = document.createElement("a");
          link.href = data.mailto;
          link.textContent = " Send a copy by email";
          result.appendChild(link);
        }
      } catch (error) {
        showFallback("The local form could not save this request.");
      } finally {
        if (button) { button.disabled = false; button.textContent = "Request the field notes"; }
      }
    });
  });

  // -- evidence reader ------------------------------------------------------
  document.querySelectorAll("[data-evidence-reader]").forEach((reader) => {
    const tabs = Array.from(reader.querySelectorAll("[data-evidence-tab]"));
    const panels = Array.from(reader.querySelectorAll("[data-evidence-panel]"));
    const select = (name) => {
      tabs.forEach((tab) => {
        const on = tab.dataset.evidenceTab === name;
        tab.setAttribute("aria-selected", on ? "true" : "false");
        tab.tabIndex = on ? 0 : -1;
      });
      panels.forEach((panel) => { panel.hidden = panel.dataset.evidencePanel !== name; });
    };
    tabs.forEach((tab, index) => {
      tab.addEventListener("click", () => select(tab.dataset.evidenceTab));
      tab.addEventListener("keydown", (event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        const next = event.key === "ArrowRight" ? (index + 1) % tabs.length : (index - 1 + tabs.length) % tabs.length;
        tabs[next].focus();
        select(tabs[next].dataset.evidenceTab);
      });
    });
  });

  // -- local search ---------------------------------------------------------
  const searchPanel = document.getElementById("search-panel");
  if (searchPanel) {
    const searchInput = document.getElementById("search-input");
    const searchResults = document.getElementById("search-results");
    const root = document.documentElement.dataset.root || "";
    let searchIndex = null;
    let searchReturnFocus = null;
    const openSearch = () => {
      searchReturnFocus = document.activeElement;
      searchPanel.hidden = false;
      document.body.style.overflow = "hidden";
      searchInput.value = "";
      renderSearch("");
      searchInput.focus();
    };
    const closeSearch = () => {
      searchPanel.hidden = true;
      document.body.style.overflow = "";
      if (searchReturnFocus) searchReturnFocus.focus();
    };
    const loadSearch = async () => {
      if (searchIndex) return searchIndex;
      const response = await fetch(root + "assets/search-index.json");
      if (!response.ok) throw new Error("search index unavailable");
      searchIndex = await response.json();
      return searchIndex;
    };
    const renderSearch = async (query) => {
      searchResults.textContent = "";
      const clean = query.trim().toLowerCase();
      if (!clean) {
        const note = document.createElement("p");
        note.className = "search-empty";
        note.textContent = "Type a term, or press Escape to close.";
        searchResults.appendChild(note);
        return;
      }
      let items = [];
      try { items = await loadSearch(); } catch (error) {
        const note = document.createElement("p");
        note.className = "search-empty";
        note.textContent = "The search index could not load. Open Start here instead.";
        searchResults.appendChild(note);
        return;
      }
      const terms = clean.split(/\s+/).filter(Boolean);
      const hits = items.filter((item) => {
        const haystack = (item.title + " " + item.description + " " + item.type).toLowerCase();
        return terms.every((term) => haystack.includes(term));
      }).slice(0, 14);
      if (!hits.length) {
        const note = document.createElement("p");
        note.className = "search-empty";
        note.textContent = "No match. Try locator, seam, redaction, digest or glossary.";
        searchResults.appendChild(note);
        return;
      }
      hits.forEach((item) => {
        const link = document.createElement("a");
        link.className = "search-result";
        link.href = root + item.url;
        const title = document.createElement("b");
        title.textContent = item.title;
        const type = document.createElement("span");
        type.textContent = item.type;
        const description = document.createElement("p");
        description.textContent = item.description;
        link.append(type, title, description);
        searchResults.appendChild(link);
      });
    };
    document.querySelectorAll("[data-search-open]").forEach((button) => button.addEventListener("click", openSearch));
    document.querySelectorAll("[data-search-close]").forEach((button) => button.addEventListener("click", closeSearch));
    searchPanel.addEventListener("click", (event) => { if (event.target === searchPanel) closeSearch(); });
    searchInput.addEventListener("input", () => renderSearch(searchInput.value));
    document.addEventListener("keydown", (event) => {
      const tag = (document.activeElement && document.activeElement.tagName) || "";
      const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || document.activeElement.isContentEditable;
      if (event.key === "/" && !typing && searchPanel.hidden) { event.preventDefault(); openSearch(); }
      if (event.key === "Escape" && !searchPanel.hidden) closeSearch();
    });
  }
})();
"""


def write_site(root: Path, pubs: list[dict], facts: list[dict], lanes: list[dict],
               issues: list[dict] | None = None) -> None:
    issues = issues or []
    _write(root / "index.html", home_html(pubs, facts, lanes))
    _write(root / "evidence" / "index.html", evidence_html(pubs, facts))
    _write(root / "facts" / "index.html", facts_html(facts))
    _write(root / "guides" / "index.html", guides_html())
    _write(root / "method" / "index.html", method_html())
    _write(root / "download" / "index.html", download_html())
    _write(root / "crew" / "index.html", crew_html(lanes))
    _write(root / "proof" / "index.html", proof_html(pubs, facts))
    _write(root / "newsletter" / "index.html", newsletter_html(issues))
    _write(root / "pricing" / "index.html", pricing_html())
    _write(root / "start" / "index.html", start_html(pubs, facts))
    _write(root / "watch" / "index.html", watch_html(pubs))
    for note in field_notes.FIELD_NOTES:
        _write(root / "watch" / note["number"] / "index.html", watch_note_html(note, pubs))
    _write(root / "glossary" / "index.html", glossary_html())
    _write(root / "assets" / "site.js", SITE_JS)
    _write(root / "assets" / "search-index.json", json.dumps(_search_index(pubs, facts), ensure_ascii=False, indent=1))
    for fact in facts:
        _write(root / "facts" / f"{fact['slug']}.html", fact_html(fact, pubs))
    for index, guide in enumerate(GUIDES):
        _write(root / "guides" / f"{guide['slug']}.html",
               guide_html(guide, GUIDES[index - 1] if index else None,
                          GUIDES[index + 1] if index + 1 < len(GUIDES) else None))
