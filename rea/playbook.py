#!/usr/bin/env python3
"""Structured operator playbook for REA.

Everything here is deterministic: instructions, guides, prompt templates, the
profession lenses and the idea catalogue. No model call is made, so the same
inputs always produce the same brief.
"""

from __future__ import annotations

import os
import re

ANGLES = ("Audit", "Compatibility", "Product")

INSTRUCTIONS = (
    {
        "n": "01",
        "title": "Attest the boundary",
        "detail": "Own the target or hold written permission. The attestation is stored in the "
                  "dossier, and remote probing is refused without it. Decide the scope note before "
                  "you start: one device, one build, one account — not 'everything'.",
    },
    {
        "n": "02",
        "title": "Point REA at the target",
        "detail": "A GitHub repo, a https URL, a local folder or binary, a HAR capture, "
                  "this WiFi/LAN, or a public host. Auto-detect covers the common cases; "
                  "force the kind only when the path is ambiguous.",
    },
    {
        "n": "03",
        "title": "Read the ladder, not just the verdict",
        "detail": "Signal means evidence landed. Empty means this technique was blind — the next "
                  "probe and a next move are recorded. Skipped means the run was offline. Error "
                  "means the target refused us; it is not a verdict either.",
    },
    {
        "n": "04",
        "title": "Choose one access mode",
        "detail": "Source, configuration, binary, protocol, edge content, firmware or fork. The mode "
                  "with the lowest risk and enough evidence wins. Change one thing, keep a baseline, "
                  "then record what actually changed.",
    },
)

GUIDES = (
    {
        "id": "first-run",
        "title": "Run your first investigation",
        "summary": "From a target to a usable edit-point map in one pass.",
        "steps": [
            "Open Investigate, paste the target, tick the authorisation attestation and add a scope note.",
            "Start the run. Leave the page open; the probe ladder updates as each technique returns.",
            "Read the verdict and the access modes first. Ignore the raw findings until you know which mode is real.",
            "Open Edit points and pick the lowest-risk seam that has evidence behind it.",
            "Download the Markdown dossier and attach it to the task or mission that will act on it.",
        ],
    },
    {
        "id": "read-dossier",
        "title": "Read a dossier correctly",
        "summary": "What each panel means, and what it does not mean.",
        "steps": [
            "Verdict: count of change surfaces across access modes. It is a map, not a completion claim.",
            "Access modes: what level of change is available, with the risk of each mode.",
            "Probe ladder: signal / empty / skipped / error. Retries show which technique replaced a blind probe.",
            "Findings: evidence with a locator and confidence. A 55% finding is a lead, not a fact.",
            "Edit points: locator + action + risk + confidence. Nothing here authorises a production change by itself.",
            "Next moves: the route out of every dead end, including the ones that need a different capture technique.",
        ],
    },
    {
        "id": "no-signal",
        "title": "When a probe finds nothing",
        "summary": "The escalation routes that turn a blind spot into evidence.",
        "steps": [
            "A website refuses or returns no API: export a HAR from the browser Network panel and investigate the .har file.",
            "No manifest: inspect the binary header and strings; the runtime is usually named in the imports or literals.",
            "Source structure is hidden: pull strings, symbol names and error text, then reconstruct modules from call sites.",
            "The binary is packed: stop static patching, identify the packer, then plan a runtime hook.",
            "No firmware magic at offset 0: carve at 4 KiB boundaries and look for SquashFS, UBIFS or an Android boot image.",
            "No public repository: search GitHub for unique strings from the target and check the licence before reuse.",
            "Nothing at all: confirm path, permissions and capture scope — then change the target, not the conclusion.",
        ],
    },
    {
        "id": "authorisation",
        "title": "Stay inside the authorisation boundary",
        "summary": "Passive observation, no exploitation, no credential replay.",
        "steps": [
            "Remote probes are same-host GET/HEAD with a request budget; there is no brute force or auth bypass.",
            "Captured HAR traffic is read for names and shapes. Header, query, body and token values are never stored.",
            "A captured token is evidence, not a key. Re-authenticate with fresh credentials when you reproduce a flow.",
            "Binary and firmware work needs a recovery path before you touch anything: backup, serial access, or a known-good image.",
            "If the target is not yours and you do not have written permission, stop and get it in writing.",
        ],
    },
    {
        "id": "implementation",
        "title": "From finding to compatible implementation",
        "summary": "Decide emulate, port or rewrite before writing code.",
        "steps": [
            "Emulate when the external contract is stable and the runtime is replaceable.",
            "Port when the platform gap is narrow and the source or a permissive upstream exists.",
            "Rewrite when the protocol is the only stable surface and the original is unmaintainable.",
            "Keep one adapter at the boundary; do not spread compatibility branches through the product.",
            "Verification is a behaviour baseline: same inputs, same outputs, documented differences.",
        ],
    },
    {
        "id": "binary-firmware",
        "title": "Binary and firmware work",
        "summary": "Read, patch and repack without destroying the device.",
        "steps": [
            "Identify the container and architecture before opening a disassembler.",
            "Map writable and executable sections, imports, overlays and signing before choosing a patch site.",
            "Prefer a runtime hook or an import shim over a byte patch: it survives updates and signature checks.",
            "For firmware, record partition layout, compression and block size; repack only touched entries.",
            "Keep the original image and verify the device boots after every change.",
        ],
    },
    {
        "id": "workflow-study",
        "title": "Observe a human workflow",
        "summary": "Turn repeated work into an automatable, consented process map.",
        "steps": [
            "Get explicit consent from every person observed and record the retention period.",
            "Capture the steps, tools, handoffs, waits and exceptions — not personal content.",
            "Import a HAR of the SaaS tools involved to see the actual system calls.",
            "Rank steps by frequency × delay × reversibility; automate the smallest reversible step first.",
            "Pilot with one person, measure time saved and error rate, then widen.",
        ],
    },
)

PROMPTS = (
    {
        "id": "full-brief",
        "title": "Full REA investigation brief",
        "when": "Hand a target to an engineer or an AI agent and get an evidence-backed dossier back.",
        "text": (
            "Act as the Reverse Engineering Engineer on an authorised REA investigation.\n\n"
            "Target: <target>\nScope: <scope>\nGoal: <what decision this investigation must support>\n\n"
            "Rules:\n"
            "- Passive observation only; no auth bypass, brute force or exploitation.\n"
            "- Every claim needs a locator and a confidence. Say 'not observed' when the evidence is absent.\n"
            "- Never store or replay credentials; mask secrets at capture time.\n"
            "- A probe that finds nothing is a route: name the next technique, not a failure.\n\n"
            "Deliver, in this order:\n"
            "1. Surface map (kind, files/hosts, runtime, container).\n"
            "2. Access modes available, with the risk of each.\n"
            "3. Protocol and configuration surfaces.\n"
            "4. Edit points with locator, action, risk and confidence.\n"
            "5. Verification plan and next three probes.\n"
        ),
    },
    {
        "id": "escalation",
        "title": "Probe escalation prompt",
        "when": "The first technique was blind and you need the next route, not a conclusion.",
        "text": (
            "The probe '<probe>' returned no signal on <target>.\n"
            "Do not treat that as failure. List three different techniques that could reveal the "
            "same behaviour, in order of cost, and state what evidence each would produce.\n\n"
            "Constraints: authorised target, passive methods only, no credential capture.\n"
            "Output: probe -> what it observes -> expected artefact -> stop condition.\n"
        ),
    },
    {
        "id": "architect-decision",
        "title": "Emulate, port or rewrite",
        "when": "You understand the behaviour and must choose an implementation strategy.",
        "text": (
            "Given this evidence: <paste edit points and protocol notes>, recommend one of:\n"
            "A) emulate the external contract, B) port the existing implementation, C) rewrite the module.\n\n"
            "Score each on: contract stability, licence, platform gap, maintenance cost, time to first "
            "verified behaviour, and reversibility. Name the failure mode of the chosen option.\n"
            "Output a one-page decision record with the rejected options and why.\n"
        ),
    },
    {
        "id": "dossier-review",
        "title": "Red-team a dossier",
        "when": "Before anyone acts on the investigation.",
        "text": (
            "You are independent Red Team. Review this REA dossier: <paste dossier>.\n"
            "Challenge every finding: is the locator real, is the confidence justified, is the "
            "authorisation boundary respected, is any edit point actually a production risk?\n"
            "List unsupported claims, missing evidence, privacy issues and the three checks you would "
            "run before accepting the dossier. Verdict: accept / accept with caveats / reject.\n"
        ),
    },
    {
        "id": "code-archaeology",
        "title": "Code archaeology report",
        "when": "An abandoned or undocumented codebase must be understood before it is changed.",
        "text": (
            "Treat this repository as an archaeological site: <repo or local path>.\n"
            "Reconstruct: entry points, runtime, data stores, external contracts, configuration, "
            "dead code, and the business rules encoded in conditionals.\n"
            "For each module give: purpose, evidence, confidence, and what breaks if it is removed.\n"
            "End with a safe first change and the test that would prove it.\n"
        ),
    },
    {
        "id": "workflow-study",
        "title": "Human workflow study",
        "when": "A repeated human process may be automatable.",
        "text": (
            "Observe the workflow '<workflow>' with consent. Record only process metadata.\n"
            "Map: trigger, steps, tools, handoffs, waits, exceptions, rework and decision points.\n"
            "Rank each step by frequency x delay x reversibility. Identify the smallest step that can "
            "be automated without moving accountability away from a person.\n"
            "Deliver a process map, a time-cost estimate with assumptions, and a one-person pilot.\n"
        ),
    },
    {
        "id": "server-audit",
        "title": "What does this server actually do?",
        "when": "A host or service has no reliable owner documentation.",
        "text": (
            "Inventory this server using read-only discovery: listening ports, services, scheduled "
            "jobs, containers, databases, config files and outbound dependencies.\n"
            "For each service explain: purpose, owner candidate, data touched, and what depends on it.\n"
            "Flag undocumented, unpatched or abandoned systems. Do not stop or change anything.\n"
            "Output a dependency map and a removal-risk table (safe / check first / breaks 7 systems).\n"
        ),
    },
    {
        "id": "idea-to-mission",
        "title": "Idea to mission",
        "when": "A product idea needs a first investigation and a testable deliverable.",
        "text": (
            "Turn this idea into one REA mission: <idea>.\n"
            "State the user, the painful job, the evidence needed, the smallest investigation, the "
            "first paid deliverable, the riskiest assumption and the cheapest test of it.\n"
            "Give five tasks with owners from the crew and a definition of done for each.\n"
            "Refuse to invent market size or user quotes; mark assumptions explicitly.\n"
        ),
    },
)

PROFESSIONS = (
    {
        "id": "reverse-engineer",
        "name": "Reverse Engineering Engineer",
        "focus": "Analyse compiled applications, reconstruct undocumented behaviour and build compatible implementations.",
        "questions": ("How does this software actually work?", "Which APIs and runtimes does it depend on?",
                      "What protocol is it speaking?"),
        "tools": ("IDA Pro", "Ghidra", "Binary Ninja", "x64dbg", "WinDbg"),
        "deliverables": ("Behaviour map", "Function and API inventory", "Compatibility notes"),
        "rea": "Binary header, strings, manifests and protocol probes; edit points in writable sections, endpoints and config.",
    },
    {
        "id": "platform-compat",
        "name": "Platform Compatibility Engineer",
        "focus": "Make software run on unsupported devices and build compatibility layers between platforms.",
        "questions": ("Which APIs are missing on the target?", "Can behaviour be shimmed without forking the app?",
                      "What is the minimum viable compatibility layer?"),
        "tools": ("Wine/Proton patterns", "Android compatibility layers", "TV platform SDKs", "API shims"),
        "deliverables": ("Compatibility matrix", "Shim design", "Port plan", "Device test matrix"),
        "rea": "Surface, manifest, binary import and protocol probes show the gap; access modes separate port from rewrite.",
    },
    {
        "id": "embedded-re",
        "name": "Embedded Systems Reverse Engineer",
        "focus": "Smart TVs, set-top boxes, routers and IoT: firmware, hardware boundaries and Linux internals.",
        "questions": ("How does the device boot?", "What rootfs and init system does it use?",
                      "Where is the trust boundary?"),
        "tools": ("binwalk", "UART/JTAG", "SquashFS/UBIFS", "ARM toolchains", "BusyBox"),
        "deliverables": ("Firmware map", "Partition layout", "Recovery procedure", "Rootfs change plan"),
        "rea": "Firmware container, binary and strings probes; edit points for init scripts, web UI and rootfs repack.",
    },
    {
        "id": "security-re",
        "name": "Security Research Engineer",
        "focus": "Vulnerability discovery, binary analysis, security auditing and risk assessment.",
        "questions": ("Where is the trust boundary?", "What is exposed without authentication?",
                      "Which finding would actually matter?"),
        "tools": ("Static analysis", "Debuggers", "Header and cookie review", "Threat modelling"),
        "deliverables": ("Risk register", "Reproduction steps", "Fix recommendation", "Disclosure note"),
        "rea": "Header, endpoint, auth-shape and binary probes; edit points carry a risk label, and findings stay passive.",
    },
    {
        "id": "archaeologist",
        "name": "Software Archaeologist",
        "focus": "Understand abandoned systems, recover lost functionality and rebuild undocumented codebases.",
        "questions": ("What did this system do?", "Which behaviour is still load-bearing?",
                      "What breaks if we remove it?"),
        "tools": ("Git history", "Logs", "Schema archaeology", "String and symbol recovery"),
        "deliverables": ("System history", "Business-rule catalogue", "Removal risk map", "Modernisation path"),
        "rea": "History, docs, code-structure and dependency probes; high-churn files become source edit points.",
    },
    {
        "id": "systems",
        "name": "Systems Engineer",
        "focus": "Windows and Linux internals, process management and OS integration.",
        "questions": ("How does the process start and persist?", "What resources and privileges does it need?",
                      "Where does it integrate with the OS?"),
        "tools": ("Process Explorer", "procmon", "systemd/Windows services", "Containers"),
        "deliverables": ("Runtime profile", "Dependency map", "Integration contract", "Operational runbook"),
        "rea": "Binary, manifest, test-surface and config probes; edit points for service config and startup paths.",
    },
    {
        "id": "protocol",
        "name": "Protocol Engineering Specialist",
        "focus": "Network protocols, streaming platforms, IPTV and custom APIs.",
        "questions": ("What is exchanged, in which order?", "How is the session authenticated?",
                      "Can a client be reimplemented from observed behaviour?"),
        "tools": ("Wireshark", "HAR capture", "Reverse proxies", "Schema inference"),
        "deliverables": ("Protocol map", "Message catalogue", "Client reimplementation plan", "Mock server"),
        "rea": "Web, API-surface, HAR and strings probes; edit points at the request boundary and base URL.",
    },
    {
        "id": "modernization",
        "name": "Application Modernization Engineer",
        "focus": "Convert legacy applications, upgrade unsupported platforms and migrate systems safely.",
        "questions": ("What is the smallest behaviour-preserving change?", "What is the rollback path?",
                      "Which contract must remain identical?"),
        "tools": ("Strangler-fig patterns", "Adapters", "Golden-master tests", "Feature flags"),
        "deliverables": ("Migration plan", "Parity checklist", "Rollback plan", "Cutover evidence"),
        "rea": "Access modes and edit points give the seam; the baseline behaviour is the acceptance test.",
    },
    {
        "id": "architect",
        "name": "Software Architect (RE background)",
        "focus": "Turn discoveries into one architecture that can support several platforms.",
        "questions": ("Should we emulate, port or rewrite?", "Can one abstraction serve every target?",
                      "Which decision is expensive to reverse?"),
        "tools": ("Decision records", "Interface contracts", "Platform abstraction layers"),
        "deliverables": ("Architecture note", "Contract definition", "Decision record", "Risk register"),
        "rea": "The dossier's modes and edit points are the inputs; the output is one boundary, not many branches.",
    },
    {
        "id": "ai-systems",
        "name": "AI Systems Engineer",
        "focus": "Automate understanding and support: platform detection, compatibility reports, agent workflows.",
        "questions": ("Can the system identify the platform itself?", "Which evidence can be structured for a model?",
                      "Where must a human remain accountable?"),
        "tools": ("Local models", "Structured extraction", "Evaluation harnesses", "Human-in-the-loop gates"),
        "deliverables": ("Automation plan", "Evaluation set", "Confidence policy", "Escalation design"),
        "rea": "Probes already emit structured evidence; the playbook briefs are deterministic and safe to automate.",
    },
    {
        "id": "product",
        "name": "Product Strategist",
        "focus": "Find the problem worth solving and the first paid deliverable.",
        "questions": ("Who feels this pain today?", "What would they pay for first?",
                      "What is the cheapest test of demand?"),
        "tools": ("Problem interviews", "Offer design", "Pricing tests", "Assumption mapping"),
        "deliverables": ("Problem frame", "Offer and price", "Pilot plan", "Evidence log"),
        "rea": "Idea briefs state who pays, the riskiest assumption and the first delivery, without invented market data.",
    },
    {
        "id": "ux",
        "name": "UX Designer",
        "focus": "Make complex reverse-engineering output usable without technical knowledge.",
        "questions": ("Can the operator act without reading raw findings?", "What is the next clear step?",
                      "Which state is confusing?"),
        "tools": ("Information architecture", "State matrices", "Plain-language copy", "Usability checks"),
        "deliverables": ("Flow", "State coverage", "Plain-language report", "Usability evidence"),
        "rea": "The ladder, access modes, edit points and next moves are the information architecture.",
    },
    {
        "id": "technical-director",
        "name": "Technical Director / R&D",
        "focus": "Set platform strategy, pick the bets and keep engineering evidence honest.",
        "questions": ("Which mission area compounds?", "What capability is missing?",
                      "Are we claiming more than the evidence supports?"),
        "tools": ("Portfolio review", "Feasibility studies", "Capability map", "Decision logs"),
        "deliverables": ("Platform strategy", "Mission portfolio", "Feasibility verdict", "Investment case"),
        "rea": "Dossiers are the evidence base; the playbook turns them into repeatable mission areas.",
    },
    {
        "id": "evidence-editor",
        "name": "Evidence Editor / Fact Checker",
        "focus": "Turn a dossier into publishable claims without inflating what was observed.",
        "questions": ("Does every sentence trace to a finding ID and locator?",
                      "What is still unknown?", "What must be redacted before publication?"),
        "tools": ("Claim-to-evidence table", "Hash manifest", "Redaction pass", "Correction log"),
        "deliverables": ("Evidence appendix", "Claim map", "Publication gate", "Correction note"),
        "rea": "Reads findings, edits, probes and next moves; blocks a story when the gate fails.",
    },
    {
        "id": "video-producer",
        "name": "Evidence-Led Video Producer",
        "focus": "Cut a clear technical story from real screen capture, artifact shots and a dossier.",
        "questions": ("What is the one seam the viewer must see?",
                      "Which shot proves the claim?", "What must be shown as a limit?"),
        "tools": ("OBS Studio", "DaVinci Resolve", "Audacity", "Phone camera", "Caption pass"),
        "deliverables": ("Paper edit", "Shot list", "Screen capture", "Captioned cut"),
        "rea": "Builds the shot script from finding IDs and locators; never invents b-roll or a result.",
    },
    {
        "id": "web-publisher",
        "name": "Web Publisher / Technical SEO",
        "focus": "Ship a fast, accessible evidence page that can be found and trusted.",
        "questions": ("Does the page state the scope and the limit?",
                      "Are metadata, links and assets correct?", "What is safe to deploy?"),
        "tools": ("Semantic HTML", "Static hosting", "Link checker", "Metadata and sitemap", "Accessibility pass"),
        "deliverables": ("Story page", "Metadata", "Link/QA report", "Deployment bundle"),
        "rea": "Publishes only the generated evidence bundle; no unreviewed screenshots or secrets.",
    },
    {
        "id": "distribution",
        "name": "Distribution / Partnerships Lead",
        "focus": "Put the evidence in front of the right buyers without spam or borrowed credibility.",
        "questions": ("Who has the exact problem this teardown proves?",
                      "Which channel reaches them without a cold blast?", "What can we measure honestly?"),
        "tools": ("Buyer map", "Warm-intro list", "Public thread research", "UTM links", "Simple outreach log"),
        "deliverables": ("Distribution plan", "Warm-intro list", "Channel test", "Weekly measurement"),
        "rea": "Uses the published evidence and the buyer's public words; never scrapes private data or sends bulk messages.",
    },
)

IDEAS = (
    {
        "id": "human-workflow",
        "name": "Human Workflow Reverse Engineering",
        "money": 5,
        "difficulty": 3,
        "summary": "Observe how people actually work, find repeated steps and delays, then generate automations with consent.",
        "who_pays": "Operations leaders, agencies and finance teams with repetitive back-office work.",
        "targets": ("screen recordings", "process notes", "SaaS exports", "HAR captures"),
        "probes": ("traffic.har", "docs.mine", "code.structure", "test.surface"),
        "surfaces": ("protocol", "config", "content"),
        "missions": (
            "Shadow one workflow end to end and record only process metadata.",
            "Rank steps by frequency x delay x reversibility.",
            "Automate the smallest reversible step and measure time saved.",
        ),
        "risks": ("employee privacy", "consent and retention", "automating accountability away"),
        "role": "product",
    },
    {
        "id": "digital-life-twin",
        "name": "Digital Life Twin",
        "money": 5,
        "difficulty": 4,
        "summary": "Build a behavioural model of how one person operates across calendar, mail, documents and projects — not a diary, an operating system.",
        "who_pays": "Founders, executives and independent professionals who pay to protect their time.",
        "targets": ("local documents", "calendar exports", "email exports", "project files"),
        "probes": ("surface.scan", "docs.mine", "code.structure", "manifest.read"),
        "surfaces": ("config", "content", "protocol"),
        "missions": (
            "Map the operator's recurring decisions and handoffs.",
            "Identify the three highest-cost repeated tasks.",
            "Propose one automation with an explicit consent and privacy boundary.",
        ),
        "risks": ("highly personal data", "consent", "over-confident inference"),
        "role": "ai-systems",
    },
    {
        "id": "ai-code-archaeologist",
        "name": "AI Code Archaeologist",
        "money": 5,
        "difficulty": 3,
        "summary": "Feed a system source, docs and binaries; reconstruct architecture, business rules and a replacement path.",
        "who_pays": "Enterprises with abandoned systems and consultancies inheriting undocumented code.",
        "targets": ("GitHub repos", "local source trees", "legacy binaries", "docs"),
        "probes": ("source.github", "manifest.read", "code.structure", "git.history", "docs.mine", "test.surface"),
        "surfaces": ("source", "config", "dependency", "verification"),
        "missions": (
            "Recover the module map and entry points.",
            "Extract business rules from conditionals and tests.",
            "Produce a safe first change with a golden-master test.",
        ),
        "risks": ("licence boundaries", "false business-rule claims", "missing verification"),
        "role": "archaeologist",
    },
    {
        "id": "vehicle-intelligence",
        "name": "Vehicle Intelligence Platform",
        "money": 4,
        "difficulty": 4,
        "summary": "Read vehicle data, explain fault trends and battery health, and predict failures without pretending to be a dealership tool.",
        "who_pays": "Independent garages, fleet operators and used-car buyers.",
        "targets": ("diagnostic app APKs", "OBD exports", "firmware images", "service records"),
        "probes": ("binary.header", "manifest.read", "binary.strings", "api.surface", "device.firmware"),
        "surfaces": ("binary", "protocol", "firmware", "config"),
        "missions": (
            "Map the diagnostic protocol and the app's data model.",
            "Build a fault-trend report from exported sessions.",
            "Validate predictions against a known-good vehicle.",
        ),
        "risks": ("safety-critical claims", "vehicle warranty", "personal location data"),
        "role": "embedded-re",
    },
    {
        "id": "smart-home-brain",
        "name": "Universal Smart Home Brain",
        "money": 4,
        "difficulty": 3,
        "summary": "Discover devices, normalise their protocols and run one automation layer across ecosystems.",
        "who_pays": "Homeowners with mixed ecosystems and integrators who support them.",
        "targets": ("hub firmware", "device apps", "local network captures", "HAR traffic"),
        "probes": ("device.firmware", "binary.strings", "api.surface", "traffic.har"),
        "surfaces": ("protocol", "firmware", "config"),
        "missions": (
            "Map device discovery and local control protocols.",
            "Normalise on/off, level and sensor events into one model.",
            "Pilot one cross-ecosystem automation with a manual override.",
        ),
        "risks": ("device safety", "cloud dependency changes", "physical access"),
        "role": "protocol",
    },
    {
        "id": "shadow-it",
        "name": "Shadow IT Detector",
        "money": 5,
        "difficulty": 3,
        "summary": "Discover forgotten software, secret databases and unknown systems, then explain what each one touches.",
        "who_pays": "Security leaders, IT teams and companies preparing for audit or migration.",
        "targets": ("server inventories", "network captures", "SaaS billing exports", "endpoint inventories"),
        "probes": ("traffic.har", "surface.scan", "manifest.read", "api.surface", "test.surface"),
        "surfaces": ("protocol", "config", "policy"),
        "missions": (
            "Inventory services and owners from observed traffic and config.",
            "Flag abandoned or unpatched systems with evidence.",
            "Produce a removal-risk table, not a shutdown list.",
        ),
        "risks": ("false ownership claims", "privacy", "disruptive remediation"),
        "role": "security-re",
    },
    {
        "id": "server-intelligence",
        "name": "Server Intelligence Platform",
        "money": 5,
        "difficulty": 3,
        "summary": "Explain what a server actually does, which systems depend on it and what breaks if it is removed.",
        "who_pays": "Enterprises with undocumented hosts and managed-service providers inheriting them.",
        "targets": ("Linux/Windows hosts", "container inventories", "service configs", "traffic captures"),
        "probes": ("surface.scan", "manifest.read", "code.structure", "api.surface", "test.surface"),
        "surfaces": ("config", "source", "protocol", "policy"),
        "missions": (
            "Inventory services, ports, jobs and databases read-only.",
            "Name the likely owner and data touched per service.",
            "Produce a dependency map and removal-risk table.",
        ),
        "risks": ("production access", "credentials", "false dependency claims"),
        "role": "systems",
    },
    {
        "id": "voice-marketplace",
        "name": "Voice Skill Marketplace Analyzer",
        "money": 5,
        "difficulty": 3,
        "summary": "Analyse thousands of voice applications for popular functions, missing capabilities and commercial gaps.",
        "who_pays": "Voice developers, platform teams and agencies selling skill portfolios.",
        "targets": ("marketplace listings", "skill manifests", "public APIs", "HAR captures"),
        "probes": ("surface.scan", "manifest.read", "api.surface", "docs.mine", "traffic.har"),
        "surfaces": ("protocol", "content", "config"),
        "missions": (
            "Build a comparable feature matrix from public listings.",
            "Identify the most common missing capability.",
            "Validate one gap with developers before building.",
        ),
        "risks": ("terms of service", "listing accuracy", "thin market claims"),
        "role": "product",
    },
    {
        "id": "dealership-transparency",
        "name": "Dealership Transparency Platform",
        "money": 4,
        "difficulty": 4,
        "summary": "Turn service records, diagnostics and error histories into a true vehicle condition report.",
        "who_pays": "Used-car buyers, insurers and independent inspectors.",
        "targets": ("diagnostic exports", "service records", "vehicle app data", "firmware versions"),
        "probes": ("manifest.read", "binary.strings", "api.surface", "docs.mine"),
        "surfaces": ("protocol", "config", "binary"),
        "missions": (
            "Normalise diagnostic exports into one condition schema.",
            "Separate observed faults from inferred risk.",
            "Pilot on ten known vehicles and publish the error rate.",
        ),
        "risks": ("consumer harm from false predictions", "data licensing", "dealer pushback"),
        "role": "product",
    },
    {
        "id": "ev-optimization",
        "name": "EV Optimization Engine",
        "money": 4,
        "difficulty": 4,
        "summary": "Analyse charging patterns, efficiency and battery behaviour to recommend lifetime battery strategy.",
        "who_pays": "EV owners, fleet operators and charge-point operators.",
        "targets": ("charging session exports", "vehicle telemetry", "route data", "app captures"),
        "probes": ("api.surface", "traffic.har", "manifest.read", "docs.mine"),
        "surfaces": ("protocol", "config", "content"),
        "missions": (
            "Build a clean session dataset and define battery-health metrics.",
            "Test one recommendation against a control group.",
            "Publish assumptions and uncertainty, not a single score.",
        ),
        "risks": ("battery-safety claims", "personal travel data", "thin baselines"),
        "role": "ai-systems",
    },
    {
        "id": "ai-reverse-engineer",
        "name": "AI Reverse Engineer",
        "money": 5,
        "difficulty": 5,
        "summary": "Give a model an executable, source and docs; ask for the architecture, bottlenecks and a replacement plan.",
        "who_pays": "Software companies buying diligence, modernisation and compatibility research.",
        "targets": ("binaries", "repositories", "documentation", "live APIs"),
        "probes": ("source.github", "manifest.read", "code.structure", "binary.header", "api.surface", "docs.mine"),
        "surfaces": ("source", "binary", "protocol", "dependency"),
        "missions": (
            "Produce a verified architecture map with evidence.",
            "Identify the highest-cost bottleneck from measured behaviour.",
            "Generate a replacement slice and prove parity.",
        ),
        "risks": ("over-claiming model understanding", "licence", "verification cost"),
        "role": "architect",
    },
    {
        "id": "waste-detective",
        "name": "AI Waste Detective",
        "money": 5,
        "difficulty": 3,
        "summary": "Continuously find tiny inefficiencies — idle cloud, duplicate licences, forgotten subscriptions, standby energy — and aggregate them into measurable savings.",
        "who_pays": "CFOs, IT managers and operations leads who feel the leak but cannot see it.",
        "targets": ("cloud billing exports", "licence inventories", "energy data", "SaaS admin exports"),
        "probes": ("surface.scan", "manifest.read", "api.surface", "docs.mine"),
        "surfaces": ("config", "dependency", "policy"),
        "missions": (
            "Inventory spend and usage from exports; never infer from names alone.",
            "Rank leaks by annualised value x confidence x reversibility.",
            "Recover one leak end to end and measure the saving.",
        ),
        "risks": ("false savings claims", "contract terms", "removing something still in use"),
        "role": "systems",
    },
    {
        "id": "rounding-leak",
        "name": "Rounding Leak Detective",
        "money": 4,
        "difficulty": 2,
        "summary": "Audit payroll, billing and ledger exports for fractional-cent rounding drift: residue that accumulates one payment at a time, plus the reconciliation checks that expose it.",
        "who_pays": "Finance teams, payroll providers and auditors who must prove control totals reconcile.",
        "targets": ("payroll exports", "ledger exports", "billing CSVs", "payment reports"),
        "probes": ("surface.scan", "docs.mine", "test.surface"),
        "surfaces": ("content", "config", "policy"),
        "missions": (
            "Recompute payments at full precision and reconcile them to posted cents.",
            "Quantify one-directional rounding residue and its trend.",
            "Recommend a documented rounding policy and an exception monitor.",
        ),
        "risks": ("financial privacy", "false accusation", "rounding-policy ambiguity"),
        "role": "systems",
    },
    {
        "id": "subscription-discovery",
        "name": "Subscription Discovery",
        "money": 4,
        "difficulty": 2,
        "summary": "Reverse engineer statements and admin exports to find forgotten, duplicate and underused subscriptions for consumers and small businesses.",
        "who_pays": "Small businesses and households with too many renewals.",
        "targets": ("bank statement exports", "SaaS billing exports", "email receipts"),
        "probes": ("docs.mine", "manifest.read", "traffic.har"),
        "surfaces": ("config", "content", "policy"),
        "missions": (
            "Normalise transactions without storing full account numbers.",
            "Cluster renewals and flag duplicates or zero-use services.",
            "Produce a cancel/keep list with the renewal date and evidence.",
        ),
        "risks": ("financial privacy", "wrong cancellation advice", "statement access"),
        "role": "product",
    },
    {
        "id": "cloud-waste",
        "name": "Cloud Waste Intelligence",
        "money": 5,
        "difficulty": 3,
        "summary": "Find idle servers, overprovisioned databases, orphaned storage and unused reservations across cloud accounts, with evidence for each saving.",
        "who_pays": "Platform teams and finance owners with a cloud bill they cannot explain.",
        "targets": ("cloud billing exports", "inventory APIs", "IaC repositories", "monitoring exports"),
        "probes": ("surface.scan", "manifest.read", "code.structure", "test.surface"),
        "surfaces": ("config", "source", "policy"),
        "missions": (
            "Map resource -> owner -> workload -> cost from exports and IaC.",
            "Flag idle or duplicated resources with a utilisation window.",
            "Downsize one resource and record the before/after bill.",
        ),
        "risks": ("production disruption", "billing lag", "ownership ambiguity"),
        "role": "systems",
    },
    {
        "id": "legacy-modernizer",
        "name": "Legacy Software Modernizer",
        "money": 5,
        "difficulty": 3,
        "summary": "Turn an old application into documentation, API maps, data structures and a modern replacement architecture.",
        "who_pays": "Enterprises maintaining unsupported line-of-business software.",
        "targets": ("legacy source", "binaries", "database schemas", "operational docs"),
        "probes": ("source.github", "manifest.read", "code.structure", "git.history", "binary.header", "docs.mine"),
        "surfaces": ("source", "config", "dependency", "verification"),
        "missions": (
            "Recover the entry points and business rules.",
            "Map data structures and external contracts.",
            "Deliver a parity-tested replacement slice.",
        ),
        "risks": ("licence", "hidden business rules", "no baseline"),
        "role": "modernization",
    },
)

RANGE = (
    {
        "n": "01",
        "id": "wifi-internal",
        "title": "Internal WiFi — simple probes",
        "level": "Low",
        "authorisation": "You own the network or hold written permission from its owner.",
        "summary": "Start with the least invasive read of your own WiFi: adapter state, local addresses, gateway, DNS and the neighbour cache. The optional device sweep stays off until you explicitly tick it.",
        "allowed": (
            "Read SSID, local addresses, gateway, DNS and the neighbour cache (passive).",
            "Run the bounded ICMP sweep only on a network you own and only when the sweep box is ticked.",
            "Record the scope and authorisation note in the dossier.",
        ),
        "evidence": (
            "WiFi state and interface map",
            "Gateway and DNS",
            "Neighbour table; devices only after an explicit sweep",
        ),
        "stop": (
            "The network is shared or corporate and you lack written permission.",
            "You cannot name the network owner.",
            "The sweep box is unticked: stay passive.",
        ),
        "probes": ("net.local", "net.devices"),
        "crew": ("protocol", "systems"),
        "start": {"target": "wifi", "kind": "wifi", "sweep": False,
                  "scope": "Stage 1 — internal WiFi, own network"},
        "success": "A passive map of this host and network, with no packets sent; devices only after an explicit sweep.",
    },
    {
        "n": "02",
        "id": "permissive-hardware",
        "title": "Permissive hardware — owned lab device",
        "level": "Moderate",
        "authorisation": "You own the device or have written permission; check warranty, contract and a recovery path first.",
        "summary": "Inspect an artifact with permissive security: unsigned firmware, open configuration or a test unit. Static first; do not modify anything until the original image can be restored.",
        "allowed": (
            "Inventory files, strings, configuration and firmware containers statically.",
            "Read the documented update and recovery path.",
            "Use a lab unit you can reflash and replace.",
        ),
        "evidence": (
            "CPU, container and rootfs map",
            "Writable sections, overlay and configuration",
            "Update mechanism and recovery path",
        ),
        "stop": (
            "The bootloader is locked or the device is in production.",
            "You cannot restore the original image.",
            "The vendor contract forbids modification.",
        ),
        "probes": ("surface.scan", "manifest.read", "binary.header",
                   "binary.controls", "binary.strings", "device.firmware", "docs.mine"),
        "crew": ("embedded-re", "archaeologist"),
        "start": {"target": "", "kind": "file", "sweep": False,
                  "scope": "Stage 2 — permissive hardware, owned lab device"},
        "success": "A static evidence map plus a documented recovery path; still no modification.",
    },
    {
        "n": "03",
        "id": "hardened-hardware",
        "title": "Hardened hardware — signed and locked",
        "level": "High",
        "authorisation": "Written permission, an owned lab recovery path and a change window. Production devices are out of scope.",
        "summary": "Map the controls instead of defeating them: signature envelopes, secure boot, measured boot and rollback policy. A control that blocks you is a finding to document, not a challenge.",
        "allowed": (
            "Static analysis, signature metadata and boot-chain documentation.",
            "Compare signed versions and configuration.",
            "Document the control and the person who owns it.",
        ),
        "evidence": (
            "Signature envelope and trust chain",
            "Secure/measured boot and rollback policy",
            "Update and recovery constraints",
        ),
        "stop": (
            "Any step would bypass a security control.",
            "There is no recovery path or owner sign-off.",
            "The device protects third-party data you do not own.",
        ),
        "probes": ("binary.header", "binary.controls", "binary.strings",
                   "device.firmware", "manifest.read"),
        "crew": ("embedded-re", "security-re", "architect"),
        "start": {"target": "", "kind": "file", "sweep": False,
                  "scope": "Stage 3 — hardened hardware, authorised lab only"},
        "success": "A control map with evidence and a safe recommendation; no bypass and no modification.",
    },
    {
        "n": "04",
        "id": "encrypted-systems",
        "title": "Encrypted systems — metadata, not plaintext",
        "level": "Research",
        "authorisation": "Written permission for the system and data; use owned captures or authorised test keys only.",
        "summary": "Understand crypto boundaries without extracting keys or decrypting third-party data: TLS chains, pinning, encrypted containers, key ownership and rotation.",
        "allowed": (
            "Read certificate chains, headers, capture metadata and documentation.",
            "Analyse ciphertext and container structure without decrypting it.",
            "Use synthetic data or test keys that were issued to you.",
        ),
        "evidence": (
            "TLS certificate and pinning map",
            "Container headers and key-ownership map",
            "Rotation, recovery and failure modes",
        ),
        "stop": (
            "A step needs a key you do not own.",
            "Decryption would expose third-party data.",
            "The only path is bypass, extraction or stealth.",
        ),
        "probes": ("traffic.har",),
        "crew": ("security-re", "systems", "architect"),
        "start": {"target": "", "kind": "har", "sweep": False,
                  "scope": "Stage 4 — encrypted system, authorised capture only"},
        "success": "A crypto-boundary and key-ownership map that needs no plaintext.",
    },
)


def _stars(value: int) -> str:
    value = max(0, min(5, int(value)))
    return "★" * value + "☆" * (5 - value)


def _probe(probe_id: str) -> dict:
    try:
        import engine
        meta = engine.PROBES.get(probe_id)
        if meta:
            return {"id": probe_id, "label": meta["label"], "family": meta["family"],
                    "description": meta["description"]}
    except Exception:  # noqa: BLE001 - the label is cosmetic
        pass
    return {"id": probe_id, "label": probe_id.replace(".", " ").title(),
            "family": "Probe", "description": ""}


def _profession(profession_id: str) -> dict | None:
    for profession in PROFESSIONS:
        if profession["id"] == profession_id:
            return profession
    return None


def profession_prompt(profession: dict) -> str:
    questions = "\n".join(f"- {q}" for q in profession["questions"])
    deliverables = ", ".join(profession["deliverables"])
    return (
        f"You are the {profession['name']} on an authorised REA investigation.\n\n"
        f"Focus: {profession['focus']}\n\n"
        f"Questions you must answer:\n{questions}\n\n"
        f"Deliver: {deliverables}.\n\n"
        "Rules: passive observation only; every claim has a locator and confidence; no credential "
        "storage or replay; dead ends become the next probes. State what you did not observe.\n"
    )


def guess_kind(target: str) -> str:
    value = str(target or "").strip()
    if not value:
        return "auto"
    if re.match(r"^https?://", value, re.I):
        return "repo" if re.search(r"github\.com/[\w.\-]+/[\w.\-]+", value, re.I) else "website"
    if value.lower().endswith(".har"):
        return "har"
    name = os.path.basename(value.rstrip("/\\"))
    if "." in name and not value.endswith(("/", "\\")):
        return "file"
    return "folder"


def _custom_idea(goal: str) -> dict:
    low = goal.lower()
    probes = ["surface.scan", "manifest.read", "docs.mine"]
    surfaces = ["config", "protocol"]
    role = "reverse-engineer"
    if any(word in low for word in ("web", "site", "http", "api", "saas", "stream")):
        probes = ["web.fetch", "web.headers", "web.stack", "web.endpoints", "traffic.har"]
        surfaces = ["protocol", "content", "policy"]
        role = "protocol"
    if any(word in low for word in ("app", "apk", "exe", "binary", "dll", "firmware", "device", "tv", "router", "car")):
        probes = ["binary.header", "binary.strings", "manifest.read", "device.firmware", "api.surface"]
        surfaces = ["binary", "firmware", "protocol", "config"]
        role = "embedded-re"
    if any(word in low for word in ("repo", "source", "code", "legacy", "github")):
        probes = ["source.github", "manifest.read", "code.structure", "git.history", "test.surface"]
        surfaces = ["source", "dependency", "config", "verification"]
        role = "archaeologist"
    if any(word in low for word in ("server", "host", "cloud", "port", "service", "infrastructure")):
        probes = ["surface.scan", "manifest.read", "code.structure", "api.surface", "test.surface"]
        surfaces = ["config", "source", "protocol", "policy"]
        role = "systems"
    if any(word in low for word in ("workflow", "process", "team", "employee", "manual", "admin")):
        probes = ["traffic.har", "docs.mine", "code.structure", "test.surface"]
        surfaces = ["protocol", "config", "content"]
        role = "product"
    title = (goal.strip().split(".")[0][:70] if goal.strip() else "Custom investigation")
    return {
        "id": "custom",
        "name": title or "Custom investigation",
        "money": 3,
        "difficulty": 3,
        "summary": goal.strip() or "A target you are authorised to analyse, with the technique chosen from the goal.",
        "who_pays": "the owner of the target or the team that depends on it",
        "targets": ("the authorised target",),
        "probes": tuple(dict.fromkeys(probes)),
        "surfaces": tuple(surfaces),
        "missions": (
            "Confirm the decision this investigation must support.",
            "Probe until one access mode is evidenced.",
            "Prove one reversible change and record the result.",
        ),
        "risks": ("scope creep", "unverified claims", "authorisation boundary"),
        "role": role,
    }


def generate_brief(idea_id: str | None = None, target: str = "", goal: str = "",
                   variant: int = 0, angle: str = "") -> dict:
    idea = None
    if idea_id and idea_id != "custom":
        idea = next((item for item in IDEAS if item["id"] == idea_id), None)
    custom = idea is None
    if idea is None:
        idea = _custom_idea(goal)
    chosen = angle if angle in ANGLES else ANGLES[int(variant or 0) % len(ANGLES)]
    profession = _profession(idea.get("role") or "") or PROFESSIONS[0]
    probes = [_probe(pid) for pid in idea["probes"]]
    label = target.strip() or "<target>"
    scope = goal.strip() or f"establish the evidence needed for {idea['name'].lower()}"
    phases = [
        {
            "title": "Frame the target",
            "actions": [
                f"Record authorisation and a scope note for {label}.",
                f"State the decision this brief supports: {scope}.",
                f"Choose the target kind: {guess_kind(target)}.",
            ],
        },
        {
            "title": "Probe the surface",
            "actions": [f"{probe['family']} · {probe['label']} — {probe['description']}".rstrip(" —")
                        for probe in probes],
        },
        {
            "title": "Connect the evidence",
            "actions": [
                "Build the graph: target, assets, technology, endpoints, sources, edit points.",
                "Mark every empty probe with the technique that replaced it.",
                f"Name the available access modes: {', '.join(idea['surfaces'])}.",
            ],
        },
        {
            "title": f"Prove one reversible change · {chosen}",
            "actions": [
                "Capture a baseline of current behaviour before touching anything.",
                "Make the smallest change that tests the thesis, behind a flag or on a copy.",
                "Record before/after evidence; stop if the licence or authorisation boundary is unclear.",
            ],
        },
        {
            "title": "Deliver",
            "actions": [
                "Publish the Markdown dossier with evidence, risks and confidence.",
                "Recommend emulate, port or rewrite — with the failure mode of the chosen option.",
                "List the next three probes and the owner of each.",
            ],
        },
    ]
    questions = [
        f"Who is the user and what painful job does {idea['name'].lower()} remove?",
        f"What evidence would prove the thesis: {idea['summary']}",
        f"Which access mode gives the lowest-risk path: {', '.join(idea['surfaces'])}?",
        "What would make us stop — licence, safety, privacy or cost?",
    ]
    deliverables = [
        "Evidence table with locator and confidence",
        "Access-mode decision and edit-point map with risk",
        "One verified change or a named blocker",
        "Markdown dossier plus next three probes",
    ]
    session_prompt = (
        f"Act as the {profession['name']} on an authorised REA investigation.\n\n"
        f"Target: {label}\nScope: {scope}\nAngle: {chosen}\n\n"
        f"Thesis: {idea['summary']}\n\n"
        "Use these probes, in this order:\n"
        + "\n".join(f"- {probe['label']} ({probe['family']}): {probe['description']}" for probe in probes)
        + "\n\nRules: passive observation only; no auth bypass or exploitation; no credential storage or "
          "replay; a dead end becomes the next technique, not a failure. Every claim needs a locator and "
          "a confidence.\n\n"
          "Deliver: the four dossier deliverables, then one recommended reversible change and its "
          "verification step."
    )
    review_prompt = (
        "You are independent Red Team. Review this REA brief and its eventual dossier.\n"
        f"Target: {label}. Thesis: {idea['summary']}.\n"
        "Challenge the evidence, the licence boundary, the privacy impact and every risk label. "
        "Return unsupported claims, missing evidence, and three checks to run before anyone acts. "
        "Verdict: accept / accept with caveats / reject."
    )
    return {
        "id": idea["id"],
        "title": idea["name"],
        "custom": custom,
        "angle": chosen,
        "target": target.strip(),
        "goal": goal.strip(),
        "thesis": idea["summary"],
        "who_pays": idea["who_pays"],
        "probes": probes,
        "surfaces": list(idea["surfaces"]),
        "phases": phases,
        "questions": questions,
        "deliverables": deliverables,
        "risks": list(idea["risks"]),
        "missions": list(idea["missions"]),
        "role": {"id": profession["id"], "name": profession["name"], "focus": profession["focus"]},
        "prompts": [
            {"title": "Investigation brief", "text": session_prompt},
            {"title": "Dossier review", "text": review_prompt},
        ],
        "start": {
            "target": target.strip(),
            "kind": guess_kind(target),
            "scope": f"{idea['name']} · {chosen} · {scope}"[:280],
        },
    }


def payload() -> dict:
    return {
        "instructions": [dict(item) for item in INSTRUCTIONS],
        "guides": [dict(item) for item in GUIDES],
        "prompts": [dict(item) for item in PROMPTS],
        "professions": [
            {**profession, "prompt": profession_prompt(profession)}
            for profession in PROFESSIONS
        ],
        "ideas": [
            {**idea, "stars_money": _stars(idea["money"]),
             "stars_difficulty": _stars(idea["difficulty"])}
            for idea in IDEAS
        ],
        "range": [
            {
                **stage,
                "probes": [_probe(pid) for pid in stage["probes"]],
                "crew": [
                    {"id": person["id"], "name": person["name"]}
                    for person in (_profession(cid) for cid in stage["crew"])
                    if person
                ],
            }
            for stage in RANGE
        ],
        "angles": list(ANGLES),
        "counts": {
            "instructions": len(INSTRUCTIONS),
            "guides": len(GUIDES),
            "prompts": len(PROMPTS),
            "professions": len(PROFESSIONS),
            "ideas": len(IDEAS),
            "range": len(RANGE),
        },
    }


if __name__ == "__main__":
    import json
    print(json.dumps(generate_brief("ai-code-archaeologist", "<repo>", variant=0), indent=1))
