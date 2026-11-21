#!/usr/bin/env python3
"""REA server — local console for the investigation engine.

Serves only ``rea/viewer`` to the browser and exposes the dossier API:

    GET  /api/health
    GET  /api/probes                     probe catalogue and ladders
    GET  /api/playbook                   instructions, guides, prompts, ideas, crew
    POST /api/plan                       build a read-only autonomy route
    GET  /api/revenue/models             productised revenue routes
    POST /api/revenue/scan               live public demand scan
    POST /api/revenue/offer              package a sellable offer
    GET  /api/media                      channel kit, stack and donation copy
    POST /api/media/video                package one video
    GET  /api/newsletter                 public field-note archive
    POST /api/newsletter/subscribe       opt in to the local field-note list
    GET  /mailroom/                      owner-only contact and issue console
    GET  /api/mailroom                   owner-only list, issues and stats
    GET  /api/mailroom/export.csv        owner-only contact export
    POST /api/mailroom/contact           owner-only manual contact
    POST /api/mailroom/status            owner-only contact status
    POST /api/mailroom/issue             draft a monthly field note
    POST /api/mailroom/issue/status      mark an issue draft/ready/sent
    GET  /api/investigations             dossier summaries
    GET  /api/investigations/<id>        one dossier (poll while running)
    GET  /api/investigations/<id>/report.md
    POST /api/investigations             start one (requires authorisation)
    GET  /api/improvements               local plans and suggestions, loopback only
    GET  /api/improvements/<id>/brief.md
    POST /api/improvements/suggest       suggest improvements from a brief
    POST /api/improvements/suggestions/approve
    POST /api/improvements/suggestions/decline
    POST /api/improvements               draft one reversible change plan
    POST /api/improvements/status        record the next plan state
    POST /api/improvements/decide        crew recommendation, drafted plan and advice
    POST /api/improvements/execute       preview or apply a baselined patch (local git only)
    POST /api/improvements/rollback      reverse the applied patch

Everything is local: no account and no key. Public demand scans go only to
Hacker News search. Probe traffic goes only to targets the operator starts.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import autonomy
import decision
import engine
import executor
import improvements
import mailroom
import media
import playbook
import revenue
import studio

HERE = Path(__file__).resolve().parent
VIEWER = HERE / "viewer"
DEFAULT_PORT = 4711


class REAHandler(SimpleHTTPRequestHandler):
    server_version = "REA/1.0"
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(VIEWER), **kwargs)

    # -- helpers ------------------------------------------------------------
    def _json(self, payload, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _local_only(self) -> bool:
        return self.client_address[0] in {"127.0.0.1", "::1", "::ffff:127.0.0.1"}

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValueError("bad Content-Length")
        if length <= 0:
            return {}
        if length > 200_000:
            raise ValueError("body too large")
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(str(exc))
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        return data

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    # -- routes -------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - http.server API
        path = urlparse(self.path).path
        if path == "/api/health":
            return self._json({
                "ok": True, "app": "REA", "version": "1.0",
                "probes": len(engine.PROBES),
                "investigations": len(engine.list_investigations()),
            })
        if path == "/api/probes":
            return self._json({
                "probes": engine.probe_catalogue(),
                "ladders": engine.ORDER,
                "hints": engine.NEXT_HINTS,
            })
        if path == "/api/playbook":
            return self._json(playbook.payload())
        if path == "/api/revenue/models":
            return self._json(revenue.models_payload())
        if path == "/api/media":
            return self._json(media.payload())
        if path == "/api/newsletter":
            return self._json(mailroom.public_payload())
        if path in {"/mailroom", "/mailroom/"}:
            if not self._local_only():
                return self._json({"error": "the Mailroom is local-only"}, 403)
            return self._serve_viewer_file("mailroom.html")
        if path.startswith("/api/mailroom"):
            return self._mailroom_get(path)
        if path == "/api/studio":
            return self._json(studio.payload())
        if path == "/api/studio/publications":
            return self._json({"publications": studio.published()})
        if path.startswith("/api/improvements"):
            if not self._local_only():
                return self._json({"error": "Improve is local-only"}, 403)
            if path == "/api/improvements":
                return self._json(improvements.payload())
            match = re.fullmatch(r"/api/improvements/([A-Za-z0-9\-]+)/brief\.md", path)
            if not match:
                return self._json({"error": "not found"}, 404)
            plan = improvements.get(match.group(1))
            if not plan:
                return self._json({"error": "plan not found"}, 404)
            body = improvements.brief(plan).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Disposition",
                             f'attachment; filename="{plan["id"]}.md"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/investigations":
            items = [item for item in engine.list_investigations(200)
                     if not studio.is_fixture_target(item.get("target"))]
            return self._json({"investigations": items})
        match = re.fullmatch(r"/api/investigations/([A-Za-z0-9\-]+)(/report\.md)?", path)
        if match:
            dossier = engine.load(match.group(1))
            if dossier is None:
                return self._json({"error": "investigation not found"}, 404)
            if match.group(2):
                body = engine.report_markdown(dossier).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="{dossier["id"]}.md"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            return self._json(dossier)
        if path == "/published" or path.startswith("/published/"):
            return self._serve_published(path)
        return super().do_GET()

    def _serve_viewer_file(self, name: str) -> None:
        target = (VIEWER / name).resolve()
        if target != VIEWER.resolve() and VIEWER.resolve() not in target.parents:
            return self._json({"error": "not found"}, 404)
        if not target.is_file():
            return self._json({"error": "not found"}, 404)
        body = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _mailroom_get(self, path: str) -> None:
        if not self._local_only():
            return self._json({"error": "the Mailroom is local-only"}, 403)
        query = parse_qs(urlparse(self.path).query)
        if path == "/api/mailroom/export.csv":
            status = (query.get("status") or [""])[0]
            try:
                body = mailroom.export_csv(status=status).encode("utf-8")
            except ValueError as exc:
                return self._json({"error": str(exc)}, 500)
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="mailroom-contacts.csv"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in {"/api/mailroom", "/api/mailroom/"}:
            try:
                data = mailroom.payload(
                    query=(query.get("q") or [""])[0],
                    status=(query.get("status") or [""])[0],
                )
            except ValueError as exc:
                return self._json({"error": str(exc)}, 500)
            return self._json(data)
        match = re.fullmatch(r"/api/mailroom/issues/([A-Za-z0-9\-]+)\.md", path)
        if match:
            issue = mailroom.find_issue(match.group(1))
            if not issue:
                return self._json({"error": "issue not found"}, 404)
            body = mailroom.issue_markdown(issue).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{match.group(1)}.md"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return self._json({"error": "not found"}, 404)

    def _serve_published(self, path: str) -> None:
        rel = unquote(path[len("/published"):]).lstrip("/")
        if not rel or rel.endswith("/"):
            rel = (rel + "index.html").lstrip("/")
        root = studio.SITE_DIR.resolve()
        target = (root / rel).resolve()
        if target != root and root not in target.parents:
            return self._json({"error": "not found"}, 404)
        if not target.is_file():
            return self._json({"error": "not found"}, 404)
        body = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in {"application/json", "image/svg+xml"}:
            ctype += "; charset=utf-8"
        total = len(body)
        requested = (self.headers.get("Range") or "").strip()
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
        if match and total:
            first, last = match.groups()
            start = int(first) if first else 0
            end = int(last) if last else total - 1
            end = min(end, total - 1)
            if start > end or start >= total:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{total}")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            chunk = body[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(total))
        self.end_headers()
        self.wfile.write(body)

    def _newsletter_subscribe(self, body: dict) -> None:
        try:
            contact = mailroom.subscribe(
                body.get("email"),
                name=body.get("name") or "",
                topics=body.get("topics") or ("monthly",),
                source=body.get("source") or "field-notes",
                donated=body.get("donated") is True,
                consent=body.get("consent") is True,
            )
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        owner = media.CONTACT_EMAIL
        subject = quote("Field notes request")
        mail_body = quote(
            "Please add me to the monthly field notes.\n\n"
            f"Email: {body.get('email') or ''}\nName: {body.get('name') or ''}\n"
        )
        return self._json({
            "ok": True,
            "contact": contact,
            "owner_email": owner,
            "mailto": f"mailto:{owner}?subject={subject}&body={mail_body}",
            "message": (
                "Request saved to the local Mailroom. No email was sent automatically. "
                "The owner can review the list and send a copy from the configured address."
            ),
        }, 201)

    def _mailroom_post(self, path: str, body: dict) -> None:
        if not self._local_only():
            return self._json({"error": "the Mailroom is local-only"}, 403)
        try:
            if path == "/api/mailroom/contact":
                contact = mailroom.add_contact(
                    body.get("email"),
                    name=body.get("name") or "",
                    topics=body.get("topics") or ("monthly",),
                    source=body.get("source") or "manual",
                    donated=body.get("donated") is True,
                    note=body.get("note") or "",
                )
                return self._json({"contact": contact}, 201)
            if path == "/api/mailroom/status":
                contact = mailroom.set_status(
                    body.get("id"), body.get("status"), note=body.get("note") or "")
                return self._json({"contact": contact})
            if path == "/api/mailroom/issue":
                publication, facts = studio.monthly_material()
                issue = mailroom.draft_issue(
                    body.get("month"), body.get("title") or "", body.get("intro") or "",
                    publication=publication, facts=facts)
                return self._json({"issue": issue}, 201)
            if path == "/api/mailroom/issue/status":
                issue = mailroom.set_issue_status(body.get("id"), body.get("status"))
                return self._json({"issue": issue})
        except KeyError as exc:
            return self._json({"error": str(exc).strip("'")}, 404)
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        return self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        path = urlparse(self.path).path
        try:
            body = self._read_json()
        except ValueError as exc:
            return self._json({"error": f"bad request: {exc}"}, 400)
        if path == "/api/newsletter/subscribe":
            return self._newsletter_subscribe(body)
        if path.startswith("/api/mailroom"):
            return self._mailroom_post(path, body)
        if path.startswith("/api/improvements"):
            if not self._local_only():
                return self._json({"error": "Improve is local-only"}, 403)
            try:
                if path == "/api/improvements/suggest":
                    result = improvements.suggest(
                        body.get("dossier"), body.get("goal"),
                        body.get("system_class"), limit=body.get("limit") or 4)
                    return self._json(result)
                if path == "/api/improvements/suggestions/approve":
                    plan, suggestion = improvements.approve_suggestion(
                        body.get("id"), body.get("owner") or "",
                        goal=body.get("goal") or "",
                        change=body.get("change") or "",
                        baseline=body.get("baseline") or "",
                        verification=body.get("verification") or "",
                        rollback=body.get("rollback") or "",
                        patch=body.get("patch") or "",
                        verify_command=body.get("verify_command") or "",
                    )
                    return self._json({"plan": plan, "suggestion": suggestion}, 201)
                if path == "/api/improvements/suggestions/decline":
                    suggestion = improvements.decline_suggestion(body.get("id"))
                    return self._json({"suggestion": suggestion})
                if path == "/api/improvements":
                    plan = improvements.create(
                        body.get("dossier"), body.get("seam"),
                        body.get("goal"), body.get("change"),
                        body.get("baseline"), body.get("verification"),
                        body.get("rollback"), body.get("patch") or "",
                        body.get("owner") or "",
                        body.get("system_class") or "",
                        body.get("intervention") or "",
                        verify_command=body.get("verify_command") or "",
                    )
                    return self._json({"plan": plan}, 201)
                if path == "/api/improvements/decide":
                    result = decision.decide(
                        body.get("dossier"), body.get("goal"),
                        body.get("system_class"), owner=body.get("owner") or "")
                    return self._json(result, 201 if result.get("plan") else 200)
                if path == "/api/improvements/execute":
                    plan = improvements.get(body.get("id"))
                    if plan is None:
                        raise KeyError("plan not found")
                    if body.get("dry_run") is True:
                        return self._json({"preview": executor.preview(plan)})
                    if body.get("confirm") is not True:
                        return self._json({"error": "confirm the apply first"}, 400)
                    return self._json({"plan": executor.apply(body.get("id"))})
                if path == "/api/improvements/rollback":
                    return self._json({"plan": executor.rollback(body.get("id"))})
                if path == "/api/improvements/status":
                    plan = improvements.set_status(
                        body.get("id"), body.get("status"), note=body.get("note") or "")
                    return self._json({"plan": plan})
            except KeyError as exc:
                return self._json({"error": str(exc).strip("'")}, 404)
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            return self._json({"error": "not found"}, 404)
        if path == "/api/revenue/scan":
            try:
                result = revenue.scan_demand(
                    str(body.get("capability") or ""),
                    buyer=str(body.get("buyer") or ""),
                    limit=body.get("limit") or 8,
                    days=body.get("days") or 365,
                )
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            except RuntimeError as exc:
                return self._json({"error": str(exc)}, 502)
            return self._json(result)
        if path == "/api/revenue/offer":
            try:
                result = revenue.build_offer(
                    str(body.get("capability") or ""),
                    buyer=str(body.get("buyer") or ""),
                    model_id=str(body.get("model") or "automation-sprint"),
                    price=str(body.get("price") or ""),
                    proof=str(body.get("proof") or ""),
                )
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            return self._json(result)
        if path == "/api/studio/publish":
            inv = engine.load(str(body.get("investigation") or ""))
            if not inv:
                return self._json({"error": "investigation not found"}, 404)
            try:
                publication = studio.build_publication(
                    inv, title=str(body.get("title") or ""))
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            return self._json({"publication": publication})
        if path == "/api/ideas/generate":
            try:
                variant = int(body.get("variant") or 0)
            except (TypeError, ValueError):
                variant = 0
            brief = playbook.generate_brief(
                str(body.get("idea_id") or ""),
                str(body.get("target") or ""),
                str(body.get("goal") or ""),
                variant=variant,
                angle=str(body.get("angle") or ""),
            )
            return self._json({"brief": brief})
        if path == "/api/plan":
            kind = body.get("kind")
            if kind in {"", "auto", None}:
                kind = None
            try:
                plan = autonomy.build_plan(
                    str(body.get("target") or ""),
                    kind=kind,
                    stage_id=str(body.get("stage") or ""),
                    depth=str(body.get("depth") or "standard"),
                    network=bool(body.get("network", True)),
                    sweep=bool(body.get("sweep", False)),
                )
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            return self._json({"plan": plan, "text": autonomy.plan_text(plan)})
        if path != "/api/investigations":
            return self._json({"error": "not found"}, 404)
        kind = body.get("kind")
        if kind in {"", "auto", None}:
            kind = None
        try:
            dossier = engine.new_investigation(
                str(body.get("target") or ""),
                body.get("authorized") is True,
                str(body.get("scope") or ""),
                kind,
                bool(body.get("network", True)),
                bool(body.get("sweep", True)),
                str(body.get("stage") or ""),
                str(body.get("depth") or "standard"),
            )
        except PermissionError as exc:
            return self._json({"error": str(exc)}, 403)
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        engine.save(dossier)
        threading.Thread(target=_run, args=(dossier["id"],), daemon=True).start()
        return self._json({"investigation": dossier}, 202)


def _run(dossier_id: str) -> None:
    dossier = engine.load(dossier_id)
    if not dossier:
        return
    try:
        engine.run_investigation(
            dossier,
            network=bool(dossier.get("network", True)),
            progress=engine.save,
        )
    except Exception as exc:  # noqa: BLE001 - surface the failure in the dossier
        current = engine.load(dossier_id) or dossier
        current["status"] = "failed"
        current["error"] = f"{type(exc).__name__}: {exc}"
        current["finished"] = engine.now_iso()
        engine.save(current)


def make_server(port: int = DEFAULT_PORT, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, int(port)), REAHandler)
    server.daemon_threads = True
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="REA local console")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    server = make_server(args.port, args.host)
    url = f"http://{args.host}:{server.server_address[1]}/"
    print(f"REA console: {url}")
    print("Authorised targets only. Demand scans read public Hacker News. Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping REA.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
