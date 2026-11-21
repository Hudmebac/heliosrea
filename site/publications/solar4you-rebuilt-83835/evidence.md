# Evidence appendix — solar4you [REBUILT] — evidence teardown

- **Dossier:** `REA-20261121-afd06c73-83835`
- **Target:** solar4you [REBUILT]
- **Kind:** Local source tree
- **Finished:** 2026-11-21T09:23:03
- **Scope:** not stated
- **Authorisation:** operator attested ownership / written permission
- **Dossier SHA-256:** `75715d20efcfe131431bff8f0dbd54fc604b25495620dbf053e6ce60bbd4ccaf`
- **Evidence records:** 7 · **Change surfaces:** 2

## Findings

| ID | Family | Finding | Locator | Confidence |
|---|---|---|---|---|
| F-001 | Surface | 4 files · 22.9 KB | `—` | 95% |
|  |  | Languages by extension: .js, .html, .md, .css. Top folders: flat tree. Binaries/containers: 0. |  |  |
| F-002 | Surface | Entry-point candidates | `—` | 80% |
|  |  | index.html |  |  |
| F-003 | Code | 2 source files · 0 imports · 0 routes | `—` | 85% |
|  |  | no route declarations found |  |  |
| F-004 | Protocol | 1 request surface(s) | `—` | 85% |
|  |  | Absolute hosts: http://www.w3.org/2000/svg. Client calls: none |  |  |
| F-005 | Protocol | Remote dependency · www.w3.org | `—` | 90% |
|  |  | http://www.w3.org/2000/svg |  |  |
| F-006 | Docs | README.md · 25 lines | `README.md` | 80% |
|  |  | Headings: Solar4You, Run, Features, Data and boundaries, Verify. Commands: none. Claims: none |  |  |
| F-007 | Docs | README.md · 25 lines | `README.md` | 80% |
|  |  | Headings: Solar4You, Run, Features, Data and boundaries, Verify. Commands: none. Claims: none |  |  |

## Change surfaces

| ID | Category | Locator | Action | Risk | Confidence |
|---|---|---|---|---|---|
| E-01 | source | `solar4you [REBUILT]` | Edit the source tree directly; take a baseline test first, then patch the smallest module. | low | 90% |
| E-02 | protocol | `http://www.w3.org/2000/svg` | Reimplement or intercept the client protocol at the request boundary; keep it read-only until authorised. | medium | 80% |

## Probe ledger

| Probe | Family | Status | Result |
|---|---|---|---|
| Map the surface | Surface | signal | 4 files, 4 language signals, 0 binaries. |
| Read manifests and config | Surface | empty | No recognisable manifest or config file. |
| Check package provenance | Sources | empty | No package names to resolve. |
| Learn from GitHub | Sources | empty | No GitHub repository identified for this target. |
| Map code structure | Code | signal | 2 files, 0 route(s), 0 switch(es). |
| Map request surface | Protocol | signal | 1 request sites, 1 hosts. |
| Read repository history | History | empty | Not a git working tree. |
| Mine documentation | Docs | signal | 2 document(s) mined. |
| Find the test harness | Verification | empty | No verification harness found. |
| Parse the artifact | Binary | empty | No compiled artifact at this target. |
| Map security controls | Controls | empty | No compiled artifact to map controls from. |
| Extract strings | Binary | empty | No compiled artifact to pull strings from. |
| Map firmware containers | Firmware | empty | No firmware/container image at this target. |

## Sources

- **Project documentation** — README.md

## Limit

This appendix contains the recorded evidence, not a claim of complete coverage. A probe marked empty or error is a route for the next investigation.
