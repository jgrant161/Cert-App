"""Web front end: sign-in, engagements, uploads, the review workspace, users, and exports.

Every route that touches client data goes through ``need()``, which checks
that someone is signed in, that they may see this client, and that their
role allows the action. Clients they may not see answer 404, so the app does
not reveal which clients exist.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import threading
from datetime import date
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from .. import assistant, auth, db, microsoft
from ..export import build_workbook
from ..extraction import ai_available, default_extractor
from ..ingest import ingest
from ..pipeline import process_engagement
from ..rules import POLICY_LABELS, Policy, score_engagement
from ..states import STATES

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.globals["STATES"] = STATES
templates.env.globals["ai_available"] = ai_available
templates.env.globals["ROLES"] = auth.ROLES
templates.env.globals["can"] = auth.can


def production() -> bool:
    return os.environ.get("CERTAPP_ENV", "").lower() == "production"


def _secret_key() -> str:
    key = os.environ.get("CERTAPP_SECRET_KEY")
    if key:
        return key
    if production():
        raise RuntimeError("Set CERTAPP_SECRET_KEY before running in production.")
    path = db.data_dir() / "secret_key"   # local installs: generate once and keep
    if not path.exists():
        path.write_text(secrets.token_urlsafe(48))
    return path.read_text().strip()


class SignInRequired(Exception):
    pass


class Forbidden(Exception):
    pass


def create_app(extractor=None, ask_fn=None, microsoft_login=None) -> FastAPI:
    db.init()
    db.reset_interrupted()
    app = FastAPI(title="Certificate Review", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.extractor = extractor or default_extractor()
    app.state.ask = ask_fn or assistant.ask
    app.state.microsoft = microsoft_login or (microsoft.MicrosoftLogin() if microsoft.configured() else None)
    throttle = auth.LoginThrottle()
    running: set[int] = set()          # engagements with a read in progress
    running_lock = threading.Lock()

    def read_pending(eid: int) -> None:
        with running_lock:
            if eid in running:
                return
            running.add(eid)
        try:
            # Loop so files uploaded while a read was under way are picked up too.
            while process_engagement(eid, app.state.extractor):
                pass
        finally:
            with running_lock:
                running.discard(eid)

    # --- middleware --------------------------------------------------------

    @app.middleware("http")
    async def security(request: Request, call_next):
        # Refuse form posts that come from another site (cross-site request forgery).
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            source = request.headers.get("origin") or request.headers.get("referer")
            if source and urlsplit(source).netloc != request.headers.get("host"):
                return Response("Request refused: it did not come from this site.", status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; frame-ancestors 'self'; object-src 'none'; base-uri 'self'; form-action 'self'")
        if production():
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    app.add_middleware(SessionMiddleware, secret_key=_secret_key(), session_cookie="certapp_session",
                       max_age=12 * 60 * 60, same_site="lax", https_only=production())
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.exception_handler(SignInRequired)
    async def _sign_in(request: Request, exc: SignInRequired):
        if db.count_users() == 0:
            return RedirectResponse("/setup", status_code=303)
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/login?next={quote(target)}", status_code=303)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            return render(request, "message.html", {
                "title": "Page not found",
                "message": "It may have been deleted, or your account may not have access to it."}, 404)
        return Response(str(exc.detail), status_code=exc.status_code)

    @app.exception_handler(Forbidden)
    async def _forbidden(request: Request, exc: Forbidden):
        return render(request, "message.html", {
            "title": "Not available to your account",
            "message": "Your role does not allow this. Ask a firm administrator if you need access."},
            status_code=403)

    # --- helpers -----------------------------------------------------------

    def render(request: Request, name: str, ctx: dict, status_code: int = 200):
        user = auth.current_user(request.session) if "uid" in request.session else None
        ctx = {"user": user, "ms_enabled": app.state.microsoft is not None, **ctx}
        return templates.TemplateResponse(request, name, ctx, status_code=status_code)

    def signed_in(request: Request) -> dict:
        user = auth.current_user(request.session)
        if not user:
            raise SignInRequired()
        return user

    def need(user: dict, eid: int, action: str = "view") -> dict:
        e = db.get_engagement(eid)
        if not e or not auth.can_access_engagement(user, eid):
            raise HTTPException(404, "Not found")
        if not auth.can(user, action):
            raise Forbidden()
        return e

    def cert_in(eid: int, cid: int) -> dict:
        c = db.get_certificate(cid)
        if not c or c["engagement_id"] != eid:
            raise HTTPException(404, "Not found")
        return c

    def score(e: dict):
        policy = Policy.from_json(e["policy_json"])
        certs = db.list_certificates(e["id"])
        return policy, score_engagement(certs, policy, db.overrides_for_engagement(e["id"]))

    def back(url: str) -> RedirectResponse:
        return RedirectResponse(url, status_code=303)

    def safe_next(target: str | None) -> str:
        return target if target and target.startswith("/") and not target.startswith("//") else "/"

    # --- health ------------------------------------------------------------

    @app.get("/healthz")
    def healthz():
        try:
            db.ping()
        except Exception:
            return JSONResponse({"ok": False}, status_code=503)
        return {"ok": True}

    # --- first-run setup ---------------------------------------------------

    @app.get("/setup")
    def setup_form(request: Request):
        if db.count_users():
            return back("/login")
        return render(request, "setup.html", {"need_token": production(), "error": None})

    @app.post("/setup")
    def setup(request: Request, name: str = Form(...), email: str = Form(...),
              password: str = Form(""), confirm: str = Form(""), method: str = Form("password"),
              setup_token: str = Form("")):
        if db.count_users():
            return back("/login")

        def again(error):
            return render(request, "setup.html", {"need_token": production(), "error": error}, 400)

        if production() and not secrets.compare_digest(setup_token, os.environ.get("CERTAPP_SETUP_TOKEN", "") or secrets.token_hex()):
            return again("The setup code is not correct.")
        if method == "microsoft":
            if app.state.microsoft is None:
                return again("Microsoft sign-in is not configured on this server yet.")
            uid = db.create_user(email, name, "firm_admin", "microsoft")
        else:
            problem = auth.password_problem(password, confirm)
            if problem:
                return again(problem)
            uid = db.create_user(email, name, "firm_admin", "password", password_hash=auth.hash_password(password))
        user = db.get_user(uid)
        db.audit(user, "setup", detail="First administrator created")
        if method == "microsoft":
            return back("/login")
        auth.start_session(request.session, user)
        return back("/")

    # --- sign in / out -----------------------------------------------------

    @app.get("/login")
    def login_form(request: Request, next: str = "/", error: str = ""):
        if db.count_users() == 0:
            return back("/setup")
        return render(request, "login.html", {"next": safe_next(next), "error": error, "email": ""})

    @app.post("/login")
    def login(request: Request, email: str = Form(...), password: str = Form(...), next: str = Form("/")):
        user, error = auth.authenticate(email, password, throttle)
        if not user:
            db.audit(None, "sign_in_failed", detail=email.strip().lower()[:200])
            return render(request, "login.html", {"next": safe_next(next), "error": error, "email": email}, 400)
        auth.start_session(request.session, user)
        db.audit(user, "sign_in", detail="password")
        return back(safe_next(next))

    @app.post("/logout")
    def logout(request: Request):
        user = auth.current_user(request.session)
        if user:
            db.audit(user, "sign_out")
        request.session.clear()
        return back("/login")

    @app.get("/auth/microsoft")
    def ms_start(request: Request, next: str = "/"):
        if app.state.microsoft is None:
            return back("/login?error=" + quote("Microsoft sign-in is not configured."))
        base = os.environ.get("CERTAPP_BASE_URL", "").rstrip("/")
        redirect_uri = f"{base}/auth/microsoft/callback" if base else str(request.url_for("ms_callback"))
        flow = app.state.microsoft.start(redirect_uri)
        request.session["ms_flow"] = flow
        request.session["ms_next"] = safe_next(next)
        return RedirectResponse(flow["auth_uri"], status_code=303)

    @app.get("/auth/microsoft/callback", name="ms_callback")
    def ms_callback(request: Request):
        flow = request.session.pop("ms_flow", None)
        target = request.session.pop("ms_next", "/")
        if not flow or app.state.microsoft is None:
            return back("/login?error=" + quote("The Microsoft sign-in expired. Please try again."))
        email, error = app.state.microsoft.finish(flow, dict(request.query_params))
        if error:
            return back("/login?error=" + quote(error))
        user = db.get_user_by_email(email)
        if not user or not user["active"] or user["auth_method"] != "microsoft":
            db.audit(None, "sign_in_failed", detail=f"microsoft: {email}")
            return back("/login?error=" + quote(
                f"{email} is not set up for this app. Ask a firm administrator to add you."))
        auth.start_session(request.session, user)
        db.audit(user, "sign_in", detail="microsoft")
        return back(safe_next(target))

    # --- invitations and account -------------------------------------------

    @app.get("/invite/{token}")
    def invite_form(request: Request, token: str):
        invite, user = auth.check_invite_token(token)
        if not invite:
            return render(request, "message.html", {
                "title": "This link has expired or was already used",
                "message": "Ask whoever invited you to send a new link."}, 404)
        return render(request, "invite.html", {"invitee": user, "error": None})

    @app.post("/invite/{token}")
    def invite_accept(request: Request, token: str, password: str = Form(...), confirm: str = Form(...)):
        invite, user = auth.check_invite_token(token)
        if not invite:
            return back(f"/invite/{token}")
        problem = auth.password_problem(password, confirm)
        if problem:
            return render(request, "invite.html", {"invitee": user, "error": problem}, 400)
        db.update_user(user["id"], password_hash=auth.hash_password(password), auth_method="password")
        db.bump_session_version(user["id"])
        db.use_invite(invite["id"])
        user = db.get_user(user["id"])
        db.audit(user, "password_set", user.get("engagement_id"))
        auth.start_session(request.session, user)
        return back("/")

    @app.get("/account")
    def account(request: Request, saved: str = "", user: dict = Depends(signed_in)):
        return render(request, "account.html", {"error": None, "saved": saved})

    @app.post("/account/password")
    def change_password(request: Request, current: str = Form(...), password: str = Form(...),
                        confirm: str = Form(...), user: dict = Depends(signed_in)):
        if user["auth_method"] != "password":
            raise Forbidden()
        error = None if auth.verify_password(current, user["password_hash"]) else "Your current password is not correct."
        error = error or auth.password_problem(password, confirm)
        if error:
            return render(request, "account.html", {"error": error, "saved": ""}, 400)
        db.update_user(user["id"], password_hash=auth.hash_password(password))
        db.bump_session_version(user["id"])          # signs out other devices
        db.audit(user, "password_changed")
        auth.start_session(request.session, db.get_user(user["id"]))
        return back("/account?saved=1")

    # --- users -------------------------------------------------------------

    def manageable_users(actor: dict) -> list[dict]:
        if auth.can(actor, "manage_users"):
            return db.list_users()
        if auth.can(actor, "manage_client_users"):
            return db.list_users(actor["engagement_id"])
        raise Forbidden()

    def user_or_404(actor: dict, uid: int) -> dict:
        target = db.get_user(uid)
        if not target or not auth.can_manage_user(actor, target):
            raise HTTPException(404, "Not found")
        return target

    def invite_url(request: Request, token: str) -> str:
        base = os.environ.get("CERTAPP_BASE_URL", "").rstrip("/") or str(request.base_url).rstrip("/")
        return f"{base}/invite/{token}"

    @app.get("/users")
    def users(request: Request, user: dict = Depends(signed_in)):
        return render(request, "users.html", {"users": manageable_users(user)})

    def user_form_ctx(actor: dict, target: dict | None, error: str | None = None, link: str | None = None):
        engagements = db.list_engagements(auth.accessible_engagement_ids(actor))
        return {"target": target, "roles": auth.assignable_roles(actor), "engagements": engagements,
                "assigned": set(db.staff_engagement_ids(target["id"])) if target else set(),
                "error": error, "link": link}

    @app.get("/users/new")
    def user_new_form(request: Request, user: dict = Depends(signed_in)):
        manageable_users(user)
        return render(request, "user_form.html", user_form_ctx(user, None))

    @app.post("/users/new")
    async def user_create(request: Request, user: dict = Depends(signed_in)):
        manageable_users(user)
        form = await request.form()
        name, email = (form.get("name") or "").strip(), (form.get("email") or "").strip().lower()
        role, method = form.get("role", ""), form.get("auth_method", "password")
        engagement_id = int(form["engagement_id"]) if form.get("engagement_id") else None
        error = None
        if role not in auth.assignable_roles(user):
            error = "Choose a role."
        elif not name or "@" not in email:
            error = "Enter a name and a valid email address."
        elif db.get_user_by_email(email):
            error = "A user with that email already exists."
        elif role in auth.CLIENT_ROLES:
            if not auth.can(user, "manage_users"):
                engagement_id = user["engagement_id"]          # client admins add to their own company
            if not engagement_id or not auth.can_access_engagement(user, engagement_id):
                error = "Choose which client this user belongs to."
            method = "password"                                # clients use invitation links
        elif method == "microsoft" and app.state.microsoft is None:
            error = "Microsoft sign-in is not configured yet. Choose an invitation link instead."
        if error:
            return render(request, "user_form.html", user_form_ctx(user, None, error), 400)
        uid = db.create_user(email, name, role, method,
                             engagement_id=engagement_id if role in auth.CLIENT_ROLES else None)
        if role == "firm_staff":
            db.set_staff_engagements(uid, [int(x) for x in form.getlist("staff_engagements")])
        db.audit(user, "user_created", engagement_id, f"{email} as {auth.ROLES[role]}")
        link = invite_url(request, auth.create_invite_token(uid)) if method == "password" else None
        return render(request, "user_form.html", user_form_ctx(user, db.get_user(uid), link=link))

    @app.get("/users/{uid}")
    def user_edit_form(request: Request, uid: int, user: dict = Depends(signed_in)):
        target = user_or_404(user, uid)
        return render(request, "user_form.html", user_form_ctx(user, target))

    @app.post("/users/{uid}")
    async def user_update(request: Request, uid: int, user: dict = Depends(signed_in)):
        target = user_or_404(user, uid)
        form = await request.form()
        role = form.get("role", target["role"])
        if role not in auth.assignable_roles(user):
            raise Forbidden()
        active = 1 if form.get("active") == "on" else 0
        if target["id"] == user["id"] and (not active or role != user["role"]):
            return render(request, "user_form.html",
                          user_form_ctx(user, target, "You cannot deactivate yourself or change your own role."), 400)
        fields = {"name": (form.get("name") or target["name"]).strip(), "role": role, "active": active}
        if role in auth.CLIENT_ROLES and auth.can(user, "manage_users") and form.get("engagement_id"):
            fields["engagement_id"] = int(form["engagement_id"])
        if role in auth.FIRM_ROLES:
            fields["engagement_id"] = None
        elif not fields.get("engagement_id", target.get("engagement_id")):
            return render(request, "user_form.html",
                          user_form_ctx(user, target, "Choose which client this user belongs to."), 400)
        db.update_user(uid, **fields)
        if role == "firm_staff" and auth.can(user, "manage_users"):
            db.set_staff_engagements(uid, [int(x) for x in form.getlist("staff_engagements")])
        if not active or role != target["role"]:
            db.bump_session_version(uid)                      # takes effect immediately
        db.audit(user, "user_updated", fields.get("engagement_id") or target.get("engagement_id"),
                 f"{target['email']}: role={role}, active={bool(active)}")
        return back(f"/users/{uid}")

    @app.post("/users/{uid}/invite")
    def user_new_link(request: Request, uid: int, user: dict = Depends(signed_in)):
        target = user_or_404(user, uid)
        if target["auth_method"] == "microsoft":
            raise Forbidden()
        link = invite_url(request, auth.create_invite_token(uid))
        db.audit(user, "invite_link_created", target.get("engagement_id"), target["email"])
        return render(request, "user_form.html", user_form_ctx(user, target, link=link))

    @app.post("/users/{uid}/signout")
    def user_sign_out(uid: int, user: dict = Depends(signed_in)):
        target = user_or_404(user, uid)
        db.bump_session_version(uid)
        db.audit(user, "user_signed_out", target.get("engagement_id"), target["email"])
        return back(f"/users/{uid}")

    # --- activity log ------------------------------------------------------

    @app.get("/activity")
    def activity(request: Request, user: dict = Depends(signed_in)):
        if not auth.can(user, "view_audit"):
            raise Forbidden()
        ids = auth.accessible_engagement_ids(user)
        return render(request, "activity.html", {"entries": db.list_audit(engagement_ids=ids)})

    # --- engagements -------------------------------------------------------

    @app.get("/")
    def index(request: Request, deleted: str = "", user: dict = Depends(signed_in)):
        engagements = db.list_engagements(auth.accessible_engagement_ids(user))
        if user["role"] in auth.CLIENT_ROLES and len(engagements) == 1:
            return back(f"/e/{engagements[0]['id']}")
        return render(request, "index.html", {"engagements": engagements, "deleted": deleted})

    @app.post("/engagements")
    def create(client_name: str = Form(...), seller_name: str = Form(""), user: dict = Depends(signed_in)):
        if not auth.can(user, "create_engagement"):
            raise Forbidden()
        eid = db.create_engagement(client_name.strip(), seller_name.strip() or None)
        db.audit(user, "client_created", eid, client_name.strip())
        return back(f"/e/{eid}")

    @app.get("/e/{eid}")
    def engagement(request: Request, eid: int, tab: str = "schedule", q: str = "", state: str = "",
                   intake: str = "", delete_error: str = "", user: dict = Depends(signed_in)):
        e = need(user, eid)
        firm_tabs = {"excluded": "work", "ask": "ask", "policy": "policy", "batches": "work"}
        if tab in firm_tabs and not auth.can(user, firm_tabs[tab]):
            tab = "schedule"
        policy, result = score(e)
        rows = sorted(result.rows, key=lambda r: (r.company.lower(), r.state))
        if q:
            ql = q.lower()
            rows = [r for r in rows if ql in r.company.lower() or ql in r.source_file.lower()
                    or ql in (r.registration or "").lower()]
        if state:
            rows = [r for r in rows if r.state == state.upper()]
        statuses = {s: 0 for s in ("pending", "processing", "extracted", "error")}
        for c in result.certificates:
            statuses[c.cert["status"]] = statuses.get(c.cert["status"], 0) + 1
        follow = [(c, f) for c in result.certificates for f in c.flags if f.severity != "info"]
        follow.sort(key=lambda cf: (cf[1].severity != "follow_up", cf[0].cert["filename"].lower()))
        return render(request, "engagement.html", {
            "e": e, "policy": policy, "labels": POLICY_LABELS, "result": result, "rows": rows,
            "statuses": statuses, "follow": follow, "tab": tab, "q": q, "state": state,
            "batches": db.list_batches(eid), "qa": db.list_qa(eid) if auth.can(user, "ask") else [],
            "busy": eid in running, "done": statuses["extracted"] + statuses["error"],
            "intake": _parse_intake(intake, eid), "delete_error": delete_error,
            "staff": db.engagement_staff(eid) if user["role"] in auth.FIRM_ROLES else [],
        })

    @app.post("/e/{eid}/upload")
    async def upload(eid: int, background: BackgroundTasks, label: str = Form(""),
                     files: list[UploadFile] = File(...), user: dict = Depends(signed_in)):
        need(user, eid, "work")
        uploads = [(f.filename or "upload.pdf", await f.read()) for f in files]
        label = label.strip() or (Path(uploads[0][0]).stem if len(uploads) == 1 else f"Upload {date.today()}")
        report = ingest(eid, uploads, label)
        background.add_task(read_pending, eid)
        counts = [len(report.added), len(report.duplicates_linked), len(report.already_on_file),
                  len(report.skipped)]
        db.audit(user, "upload", eid, f"{label}: {counts[0]} new, {counts[2]} already on file")
        return back(f"/e/{eid}?tab=certificates&intake={report.batch_id}-" + "-".join(map(str, counts)))

    @app.post("/e/{eid}/process")
    def process(eid: int, background: BackgroundTasks, user: dict = Depends(signed_in)):
        need(user, eid, "work")
        background.add_task(read_pending, eid)
        return back(f"/e/{eid}?tab=certificates")

    @app.post("/e/{eid}/delete")
    def delete(eid: int, confirm_name: str = Form(""), user: dict = Depends(signed_in)):
        e = need(user, eid, "delete")
        if eid in running:
            return back(f"/e/{eid}?delete_error=busy")
        if confirm_name.strip().casefold() != e["client_name"].strip().casefold():
            return back(f"/e/{eid}?delete_error=name")
        db.delete_engagement(eid)
        shutil.rmtree(db.files_dir() / str(eid), ignore_errors=True)
        db.audit(user, "client_deleted", eid, e["client_name"])
        return RedirectResponse(f"/?deleted={quote(e['client_name'])}", status_code=303)

    @app.post("/e/{eid}/policy")
    async def policy(request: Request, eid: int, user: dict = Depends(signed_in)):
        need(user, eid, "policy")
        form = await request.form()
        p = Policy()
        for key in POLICY_LABELS:
            setattr(p, key, form.get(key) == "on")
        try:
            p.expiring_soon_days = int(form.get("expiring_soon_days") or 90)
        except ValueError:
            pass
        db.save_policy(eid, p.as_dict())
        db.audit(user, "policy_changed", eid, json.dumps(p.as_dict()))
        return back(f"/e/{eid}?tab=policy")

    @app.get("/e/{eid}/export.xlsx")
    def export(eid: int, user: dict = Depends(signed_in)):
        e = need(user, eid, "export")
        policy, result = score(e)
        data = build_workbook(e, result, policy, db.list_batches(eid))
        db.audit(user, "export", eid)
        name = re.sub(r"[^A-Za-z0-9]+", "_", e["client_name"]).strip("_") or "client"
        return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{name}_certificate_schedule.xlsx"'})

    @app.post("/e/{eid}/ask")
    def ask(eid: int, question: str = Form(...), user: dict = Depends(signed_in)):
        e = need(user, eid, "ask")
        policy, result = score(e)
        if not ai_available():
            answer = "Follow-up questions need an Anthropic API key (set ANTHROPIC_API_KEY and restart)."
        else:
            history = [(r["question"], r["answer"]) for r in db.list_qa(eid)]
            try:
                answer = app.state.ask(question, assistant.engagement_context(e, result, policy), history)
            except Exception as exc:
                answer = f"Could not get an answer: {type(exc).__name__}: {exc}"
        db.add_qa(eid, question, answer)
        db.audit(user, "question", eid, question[:300])
        return back(f"/e/{eid}?tab=ask")

    # --- certificates ------------------------------------------------------

    @app.get("/e/{eid}/c/{cid}")
    def certificate(request: Request, eid: int, cid: int, user: dict = Depends(signed_in)):
        e = need(user, eid)
        policy, result = score(e)
        match = next((c for c in result.certificates if c.cert["id"] == cid), None)
        if not match:
            raise HTTPException(404, "Not found")
        ids = [c.cert["id"] for c in sorted(result.certificates, key=lambda c: c.cert["filename"].lower())]
        i = ids.index(cid)
        return render(request, "certificate.html", {
            "e": e, "c": match, "fn": json.loads(match.cert["filename_json"]),
            "prev_id": ids[i - 1] if i > 0 else None,
            "next_id": ids[i + 1] if i + 1 < len(ids) else None,
        })

    @app.get("/e/{eid}/c/{cid}/pdf")
    def pdf(eid: int, cid: int, user: dict = Depends(signed_in)):
        need(user, eid)
        c = cert_in(eid, cid)
        db.audit(user, "pdf_viewed", eid, c["filename"])
        return FileResponse(c["storage_path"], media_type="application/pdf",
                            headers={"Content-Disposition": "inline"})

    @app.post("/e/{eid}/c/{cid}/override")
    def override(eid: int, cid: int, state: str = Form(...), decision: str = Form(...), note: str = Form(""),
                 user: dict = Depends(signed_in)):
        need(user, eid, "work")
        c = cert_in(eid, cid)
        db.set_override(cid, state.upper(), None if decision == "clear" else decision, note.strip() or None)
        db.audit(user, "override", eid, f"{c['filename']}: {state.upper()} {decision}")
        return back(f"/e/{eid}/c/{cid}")

    @app.post("/e/{eid}/c/{cid}/review")
    def review(eid: int, cid: int, review_status: str = Form(...), note: str = Form(""),
               user: dict = Depends(signed_in)):
        need(user, eid, "work")
        c = cert_in(eid, cid)
        db.set_review(cid, review_status, note.strip() or None)
        db.audit(user, "review", eid, f"{c['filename']}: {review_status}")
        return back(f"/e/{eid}/c/{cid}")

    @app.post("/e/{eid}/c/{cid}/retry")
    def retry(eid: int, cid: int, background: BackgroundTasks, user: dict = Depends(signed_in)):
        need(user, eid, "work")
        cert_in(eid, cid)
        db.requeue(cid)
        background.add_task(read_pending, eid)
        return back(f"/e/{eid}/c/{cid}")

    return app


def _parse_intake(text: str, eid: int) -> dict | None:
    """Decode the upload summary passed back to the page after an upload."""
    try:
        bid, added, linked, existing, skipped = (int(x) for x in text.split("-"))
    except ValueError:
        return None
    batch = db.get_batch(bid)
    if not batch or batch["engagement_id"] != eid:
        return None
    return {"label": batch["label"] if batch else "", "added": added, "linked": linked,
            "existing": existing, "skipped": skipped}
