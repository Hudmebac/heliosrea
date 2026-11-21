<div align="center">

```text
██████╗ ███████╗ █████╗
██╔══██╗██╔════╝██╔══██╗
██████╔╝█████╗  ███████║
██╔══██╗██╔══╝  ██╔══██║
██║  ██║███████╗██║  ██║
╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝
```

# The box is open. The receipts are on the table.

**A local-first, evidence-led reverse-engineering room.**  
Point it at something you own, map the seam where behaviour can change, then make one small change you can prove — and undo.

<a href="#quick-start"><img src="https://img.shields.io/badge/START_THE_ROOM-0b1220?style=for-the-badge&labelColor=f6ca70&color=0b1220" alt="Start the room" /></a>
<a href="rea/README.md"><img src="https://img.shields.io/badge/READ_THE_FULL_METHOD-62e8ff?style=for-the-badge&labelColor=0b1220&color=62e8ff" alt="Read the full method" /></a>

<br/>

<img src="https://img.shields.io/badge/python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+" />
<img src="https://img.shields.io/badge/dependencies-stdlib_only-22c55e?style=for-the-badge" alt="Standard library only" />
<img src="https://img.shields.io/badge/tests-39_passing-brightgreen?style=for-the-badge" alt="39 tests passing" />
<img src="https://img.shields.io/badge/network-loopback_only-0ea5e9?style=for-the-badge" alt="Loopback only" />
<img src="https://img.shields.io/badge/targets-authorised_only-f6ca70?style=for-the-badge" alt="Authorised targets only" />
<img src="https://img.shields.io/badge/account-none-6b7280?style=for-the-badge" alt="No account" />

<br/><br/>

> **The rule:** evidence first. One seam. One smallest reversible change.  
> **A receipt or a rollback — never a guess.**

</div>

---

## ⚡ What this is

REA — **Engine Room** — is a working evidence room for systems you own or have **written permission** to inspect. It runs a fixed probe ladder, records what it found with a locator and a confidence, and shows you the place where the behaviour can actually be changed.

Then **Improve** turns that place into one small plan: the crew chooses the move, drafts the patch when it can do so safely, verifies it, and reverses it automatically if the check fails.

No account. No API key. No cloud dependency. No magic.

<table>
<tr>
<td width="50%" valign="top">
<h3>✅ What it is</h3>
<ul>
<li>A <strong>local</strong> Python console at <code>127.0.0.1:4711</code></li>
<li>A <strong>dossier</strong> per target: findings, locators, confidence, limits</li>
<li>A <strong>seam map</strong>: where source, config or protocol can be changed</li>
<li>A <strong>crew decision</strong>: one ranked move with reasons attached</li>
<li>A <strong>reversible change</strong>: apply → verify → keep or roll back</li>
<li>A <strong>static evidence site</strong> generated from real dossiers</li>
</ul>
</td>
<td width="50%" valign="top">
<h3>🚫 What it is not</h3>
<ul>
<li>Not an exploit kit or a bypass shop</li>
<li>Not a scanner pointed at other people's systems</li>
<li>Not a stock "teardown" with invented findings</li>
<li>Not a promise that a change is safe because it applied</li>
<li>Not a publication machine: the gate blocks thin evidence</li>
<li>Not a hosted service: the console never leaves your machine</li>
</ul>
</td>
</tr>
</table>

---

## 🧭 The route

```text
        ┌──────────────────┐   fixed probes   ┌────────────────────┐
        │      TARGET      │ ───────────────▶ │      DOSSIER       │
        │ repo · website   │                  │ findings·locators  │
        │ binary · capture │ ◀─────────────── │ confidence · limit │
        │ LAN · public host│   authorised     └─────────┬──────────┘
        └──────────────────┘                            │ seam
                                                ┌───────▼────────┐
                                                │    IMPROVE     │
                                                │ crew decision  │
                                                │ plan + patch   │
                                                └───────┬────────┘
                                                        │ apply
                                                ┌───────▼────────┐
                                                │    VERIFY      │
                                                │ pass → keep    │
                                                │ fail → reverse │
                                                └────────────────┘
```

---

## 🖥️ Three surfaces, plus a local Connect workspace

| Surface | Where | What it is | Leaves the machine? |
|---|---|---|---|
| **Engine Room** | `/` | Private control surface: investigations, dossiers, Improve plans | No — loopback only |
| **Website** | `/published/` | Static evidence site generated from dossiers that passed the gate | Only when you deploy it |
| **Mailroom** | `/mailroom/` | Owner console: contacts, consent, CSV export, monthly issues | No — local store, loopback only |
| **Connect** | `/connect/` | Guided device intake inside the Engine Room: crew questions, one-host LAN check, saved profile and runbook | No — loopback only |

One process serves all of them. One codebase. One data model.

---

## 🔌 Connect: bring a local device in

Say **“I want to connect my home alarm”**. Open **Connect**, pick *Home alarm*, enter the panel's LAN address, and tick the permission box. The crew asks the questions a careful operator would ask — make and model, controlling app or hub, what you want REA to do, wired or Wi-Fi, whether a local API is enabled, and whether it is professionally monitored.

Then:

1. **Ask the crew** returns the questions still open, the 12 documented ports it would check, and the honest routes (local web, RTSP, MQTT, hub, vendor app).
2. **Run safe check** makes one unauthenticated `GET /` on one private host and records what answered: port, service, banner title and server.
3. **Save connection + runbook** writes a profile to `rea/data/connections/` (gitignored) with the answers, findings, change steps and a one-command rollback.

It never asks for a password, PIN or token, never attempts a login, never changes the device, and refuses public addresses, demos and fixtures. If the panel has no supported local interface, the crew says so and routes you to the vendor app or hub instead of a port trick.

```text
/connect/            local workspace (loopback only)
/api/connect/plan    questions, ports and routes — no probe
/api/connect/scan    one host, fixed ports, no credentials
/api/connect/save    profile + runbook; delete is the rollback
```

---

## 🔍 The probe ladder

| Family | What it records |
|---|---|
| **Surface** | File inventory, manifests, environment contracts |
| **Code** | Modules, routes, imports, feature switches, TODO/HACK sites |
| **Protocol** | Hosts, client calls, websockets, auth shape |
| **History** | Git remote, recent commits, churn |
| **Binary** | PE / ELF / Mach-O / APK / SquashFS headers, strings, sections |
| **Firmware** | Container magic, partition offsets, rootfs hints |
| **Network** | This WiFi / LAN: devices, topology, edit points |

Every finding carries an ID, a locator, a confidence and a stated limit.  
Empty probes are still evidence: they queue the next technique instead of pretending to succeed.

---

## 🧩 Improve: the crew decides, the rollback protects

You write one brief. The crew ranks the mapped seams, picks the highest-value move, drafts the plan and records why.

| Gate | What it enforces |
|---|---|
| **System class** | Software, mobile, web, TV, vehicle, appliance, alarm, smart home, network, embedded — a safety boundary, not an authorisation |
| **Change level** | Observe, configure, debloat, interface, extend, replace runtime — the class decides what is allowed |
| **Baseline** | No baseline, no apply |
| **Local only** | Execution is limited to software / web targets on a **clean local git work tree** |
| **Verify** | A recorded command must exit `0` |
| **Rollback** | Non-zero exit reverses the patch automatically; any applied plan can be rolled back on demand |

**Crew lenses:** Systems Architect keeps the change inside the seam · Red Team verifies independently and fails closed · App Orchestrator keeps it to one reversible step.

<details>
<summary><b>What the crew will <i>not</i> do</b></summary>

<br/>

- Execute against a URL, a phone, a car, an alarm panel, an appliance, a TV or firmware
- Apply to a dirty work tree, so the reverse stays exact
- Draft arbitrary code and call it verified — the one auto-draft recipe is the Python `__main__` guard, and only when the top-level code is bare calls
- Publish, deploy, email or upload anything on its own
- Turn an owner's permission into permission to touch a safety system

</details>

---

<a id="quick-start" name="quick-start"></a>

## ▶️ Quick start

**Windows**

```bat
REA.bat
```

**Any OS**

```bash
python rea/server.py
```

Then open **<http://127.0.0.1:4711/>**.

Python 3.10+ on your `PATH`. No `pip install`, no virtualenv, no keys.

---

## 🗂️ Repository layout

```text
HeliosREA/
├── REA.bat                  Windows launcher
├── crew.py                  personas, skills and task routing
├── rea/
│   ├── server.py            local console + JSON API (loopback only)
│   ├── engine.py            probe ladder, findings, dossier store
│   ├── improvements.py      system classes, plans, suggestions, briefs
│   ├── decision.py          crew decision, advice, safe patch recipe
│   ├── executor.py          git-anchored apply, verify, auto-reverse
│   ├── studio.py            static site and publication gate
│   ├── mailroom.py          local contacts and monthly issues
│   ├── viewer/              the console UI
│   ├── README.md            full method, gates and API reference
│   └── data/                local dossiers, plans, contacts  (gitignored)
└── site/                    generated public site              (gitignored)
```

Your evidence never enters git: `config.json`, `rea/data/` and `site/` stay out of history.

---

## 🧪 Quality gates

```bash
python -m unittest discover -s rea -p "test_*.py"
```

```text
Ran 39 tests — OK
```

The suite covers the probe ladder, dossier shape, publication gate, mailroom consent, the Improve status machine, blocked class/level pairs, suggestion ranking, crew decision, the Connect safety refusals and loopback banner check, and a **real git apply → verify → rollback** cycle in a temporary repository.

<details>
<summary><b>API at a glance</b></summary>

<br/>

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/health` | Version, probe count, dossier count |
| `POST` | `/api/investigations` | Start an authorised investigation |
| `GET` | `/api/investigations/<id>` | One dossier (poll while running) |
| `POST` | `/api/improvements/suggest` | Rank evidence-backed suggestions |
| `POST` | `/api/improvements/decide` | Crew picks one move, drafts the plan, records advice |
| `POST` | `/api/improvements/execute` | Preview or apply a baselined patch |
| `POST` | `/api/improvements/rollback` | Reverse the applied patch |
| `GET` | `/api/improvements` | Plans, suggestions, classes, levels |
| `GET` | `/api/improvements/<id>/brief.md` | Downloadable change brief |
| `POST` | `/api/studio/publish` | Strict evidence gate → static publication |

Full reference: [`rea/README.md`](rea/README.md)

</details>

---

## 🛡️ Boundaries

- **Authorised targets only.** You own it, or you have written permission.
- **No stealth, bypass, credential replay or borrowed access.**
- **No deployment, upload or automatic publication.**
- **Loopback only.** The console binds to `127.0.0.1`; there is no remote login.
- **Redact before publishing.** The gate refuses fixtures, demos and thin evidence.
- **A change is not proven because it applied.** It is proven when the recorded check passes.

---

## ⚠️ Current limits

- The crew auto-drafts exactly one recipe today: the Python `__main__` guard. Everything else gets a plan and advice, but the diff is yours.
- Automatic execution covers **software and web** plans on a **clean local git work tree**. Every other class stays a human-applied change.
- The public website address has not been supplied yet; `/published/` is a local preview.
- No licence file is present yet — add one before accepting contributions.

---

<div align="center">

**Evidence first. One seam. One change. Proof attached.**

<sub>HELIOS · REA — Engine Room · local-first, stdlib only, authorised targets only</sub>

</div>
