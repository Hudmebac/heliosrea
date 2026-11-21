# Video shot script — Inside the REA engine room: evidence, gate and seam

- **Dossier:** `REA-20261121-60b03488-28212`
- **Evidence hash:** `dc00e1f9962716233ee0d901e1ca7cfc42d3156210993164c8220f38d0dfc934`
- **Target:** rea
- **Evidence records:** 10 · **Change surfaces:** 7

> Record the proof before writing the edit. This is a shot plan built from the dossier, not a claim that a finished video exists. Use only footage of equipment you own or have written permission to film.

## Editorial spine

**Cold open:** 7 change surface(s) mapped across 3 access mode(s): Source, Configuration, Protocol.

**Scope:** State the target, the authorisation, the date and the limit. The viewer must know what was not tested.

## Shot list

| Time | Beat | Script / narration | Shot |
|---|---|---|---|
| 0:00–0:18 | Cold open | Show the evidence card. Say: “This is rea. We recorded 10 findings and 7 change surfaces. Here is the seam, and here is what we could not prove.” | Screen recording of the dossier and the hash card; no stock footage. |
| 0:18–0:45 | Scope | Name the owner/permission, the scope note, the tools and the stop condition. | Close-up of the target label and the scope card. |
| 0:45–1:27 | Evidence 1 · F-001 | **12 files · 688.5 KB.** Languages by extension: .py, .md, .png, .html. Top folders: data (1), viewer (1). Binaries/containers: 0. Locator: `recorded locator`; confidence 95%. | Highlight the exact evidence row, then cut to the artifact or screen. |
| 1:35–2:17 | Evidence 2 · F-002 | **Entry-point candidates.** server.py, index.html Locator: `recorded locator`; confidence 80%. | Highlight the exact evidence row, then cut to the artifact or screen. |
| 2:25–3:07 | Evidence 3 · F-005 | **1 environment switches.** PORT Locator: `recorded locator`; confidence 80%. | Highlight the exact evidence row, then cut to the artifact or screen. |
| 3:15–3:55 | Seam 1 · E-01 | **rea.** Edit the source tree directly; take a baseline test first, then patch the smallest module. Risk: low; confidence 90%. | Annotate the seam; show the before/after only if it was actually tested. |
| 4:03–4:43 | Seam 2 · E-03 | **PORT.** Flip a supported switch before editing code; it is the cheapest reversible change. Risk: low; confidence 80%. | Annotate the seam; show the before/after only if it was actually tested. |
| 4:51–5:36 | Proof and limit | Show the dossier ID `REA-20261121-60b03488-28212`, hash prefix `dc00e1f996271623…`, the probe ledger and the empty/error paths. Say what remains unknown. | Full-screen evidence table and a terminal or file-manager shot of the real artifact. |
| 5:36–END | Close | Offer the field guide and one fixed-scope paid teardown. No fake urgency, no promised outcome. | End card with the public links and the next evidence question. |

## B-roll

- Screen recording of the dossier opening on the finding ID.
- Macro shot of the owned device or the local artifact, with serials covered.
- The hash card generated for this publication.
- The probe ladder, including one empty or error row.
- A diagram of the seam, drawn from the edit-point locator.

## Thumbnail copy

- `REA: SHOW THE SEAM`
- `10 FINDINGS · 1 SEAM`
- `EVIDENCE, NOT HYPE`

## Title options

- rea: the evidence, the seam and the limits
- What 10 evidence records revealed about rea
- The seam in rea — a documented teardown

## Description

Inside the REA engine room: evidence, gate and seam

Built from REA dossier REA-20261121-60b03488-28212 (dc00e1f996271623…). 7 change surface(s) mapped across 3 access mode(s): Source, Configuration, Protocol.

Chapters
00:00 Cold open
00:18 Scope and evidence
00:45 Evidence
04:00 The seam
06:00 Proof and limit
07:00 Close

Every claim in this video is tied to a finding ID, a locator and a confidence in the evidence appendix. Probes that returned nothing are listed as limits, not hidden.

Optional £5 donation: https://buy.stripe.com/bJe9AS53ybBVfZv6R14wM03
Monthly field notes: opt in separately; the donation does not add you to the list.

Scope: owned or written-permission equipment only. No exploits, stolen data or control bypass.

## Empty / error probes to mention

- **Read manifests and config** — No recognisable manifest or config file.
- **Map request surface** — ValueError: Invalid IPv6 URL
- **Read repository history** — Not a git working tree.
- **Parse the artifact** — No compiled artifact at this target.
- **Map security controls** — No compiled artifact to map controls from.
- **Extract strings** — No compiled artifact to pull strings from.
- **Map firmware containers** — No firmware/container image at this target.

## Next evidence questions

- No manifest: read the binary import table and strings to infer the runtime. _(because Read manifests and config returned empty)_
- No request sites in source: capture runtime traffic (HAR or local proxy) and diff the calls. _(because Map request surface returned error)_
- No history: identify the upstream from strings or a remote config, then check GitHub. _(because Read repository history returned empty)_

## Production rule

Do not add a claim, a device, a screenshot or a result that is not in the dossier. If the edit needs a new claim, run another investigation first.
