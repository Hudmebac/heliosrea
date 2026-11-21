# Evidence appendix — Inside the REA engine room: evidence, gate and seam

- **Dossier:** `REA-20261121-60b03488-28212`
- **Target:** rea
- **Kind:** Local source tree
- **Finished:** 2026-11-21T00:15:28
- **Scope:** Owned local source tree of the REA engine room; fixture data archived
- **Authorisation:** operator attested ownership / written permission
- **Dossier SHA-256:** `dc00e1f9962716233ee0d901e1ca7cfc42d3156210993164c8220f38d0dfc934`
- **Evidence records:** 10 · **Change surfaces:** 7

## Findings

| ID | Family | Finding | Locator | Confidence |
|---|---|---|---|---|
| F-001 | Surface | 12 files · 688.5 KB | `—` | 95% |
|  |  | Languages by extension: .py, .md, .png, .html. Top folders: data (1), viewer (1). Binaries/containers: 0. |  |  |
| F-002 | Surface | Entry-point candidates | `—` | 80% |
|  |  | server.py, index.html |  |  |
| F-003 | Code | 10 source files · 99 imports · 3 routes | `—` | 85% |
|  |  | GET /api/health, POST /api/items, GET /api/widget |  |  |
| F-004 | Code | Module map | `—` | 80% |
|  |  | viewer (1) |  |  |
| F-005 | Code | 1 environment switches | `—` | 80% |
|  |  | PORT |  |  |
| F-006 | Code | 2 marked change sites | `—` | 70% |
|  |  | engine.py:506 TODO: |FIXME|HACK|XXX)\b[:\s]*(.{0,90})"); test_engine.py:75 TODO: replace the stub handler\n", |  |  |
| F-007 | Protocol | 52 request surface(s) | `—` | 85% |
|  |  | Absolute hosts: http://127.0.0.1:{httpd.server_address[1, http://127.0.0.1:{server.server_address[1, http://www.w3.org/2000/svg, http://{args.host}:{server.server_address[1, http://{netloc}/, https://api.fixture.test, https://api.fixture.test/v1/items, https://api.fixture.test/v1/items?token=***. Client calls: /api/ideas/generate, /api/investigations, /api/investigations/, /api/items, /api/plan, /api/playbook, /api/probes, /api/revenue/models. GraphQL operations detected. |  |  |
| F-008 | Docs | README.md · 171 lines | `README.md` | 80% |
|  |  | Headings: REA — Engine Room, How an investigation works, Probes, Captured traffic, WiFi, LAN devices and internet exposure, Guardrails, Engine Room and Studio, Publication gate. Commands: python rea/server.py    # any OS; python rea/engine.py <target> --offline --json --depth deep. Claims: is mocked here; a run still requires the operator's authorisation attestation. |  |  |
| F-009 | Docs | README.md · 171 lines | `README.md` | 80% |
|  |  | Headings: REA — Engine Room, How an investigation works, Probes, Captured traffic, WiFi, LAN devices and internet exposure, Guardrails, Engine Room and Studio, Publication gate. Commands: python rea/server.py    # any OS; python rea/engine.py <target> --offline --json --depth deep. Claims: is mocked here; a run still requires the operator's authorisation attestation. |  |  |
| F-010 | Verification | 1 test file(s) · 0 CI pipeline(s) | `—` | 85% |
|  |  | Tests: test_engine.py. CI: none |  |  |

## Change surfaces

| ID | Category | Locator | Action | Risk | Confidence |
|---|---|---|---|---|---|
| E-01 | source | `rea` | Edit the source tree directly; take a baseline test first, then patch the smallest module. | low | 90% |
| E-02 | source | `test_engine.py` | Route handlers are the behaviour seam: change the handler, not the framework. | low | 85% |
| E-03 | config | `PORT` | Flip a supported switch before editing code; it is the cheapest reversible change. | low | 80% |
| E-04 | source | `engine.py:506` | Inspect the marked change site (TODO: /FIXME/HACK/XXX)\b[:\s]*(.{0,90})")). | low | 65% |
| E-05 | source | `test_engine.py:75` | Inspect the marked change site (TODO: replace the stub handler\n",). | low | 65% |
| E-06 | config | `README.md → setup commands` | The documented build/run path is the cheapest supported way to reproduce behaviour. | low | 70% |
| E-07 | verification | `test_engine.py` | Run the existing harness before and after a change; add a black-box check when none exists. | low | 80% |

## Probe ledger

| Probe | Family | Status | Result |
|---|---|---|---|
| Map the surface | Surface | signal | 12 files, 4 language signals, 0 binaries. |
| Read manifests and config | Surface | empty | No recognisable manifest or config file. |
| Check package provenance | Sources | skipped | network disabled for this run |
| Learn from GitHub | Sources | skipped | network disabled for this run |
| Map code structure | Code | signal | 10 files, 3 route(s), 1 switch(es). |
| Map request surface | Protocol | error | ValueError: Invalid IPv6 URL |
| Read repository history | History | empty | Not a git working tree. |
| Mine documentation | Docs | signal | 2 document(s) mined. |
| Find the test harness | Verification | signal | 1 tests, 0 pipelines. |
| Parse the artifact | Binary | empty | No compiled artifact at this target. |
| Map security controls | Controls | empty | No compiled artifact to map controls from. |
| Extract strings | Binary | empty | No compiled artifact to pull strings from. |
| Map firmware containers | Firmware | empty | No firmware/container image at this target. |

## Sources

- **Project documentation** — README.md

## Limit

This appendix contains the recorded evidence, not a claim of complete coverage. A probe marked empty or error is a route for the next investigation.
