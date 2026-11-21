# REA — Engine Room

A local evidence production engine. Give it a GitHub repository, a website, a
local folder/binary, a captured HAR, this WiFi/LAN, or a public host. It runs
a ladder of probes, records evidence, connects what it learned, and reports
**where the behaviour can be changed**. The Engine Room then packages a real
dossier into a static evidence file, a video shot script, an evidence appendix,
a thumbnail card and an optional donation path.

It sits beside Helios and uses the same rules: local-first, stdlib only, no
account, no API key, and spend is irrelevant to it.

```text
REA.bat                 # Windows
python rea/server.py    # any OS
```

Then open <http://127.0.0.1:4711/>.

## Main areas

The project has three working areas. Use these names in discussion:

- **Engine Room** (`/`) — the private control surface. It runs investigations and can rebuild
  the Website or draft a Mailroom issue. It never deploys or sends on its own.
- **Website** (`/published/`) — the public reading surface, generated from checked evidence and
  rebuilt as new dossiers pass the gate. The public address is still to be supplied.
- **Mailroom** (`/mailroom/`) — the local-only owner console for sign-ups, consent, CSV export
  and monthly issues built from published content.

The flow is **Engine Room → Website → Mailroom**. The Engine Room influences both surfaces; the
Website is the published entity; the Mailroom serves the people who opted in.

## How an investigation works

1. **Classify** the target: local folder, local artifact, GitHub repo, website, HAR capture,
   this WiFi/LAN, or a public host.
2. **Probe**: a fixed, ordered battery. Each probe writes findings with a
   confidence, a locator and (where it applies) an edit point.
3. **Escalate**: a probe that finds nothing queues the next technique
   (`empty_next`) up to two levels deep. Nothing is recorded as a failure; the
   dossier records the retry and the next move.
4. **Connect**: findings become a graph of target → assets → technologies →
   endpoints → sources → edit points.
5. **Report**: access modes (source, config, binary, protocol, edge content,
   firmware, fork), edit points with risk, sources learned from, and next moves.

Dossiers are written to `rea/data/investigations/` as JSON. The console can
download any dossier as Markdown.

## Probes

| Family | What it does |
|---|---|
| Surface | file inventory, manifests, env contracts |
| Code | modules, routes, imports, switches, TODO/HACK sites |
| Protocol | hosts, client calls, websockets, auth shape |
| History | git remote, recent commits, churn |
| Binary | PE/ELF/Mach-O/APK/SquashFS headers, strings, sections, overlay |
| Firmware | container magic, partition offsets, rootfs hints |
| Traffic | HAR capture: methods, hosts, endpoints, auth names, status classes |
| Network | WiFi/SSID, interface addresses, gateway, DNS, LAN devices from one bounded ICMP sweep |
| Internet | Egress IP/ISP/ASN, DNS, TLS certificate, HTTP/HTTPS edge headers, port-80 redirect |
| Web | status/headers, CSP/CORS/cookies, stack fingerprint, assets, endpoints, docs |
| Sources | GitHub metadata/licence/tree/raw files, npm and PyPI provenance |

### Captured traffic

When a site blocks passive fetches, export a HAR from the browser's Network
panel (enable *Preserve log*, then *Export HAR*) and investigate the file.
REA reads methods, hosts, endpoints, status classes and authentication
**names**. It never stores header, query, body or token values.

### WiFi, LAN devices and internet exposure

Choose **WiFi & LAN devices** and leave the target as `wifi` (or supply your
private subnet, for example `192.168.1.0/24`). REA reads the adapter's SSID,
addresses, gateway and neighbour cache. The optional LAN sweep is **off by
default**; tick the sweep box to send one ICMP ping to each address in that
private `/24` (at most 254) and populate the ARP table. It never scans ports,
and public ranges are refused. Use this only on a network you own or are
authorised to inspect.

Choose **Internet exposure** and enter `internet` for your egress IP, ISP and
ASN (one lookup to `ipwho.is`), or enter a domain such as `example.com` to read
DNS, the TLS certificate and the HTTP/HTTPS edge (HSTS, CSP, server disclosure,
port-80 redirect). No port scan is performed.

The dossier gets a **Network** section, a device list, network edit points and
the devices in the graph. `rea/discovery.py` holds the parsers, the private
range guard and the bounded sweep.

## Guardrails

- **Authorised targets only.** The API refuses a run unless the operator
  attests ownership or permission; the attestation is stored in the dossier.
- **Aggressive inside authorisation.** One request per checked path, same-host,
  request budget, no auth attempts. WiFi & LAN adds one bounded ICMP sweep of a
  private `/24`; internet checks are DNS, one TLS handshake and HTTP on 80/443.
  No port scanning, brute force or exploitation. REA should exhaust static
  analysis, reconstruction, compatibility testing and authorised repacking; it
  does not attack systems you do not own.
- **Secrets never stored.** Credential-shaped values are masked at capture
  time; environment files contribute key names only.
- **Dead ends are routing information** — every empty probe contributes a next
  move, not a verdict.

## Engine Room and Studio

The area bar above the tabs names the three working areas: **Engine Room**, **Website** and
**Mailroom**. Inside the Engine Room, the sections open with **Start here**, then **Overview**,
**Investigate**, **Improve**, **Studio**, **Revenue**, **Crew** and **Method**. Start here gives the curious,
the beginner and the expert a separate route into the same public evidence. Method holds the
planning and reference surfaces so they do not compete with production.

- **Overview** — the private production floor. It reads the real dossier store, shows the
  five-gate line (Evidence, Verify, Package, Publish, Sell), the dossiers that pass the publication
  gate, the production crew lanes and the exact publication rules. Fixture and demo dossiers are
  counted but never offered for publication.
- **Investigate** — the original probe ladder and dossier view. Nothing about the evidence engine
  is mocked here; a run still requires the operator's authorisation attestation.
- **Improve** — brief a product and an outcome, then let the engine suggest evidence-backed
  improvements from the mapped dossier seams. Suggestions are deterministic: the goal ranks the
  allowed change levels, the seam supplies the locator and evidence, and the system class supplies
  the stop conditions. Approve a suggestion to open a prefilled plan, or decline it; the decision
  is recorded. The plan then moves through draft, approved, baseline recorded, change recorded,
  verified or rolled back. The plan is stored in `rea/data/improvements.json`; the console never
  applies the change to the target. A downloadable change brief carries the class limits, seam
  evidence, guardrails and execution history. Start with **Where is it?**: this computer, the local
  network, a public host, a website, a GitHub repository or a saved `.har` capture. Enter the target,
  confirm authorisation and press **Investigate & suggest**; the intake runs the same authorised
  probes as Investigate and then proposes changes from the new dossier. Reusing a completed dossier
  is the alternative for evidence collected earlier.

  A baselined plan can be executed locally: record a unified diff (`git diff`), preview it with
  `git apply --check`, then press **Apply & verify**. The target must be a clean local git work tree
  and the class must be software or web. The recorded verification command runs after the patch; a
  non-zero exit reverses the patch automatically, and an applied plan can be rolled back explicitly.
  Every other class stays a human-applied change.

  Press **Let the crew decide** and the crew takes the highest-ranked suggestion, drafts the plan and
  records its advice. Where the seam is a simple Python entry point it also drafts the exact
  `__main__` guard patch and a read-only `py_compile` check; otherwise it says plainly that the diff
  needs a human. The decision is a draft, never an action, and the patch remains reversible.

  System classes cover **software**, **mobile**, **web**, **TV / streaming**, **vehicle**,
  **appliance**, **alarm / access control**, **smart home / IoT**, **network** and
  **embedded / firmware**. Change levels are **Observe**, **Configure**, **Debloat**,
  **Replace / add interface**, **Extend** and **Replace runtime**. The class decides which levels are
  allowed: a vehicle plan can work on infotainment but cannot be a debloat or runtime replacement,
  and an alarm plan cannot disable monitoring or life-safety functions.
- **Studio** — choose a completed, authorised dossier and build the publication. The generated
  bundle contains a story page, per-finding fact files, an evidence appendix, a video shot script,
  a manifest with the dossier SHA-256 and an SVG evidence card. It also generates the public
  evidence library, start page, field guides, glossary, method, crew, proof, watch page and support
  page. The video package includes a paper edit built from real finding IDs and locators plus ten
  separately rendered narrated captioned field notes. The notes are summaries of the dossier, not
  recorded teardowns or client footage.
- **Revenue** — scan public demand, then package a productised offer. Six 2026 routes cover
  automation, audit evidence, legacy rescue, firmware teardown, agentic-workflow review and technical
  diligence. Each offer carries a price hypothesis, deliverables, proof asset, first-ten-customer
  plan, outreach rules and risks. Demand comes from public Hacker News search; no private data, no
  automatic messages and no income guarantee.
- **Crew** — seventeen profession lenses, each with questions, tools, deliverables, its REA mapping
  and a copy-ready role prompt. The production lanes add evidence editor, video producer, web
  publisher and distribution lead to the original thirteen roles.
- **Method** — **Autonomy**, **Range**, **Playbook**, **Prompts** and **Ideas**. Autonomy builds a
  read-only route before anything runs; Range states the authorisation and stop conditions; Playbook
  holds the run order and probe ladders; Prompts are copy-ready briefs; Ideas generate deterministic
  investigation briefs. These are reference surfaces, not a content calendar.

### Publication gate

`rea/studio.py` refuses to publish unless all of these are true:

- The operator attested ownership or written permission.
- The investigation status is complete.
- The target is real, not a fixture, demo, IANA example domain or private network/egress capture.
- The dossier has at least three evidence records and one mapped change surface.

A successful build writes to `site/` and is previewable at `/published/`. The generated site keeps
finding IDs, locators, confidence, redacted details and the dossier hash. Empty and error probes stay
in the page as limits. The public set includes the home page, start page, evidence library,
per-finding fact files, nine field guides, a searchable glossary, method, crew, proof policy, a
field-note series index with ten note pages, monthly field notes and support. The only checkout is the optional public £5 donation link;
there is no paid pack. No secret key is stored.

### Improve gate

`rea/improvements.py` first suggests from the completed, authorised, non-fixture dossier. The brief
is matched against the allowed change levels, each seam contributes at most two options, and blocked
seams are excluded. The suggestions are stored with the plan store; approving one creates a draft
plan and records the link, while declining records the decision without creating a plan. This is a
deterministic suggestion engine, not a model call and not a promise that the change is safe.

A plan is accepted only from a dossier that is complete, authorised, non-fixture
and has the named seam. It also requires a known system class and a change level allowed for that
class. Safety-critical classes carry their own stop conditions into the plan and the brief; they do
not turn an owner permission into permission to touch safety systems. The status path is enforced:
draft → approved → baseline recorded → change recorded → verified, with rollback available after
approval. Each transition is written to the plan history with a timestamp. A plan that carries a
unified diff can be applied locally once it is baselined: `rea/executor.py` checks the patch, applies
it, runs the recorded verification command and reverses it automatically when that command fails.
Only software and web plans on a clean local git work tree are executed; every other class is still
applied by a human. That keeps the same rule as the rest of the room: evidence first, one smallest
reversible change, then a result that can be checked or undone.

## Learn, search and watch

The public room has three learner routes on `/published/start/`: a two-minute curious route, a
thirty-minute beginner route and an expert route that goes straight to the dossier, locators and
appendix. The same page includes a keyboard-accessible fact reader, a searchable glossary and a
search overlay available from every page (`/` opens it).

The field-note series at `/published/watch/` carries ten short narrated captioned briefs. Each has
its own page at `/published/watch/01/` through `/published/watch/10/`, with a poster, WebVTT
captions, a transcript, downloads and the next/previous navigation. The series uses three installed
local Windows OneCore voices (George, Hazel and Susan) in ten presenter treatments: pitch, pace,
tone and the ambient bed change, but they are not ten different people. No external TTS, third-party audio or recorded presenter is
used. Generate or refresh the series with:

```text
python rea/video.py
```

The output is `site/assets/field-notes/`, with one numbered folder per note. No upload is
performed. Each folder has a `youtube.md` review draft, not a published video. The series uses
generated diagrams and local synthetic voices; it does not borrow footage, audio or branding from
the external REA project or the YouTube explainers.

Briefs are generated locally with templates; no model call is made and no market data is invented.

## Mailroom and monthly field notes

The public field-note form is opt-in. It writes to `rea/data/mailroom.json`, which is outside the
generated site and ignored by git. The store holds contacts, preferences, consent times, status and
monthly issues. The owner console is at `/mailroom/` and is restricted to loopback; it is not linked
from the public site.

The owner can search contacts, mark them active/paused/unsubscribed, export CSV, add a contact only
when that person asked, draft an issue from the latest published fact files, mark it ready and copy
the email text. The local build does **not** send email automatically. The owner reviews and sends
from the configured address; the first issue is drafted from a real publication, not invented copy.

Set `REA_CONTACT_EMAIL` to override the address without editing code. The default is
`mysurnameandfirsname@gmail.com`.

```text
python rea/mailroom.py list
python rea/mailroom.py export --out mailroom-contacts.csv
python rea/mailroom.py draft --month 2026-11
```

## API

```text
GET  /api/health
GET  /api/probes
GET  /api/playbook
POST /api/plan             {target, kind, stage, depth, network, sweep}   read-only
GET  /api/revenue/models
POST /api/revenue/scan     {capability, buyer, limit, days}             live public search
POST /api/revenue/offer    {capability, buyer, model, price, proof}
GET  /api/media
GET  /api/newsletter                         public issue archive (no contacts)
POST /api/newsletter/subscribe {email, name?, topics, consent, donated?, source?}
GET  /mailroom/                              owner console, loopback only
GET  /api/mailroom                           contacts, issues and stats, loopback only
GET  /api/mailroom/export.csv                contact CSV, loopback only
POST /api/mailroom/contact                   manual contact, loopback only
POST /api/mailroom/status                    active/paused/unsubscribed, loopback only
POST /api/mailroom/issue                     draft monthly note from latest publication
POST /api/mailroom/issue/status              draft/ready/sent
GET  /api/studio
GET  /api/studio/publications
POST /api/studio/publish   {investigation, title?}          strict evidence gate
GET  /api/improvements                        local change plans and suggestions, loopback only
GET  /api/improvements/<id>/brief.md          downloadable change brief
POST /api/improvements/suggest {dossier, goal, system_class, limit?}
POST /api/improvements/suggestions/approve {id, owner, goal?, change?, baseline?, verification?, rollback?, patch?}
POST /api/improvements/suggestions/decline {id}
POST /api/improvements    {dossier, seam, goal, change, baseline, verification, rollback, system_class, intervention, patch?, owner}
POST /api/improvements/status {id, status, note?}           recorded transition only
POST /api/improvements/decide {dossier, goal, system_class, owner?}  crew recommendation
POST /api/improvements/execute {id, dry_run} or {id, confirm:true}   local git work tree only
POST /api/improvements/rollback {id}                        reverse the applied patch
POST /api/ideas/generate   {idea_id, target, goal, angle, variant}
GET  /api/investigations
GET  /api/investigations/<id>
GET  /api/investigations/<id>/report.md
POST /api/investigations   {target, authorized, scope, kind, network, sweep, stage, depth}
GET  /published/            generated static evidence site
```

CLI, for scripts:

```text
python rea/engine.py <target> --offline --json --depth deep
python rea/mailroom.py list
python rea/mailroom.py export --out mailroom-contacts.csv
python rea/mailroom.py draft --month 2026-11
```

## Adding a probe

Register it in `rea/engine.py` with `_register(...)` and add it to `ORDER`.
A probe returns `{"status": "signal" | "empty" | "error", "summary": str}` and
records evidence through `ctx.add(...)` / `ctx.edit(...)`. Give it
`empty_next` so a dead end routes somewhere useful.
