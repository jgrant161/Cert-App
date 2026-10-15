"""Web front end: the shop's back office and the consignor portal, in one app.

Shop pages (owner, staff) live at the top level. Consignor pages live under
/me and only ever load records through the signed-in consignor's id, so one
consignor can never see another's items, sales or payouts.
"""

from __future__ import annotations

import csv
import io
import json
import os
import secrets
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from .. import auth, barcode, db, ledger, listings, money, ops, square

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")
# Blank form fields arrive as undefined or None; both show as empty.
templates.env.filters["money"] = lambda c, blank="": money.fmt(c if isinstance(c, int) else None, blank)
templates.env.filters["plain"] = lambda c: money.plain(c if isinstance(c, int) else None)
templates.env.globals.update(CHANNELS=money.CHANNELS, PAYOUT_METHODS=money.PAYOUT_METHODS, ROLES=auth.ROLES,
                             can=auth.can, is_staff=auth.is_staff, setting=money.setting)
templates.env.globals["barcode"] = lambda text: Markup(barcode.svg(text))
templates.env.globals["today"] = db.today

STATUS_LABELS = {"submitted": "Submitted", "received": "In shop, not listed", "listed": "For sale",
                 "sold": "Sold", "returned": "Returned", "declined": "Declined"}
templates.env.globals["STATUS_LABELS"] = STATUS_LABELS

PHOTO_TYPES = {b"\xff\xd8\xff": "jpg", b"\x89PNG": "png", b"RIFF": "webp", b"GIF8": "gif"}
PHOTO_MAX_BYTES = 15 * 1024 * 1024
PHOTOS_PER_ITEM = 12
ITEM_TEXT_FIELDS = ("title", "category", "sport", "year", "brand_set", "card_number", "subject", "grader", "grade",
                    "cert_number", "condition", "description", "tcgplayer_id", "location")


def production() -> bool:
    return os.environ.get("CONSIGN_ENV", "").lower() == "production"


def _secret_key() -> str:
    key = os.environ.get("CONSIGN_SECRET_KEY")
    if key:
        return key
    if production():
        raise RuntimeError("Set CONSIGN_SECRET_KEY before running in production.")
    path = db.data_dir() / "secret_key"
    if not path.exists():
        path.write_text(secrets.token_urlsafe(48))
    return path.read_text().strip()


class SignInRequired(Exception):
    pass


class Forbidden(Exception):
    pass


def photo_kind(head: bytes) -> str | None:
    for magic, ext in PHOTO_TYPES.items():
        if head.startswith(magic) and (ext != "webp" or head[8:12] == b"WEBP"):
            return ext
    return None


def create_app(square_factory=None) -> FastAPI:
    db.init()
    app = FastAPI(title="Dug Out Consignment", docs_url=None, redoc_url=None, openapi_url=None)
    # A function returning a Square client, or None when Square is not connected.
    app.state.square = square_factory or (lambda: square.Square() if square.configured() else None)
    throttle = auth.LoginThrottle()

    @app.middleware("http")
    async def security(request: Request, call_next):
        # Refuse form posts that come from another site (cross-site request forgery).
        if request.method not in ("GET", "HEAD", "OPTIONS") and not request.url.path.startswith("/webhooks/"):
            source = request.headers.get("origin") or request.headers.get("referer")
            if source and urlsplit(source).netloc != request.headers.get("host"):
                return Response("Request refused: it did not come from this site.", status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' "
            "https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data: blob:; "
            "frame-ancestors 'self'; object-src 'none'; base-uri 'self'; form-action 'self'")
        if production():
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    app.add_middleware(SessionMiddleware, secret_key=_secret_key(), session_cookie="consign_session",
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
            return render(request, "message.html", {"title": "Page not found",
                                                    "message": "It may have been removed, or your account cannot see it."}, 404)
        return Response(str(exc.detail), status_code=exc.status_code)

    @app.exception_handler(Forbidden)
    async def _forbidden(request: Request, exc: Forbidden):
        return render(request, "message.html", {"title": "Not available to your account",
                                                "message": "Ask the shop owner if you need access."}, 403)

    # --- helpers -----------------------------------------------------------

    def render(request: Request, name: str, ctx: dict, status_code: int = 200):
        user = auth.current_user(request.session) if "uid" in request.session else None
        flash = request.session.pop("flash", None)
        error = request.session.pop("error", None)
        ctx = {"user": user, "flash": flash, "error": error, "square_on": square.configured(), **ctx}
        return templates.TemplateResponse(request, name, ctx, status_code=status_code)

    def signed_in(request: Request) -> dict:
        user = auth.current_user(request.session)
        if not user:
            raise SignInRequired()
        return user

    def staff(request: Request, action: str = "work") -> dict:
        user = signed_in(request)
        if not auth.is_staff(user):
            raise HTTPException(404)        # consignors never see that shop pages exist
        if not auth.can(user, action):
            raise Forbidden()
        return user

    def consignor_user(request: Request) -> tuple[dict, dict]:
        user = signed_in(request)
        if user["role"] != "consignor" or not user["consignor_id"]:
            raise HTTPException(404)
        return user, db.get_consignor(user["consignor_id"])

    def back(url: str, flash: str | None = None, error: str | None = None, request: Request | None = None):
        if request is not None:
            if flash:
                request.session["flash"] = flash
            if error:
                request.session["error"] = error
        return RedirectResponse(url, status_code=303)

    def safe_next(target: str | None) -> str:
        ok = target and target.startswith("/") and not target.startswith(("//", "/\\")) and "\\" not in target
        return target if ok else "/"

    def item_or_404(iid: int) -> dict:
        item = db.get_item(iid)
        if not item:
            raise HTTPException(404)
        return item

    def own_item(cid: int, iid: int) -> dict:
        item = db.get_item(iid)
        if not item or item["consignor_id"] != cid:
            raise HTTPException(404)
        return item

    def cents(value, label: str, required: bool = False, negative: bool = False) -> int | None:
        try:
            c = money.to_cents(value, allow_negative=negative)
        except ValueError as e:
            raise ops.OpError(f"{label}: {e}") from None
        if required and c is None:
            raise ops.OpError(f"Enter {label.lower()}.")
        return c

    def pct(value, label: str = "Commission") -> float | None:
        value = (value or "").strip().rstrip("%")
        if not value:
            return None
        try:
            p = float(value)
        except ValueError:
            raise ops.OpError(f"{label} must be a number of percent.") from None
        if not 0 <= p <= 100:
            raise ops.OpError(f"{label} must be between 0 and 100.")
        return p

    def base_url(request: Request) -> str:
        return (os.environ.get("CONSIGN_BASE_URL") or str(request.base_url)).rstrip("/")

    def photo_urls(request: Request, items: list[dict]) -> dict[int, list[str]]:
        base = base_url(request)
        return {iid: [f"{base}/p/{p['token']}.{p['ext']}" for p in ps]
                for iid, ps in db.photos_for_items([i["id"] for i in items]).items()}

    async def save_photos(item: dict, files: list[UploadFile], user: dict) -> str | None:
        have = len(db.list_photos(item["id"]))
        skipped = []
        for f in files:
            if not f or not f.filename:
                continue
            data = await f.read(PHOTO_MAX_BYTES + 1)
            ext = photo_kind(data[:16])
            if not ext or len(data) > PHOTO_MAX_BYTES:
                skipped.append(f"{f.filename} ({'too large' if ext else 'not a JPEG, PNG, WebP or GIF photo'})")
                continue
            if have >= PHOTOS_PER_ITEM:
                skipped.append(f"{f.filename} (limit is {PHOTOS_PER_ITEM} photos per item)")
                continue
            token = secrets.token_urlsafe(16)
            folder = db.files_dir() / "photos"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{token}.{ext}").write_bytes(data)
            db.add_photo(item["id"], token, ext, user["id"])
            have += 1
        if skipped:
            return "Not added: " + "; ".join(skipped)
        return None

    def csv_response(name: str, header: list[str], rows: list[list]) -> Response:
        return Response(listings.to_csv(header, rows), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    def sq():
        return app.state.square()

    def parse_ids(raw: str | list | None) -> list[int]:
        if isinstance(raw, list):
            raw = ",".join(raw)
        return [int(x) for x in (raw or "").replace(" ", "").split(",") if x.isdigit()]

    # --- health, setup, sign-in -------------------------------------------

    @app.get("/healthz")
    def healthz():
        try:
            db.ping()
        except Exception:
            return JSONResponse({"ok": False}, status_code=503)
        return {"ok": True}

    @app.get("/setup")
    def setup_form(request: Request):
        if db.count_users():
            return back("/login")
        return render(request, "setup.html", {"need_token": production()})

    @app.post("/setup")
    def setup(request: Request, name: str = Form(...), email: str = Form(...), password: str = Form(""),
              confirm: str = Form(""), setup_token: str = Form("")):
        if db.count_users():
            return back("/login")
        if production() and not secrets.compare_digest(setup_token, os.environ.get("CONSIGN_SETUP_TOKEN") or secrets.token_hex()):
            return render(request, "setup.html", {"need_token": True, "error": "The setup code is not correct."}, 400)
        problem = auth.password_problem(password, confirm)
        if problem:
            return render(request, "setup.html", {"need_token": production(), "error": problem}, 400)
        uid = db.create_user(email, name, "owner", auth.hash_password(password))
        user = db.get_user(uid)
        db.audit(user, "setup", detail="Owner account created")
        auth.start_session(request.session, user)
        return back("/settings", "Welcome. Check the commission tiers and fees below first.", request=request)

    @app.get("/login")
    def login_form(request: Request, next: str = "/"):
        if db.count_users() == 0:
            return back("/setup")
        return render(request, "login.html", {"next": safe_next(next), "email": ""})

    @app.post("/login")
    def login(request: Request, email: str = Form(...), password: str = Form(...), next: str = Form("/")):
        user, error = auth.authenticate(email, password, throttle)
        if not user:
            db.audit(None, "sign_in_failed", detail=email.strip().lower()[:200])
            return render(request, "login.html", {"next": safe_next(next), "error": error, "email": email}, 400)
        auth.start_session(request.session, user)
        db.audit(user, "sign_in")
        return back(safe_next(next))

    @app.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return back("/login")

    @app.get("/invite/{token}")
    def invite_form(request: Request, token: str):
        invite, user = auth.check_invite_token(token)
        if not invite:
            return render(request, "message.html", {"title": "This link has expired or was already used",
                                                    "message": "Ask the shop for a new link."}, 400)
        return render(request, "invite.html", {"invitee": user, "token": token})

    @app.post("/invite/{token}")
    def invite_accept(request: Request, token: str, password: str = Form(""), confirm: str = Form("")):
        invite, user = auth.check_invite_token(token)
        if not invite:
            return back(f"/invite/{token}")
        problem = auth.password_problem(password, confirm)
        if problem:
            return render(request, "invite.html", {"invitee": user, "token": token, "error": problem}, 400)
        db.update_user(user["id"], password_hash=auth.hash_password(password))
        db.use_invite(invite["id"])
        db.bump_session_version(user["id"])
        user = db.get_user(user["id"])
        db.audit(user, "invite_accepted")
        auth.start_session(request.session, user)
        return back("/")

    @app.get("/account")
    def account(request: Request):
        user = signed_in(request)
        consignor = db.get_consignor(user["consignor_id"]) if user["consignor_id"] else None
        return render(request, "account.html", {"consignor": consignor})

    @app.post("/account/password")
    def account_password(request: Request, current: str = Form(""), password: str = Form(""), confirm: str = Form("")):
        user = signed_in(request)
        if not auth.verify_password(current, user["password_hash"]):
            return back("/account", error="Your current password is not correct.", request=request)
        problem = auth.password_problem(password, confirm)
        if problem:
            return back("/account", error=problem, request=request)
        db.update_user(user["id"], password_hash=auth.hash_password(password))
        db.bump_session_version(user["id"])
        auth.start_session(request.session, db.get_user(user["id"]))
        db.audit(user, "password_changed")
        return back("/account", "Password changed. Other devices have been signed out.", request=request)

    @app.post("/account/details")
    def account_details(request: Request, phone: str = Form(""), address: str = Form(""),
                        payout_method: str = Form("check"), payout_details: str = Form("")):
        user, consignor = consignor_user(request)
        if payout_method not in money.PAYOUT_METHODS:
            payout_method = consignor["payout_method"]
        db.update_consignor(consignor["id"], phone=phone.strip(), address=address.strip(),
                            payout_method=payout_method, payout_details=payout_details.strip())
        db.audit(user, "consignor_updated_details", consignor["id"])
        return back("/account", "Saved.", request=request)

    # --- public photo links (used in marketplace listings) ------------------

    @app.get("/p/{name}")
    def public_photo(name: str):
        token, _, ext = name.rpartition(".")
        photo = db.get_photo_by_token(token)
        if not photo or photo["ext"] != ext:
            raise HTTPException(404)
        return FileResponse(db.files_dir() / "photos" / f"{token}.{ext}",
                            headers={"Cache-Control": "public, max-age=86400"})

    # --- dashboard -----------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        user = signed_in(request)
        if user["role"] == "consignor":
            return back("/me")
        month_start = date.fromisoformat(db.today()).replace(day=1).isoformat()
        soon = (date.fromisoformat(db.today()) + timedelta(days=14)).isoformat()
        on_hand = db.list_items(status="on_hand")
        return render(request, "dashboard.html", {
            "acct": ledger.accounts(),
            "month": ledger.period_summary(month_start),
            "counts": db.item_counts(),
            "submissions": db.list_items(status="submitted", limit=50),
            "proposals": db.pending_proposals("consignor"),
            "aging": [i for i in on_hand if i["expires_at"] and i["expires_at"] <= soon][:50],
            "unlisted": [i for i in on_hand if i["status"] == "received"][:50],
            "recent": db.list_sales(limit=10),
            "pending_payouts": db.list_payouts(status="pending"),
        })

    # --- consignors --------------------------------------------------------

    @app.get("/consignors")
    def consignors(request: Request, show: str = "active"):
        staff(request)
        rows = db.list_consignors(active_only=show == "active")
        acct = ledger.accounts()
        return render(request, "consignors.html", {"rows": rows, "balances": acct["balances"], "show": show})

    def consignor_fields(form) -> dict:
        method = form.get("payout_method", "check")
        if method not in money.PAYOUT_METHODS:
            raise ops.OpError("Choose a payout method.")
        name = (form.get("name") or "").strip()
        if not name:
            raise ops.OpError("Enter the consignor's name.")
        return {"name": name, "email": (form.get("email") or "").strip().lower() or None,
                "phone": (form.get("phone") or "").strip(), "address": (form.get("address") or "").strip(),
                "payout_method": method, "payout_details": (form.get("payout_details") or "").strip(),
                "commission_pct": pct(form.get("commission_pct")), "notes": (form.get("notes") or "").strip()}

    @app.post("/consignors")
    async def consignor_create(request: Request):
        user = staff(request)
        try:
            fields = consignor_fields(await request.form())
        except ops.OpError as e:
            return back("/consignors", error=str(e), request=request)
        name = fields.pop("name")
        cid = db.create_consignor(name, **fields)
        db.audit(user, "consignor_created", cid, detail=name)
        return back(f"/consignors/{cid}", f"{name} added.", request=request)

    @app.get("/consignors/{cid}")
    def consignor_page(request: Request, cid: int):
        staff(request)
        c = db.get_consignor(cid)
        if not c:
            raise HTTPException(404)
        return render(request, "consignor.html", {
            "c": c, "bal": ledger.consignor_balance(cid), "items": db.list_items(consignor_id=cid),
            "statement": list(reversed(ledger.statement(cid))), "payouts": db.list_payouts(cid),
            "logins": db.list_users(consignor_id=cid), "invite_link": request.session.pop("invite_link", None),
            "tiers": money.setting("commission_tiers")})

    @app.post("/consignors/{cid}")
    async def consignor_update(request: Request, cid: int):
        user = staff(request)
        if not db.get_consignor(cid):
            raise HTTPException(404)
        form = await request.form()
        try:
            fields = consignor_fields(form)
        except ops.OpError as e:
            return back(f"/consignors/{cid}", error=str(e), request=request)
        fields["active"] = 1 if form.get("active") else 0
        db.update_consignor(cid, **fields)
        db.audit(user, "consignor_updated", cid)
        return back(f"/consignors/{cid}", "Saved.", request=request)

    @app.post("/consignors/{cid}/invite")
    def consignor_invite(request: Request, cid: int, email: str = Form("")):
        user = staff(request)
        c = db.get_consignor(cid)
        if not c:
            raise HTTPException(404)
        email = (email or c["email"] or "").strip().lower()
        if "@" not in email:
            return back(f"/consignors/{cid}", error="Add the consignor's email address first.", request=request)
        login = db.get_user_by_email(email)
        if login and login["consignor_id"] != cid:
            return back(f"/consignors/{cid}", error="That email already signs in to another account.", request=request)
        if not login:
            uid = db.create_user(email, c["name"], "consignor", consignor_id=cid)
            if not c["email"]:
                db.update_consignor(cid, email=email)
        else:
            uid = login["id"]
        token = auth.create_invite_token(uid)
        request.session["invite_link"] = f"{base_url(request)}/invite/{token}"
        db.audit(user, "consignor_invited", cid, detail=email)
        return back(f"/consignors/{cid}", "Portal link created. Send it to the consignor; it works once, for "
                    f"{auth.INVITE_DAYS} days.", request=request)

    @app.post("/consignors/{cid}/adjustments")
    def adjustment_add(request: Request, cid: int, amount: str = Form(""), direction: str = Form("charge"),
                       memo: str = Form(""), at: str = Form("")):
        user = staff(request)
        if not db.get_consignor(cid):
            raise HTTPException(404)
        try:
            c = cents(amount, "Amount", required=True)
            if not memo.strip():
                raise ops.OpError("Say what the adjustment is for (grading fee, shipping, correction...).")
        except ops.OpError as e:
            return back(f"/consignors/{cid}", error=str(e), request=request)
        signed = -c if direction == "charge" else c
        db.add_adjustment(cid, signed, memo.strip(), _date(at), user["id"])
        db.audit(user, "adjustment", cid, detail=f"{money.fmt(signed)} {memo.strip()}")
        return back(f"/consignors/{cid}", "Adjustment recorded.", request=request)

    def statement_csv(cid: int) -> Response:
        c = db.get_consignor(cid)
        header = ["Date", "Type", "SKU", "Description", "Channel", "Sale price", "Fees", "Commission %",
                  "Commission", "Amount to you", "Balance"]
        rows = [[r["date"], r["kind"], r.get("sku", ""), r["description"],
                 money.CHANNELS.get(r.get("channel"), ""), money.plain(r.get("price")), money.plain(r.get("fees")),
                 f"{r['pct']:g}" if r.get("pct") is not None else "", money.plain(r.get("commission")),
                 money.plain(r["amount"]), money.plain(r["balance"])] for r in ledger.statement(cid)]
        return csv_response(f"statement-{c['code']}.csv", header, rows)

    @app.get("/consignors/{cid}/statement.csv")
    def consignor_statement_csv(request: Request, cid: int):
        staff(request)
        if not db.get_consignor(cid):
            raise HTTPException(404)
        return statement_csv(cid)

    # --- items ---------------------------------------------------------------

    @app.get("/items")
    def items(request: Request, status: str = "", q: str = "", consignor: str = ""):
        staff(request)
        cid = int(consignor) if consignor.isdigit() else None
        rows = db.list_items(consignor_id=cid, status=status or None, q=q or None)
        return render(request, "items.html", {"rows": rows, "status": status, "q": q, "consignor": cid,
                                              "consignors": db.list_consignors(active_only=True)})

    @app.get("/scan")
    def scan(request: Request, code: str = ""):
        user = signed_in(request)
        if not auth.is_staff(user):
            raise HTTPException(404)
        item = db.get_item_by_sku(code) if code.strip() else None
        if item:
            return back(f"/items/{item['id']}")
        return back(f"/items?{urlencode({'q': code})}", error=f"No item has the SKU {code}." if code else None,
                    request=request)

    @app.get("/items/new")
    def item_new(request: Request, consignor: str = ""):
        staff(request)
        return render(request, "item_new.html", {"consignors": db.list_consignors(active_only=True),
                                                 "selected": int(consignor) if consignor.isdigit() else None})

    def item_fields(form) -> dict:
        fields = {k: (form.get(k) or "").strip() or None for k in ITEM_TEXT_FIELDS if k in form}
        if "title" in fields and not fields["title"]:
            raise ops.OpError("Give the item a title.")
        if "quantity" in form:
            try:
                fields["quantity"] = max(int(form.get("quantity") or 1), 0)
            except ValueError:
                raise ops.OpError("Quantity must be a whole number.") from None
        for key, label in (("asking_cents", "Asking price"), ("min_cents", "Floor price")):
            name = key.replace("_cents", "")
            if name in form:
                fields[key] = cents(form.get(name), label)
        if "commission_pct" in form:
            fields["commission_pct"] = pct(form.get("commission_pct"))
        return fields

    @app.post("/items")
    async def item_create(request: Request):
        user = staff(request)
        form = await request.form()
        try:
            cid = int(form.get("consignor_id") or 0)
            if not db.get_consignor(cid):
                raise ops.OpError("Choose the consignor.")
            fields = item_fields(form)
            price = cents(form.get("price"), "Price")
        except ops.OpError as e:
            return back(f"/items/new?consignor={form.get('consignor_id') or ''}", error=str(e), request=request)
        iid = db.create_item(consignor_id=cid, status="submitted", submitted_by="staff", price_cents=price, **fields)
        item = db.get_item(iid)
        db.audit(user, "item_created", cid, iid, item["title"])
        ops.receive(item, user)
        warning = await save_photos(item, form.getlist("photos"), user)
        if price and form.get("list_now"):
            warning = ops.list_item(db.get_item(iid), user, sq()) or warning
        if form.get("another"):
            return back(f"/items/new?consignor={cid}", f"{item['sku']} added.", warning, request)
        return back(f"/items/{iid}", f"{item['sku']} added.", warning, request)

    @app.get("/items/{iid}")
    def item_page(request: Request, iid: int):
        staff(request)
        item = item_or_404(iid)
        shop = money.setting("shop_name")
        price = item["price_cents"] or item["asking_cents"] or 0
        rate = money.commission_pct(item, price)
        preview = money.split(price, money.estimate_fees("instore", price), rate) if price else None
        return render(request, "item.html", {
            "item": item, "photos": db.list_photos(iid), "proposals": db.list_proposals(iid),
            "pending": db.pending_proposal(iid), "sales": db.list_sales(item_id=iid),
            "history": db.list_audit(item_id=iid, limit=50), "rate": rate, "preview": preview,
            "fee_estimates": {ch: money.estimate_fees(ch, price) for ch in money.CHANNELS},
            "copy": {"title": listings.title(item), "description": listings.description(item, shop)},
            "photo_urls": photo_urls(request, [item]).get(iid, [])})

    def item_action(request: Request, iid: int, fn, done: str):
        user = staff(request)
        item = item_or_404(iid)
        try:
            warning = fn(item, user)
        except ops.OpError as e:
            return back(f"/items/{iid}", error=str(e), request=request)
        return back(f"/items/{iid}", done, warning, request)

    @app.post("/items/{iid}")
    async def item_update(request: Request, iid: int):
        user = staff(request)
        item_or_404(iid)
        try:
            fields = item_fields(await request.form())
        except ops.OpError as e:
            return back(f"/items/{iid}", error=str(e), request=request)
        db.update_item(iid, **fields)
        db.audit(user, "item_updated", item_or_404(iid)["consignor_id"], iid)
        item = item_or_404(iid)
        warning = None
        if item["square_item_id"]:
            warning = ops._square(sq(), square.push_item, item)
        return back(f"/items/{iid}", "Saved.", warning, request)

    @app.post("/items/{iid}/photos")
    async def item_photos(request: Request, iid: int, photos: list[UploadFile] = File(default=[])):
        user = staff(request)
        item = item_or_404(iid)
        warning = await save_photos(item, photos, user)
        db.audit(user, "photos_added", item["consignor_id"], iid)
        return back(f"/items/{iid}#photos", "Photos added." if not warning else None, warning, request)

    @app.post("/items/{iid}/photos/{pid}/{action}")
    def item_photo_action(request: Request, iid: int, pid: int, action: str):
        user = staff(request)
        photo = db.get_photo(pid)
        if not photo or photo["item_id"] != iid:
            raise HTTPException(404)
        if action == "delete":
            db.delete_photo(pid)
            (db.files_dir() / "photos" / f"{photo['token']}.{photo['ext']}").unlink(missing_ok=True)
            db.audit(user, "photo_deleted", item_or_404(iid)["consignor_id"], iid)
        elif action == "cover":
            db.make_cover(pid)
        return back(f"/items/{iid}#photos")

    @app.post("/items/{iid}/receive")
    def item_receive(request: Request, iid: int, location: str = Form("")):
        return item_action(request, iid, lambda i, u: ops.receive(i, u, location.strip() or None), "Checked in.")

    @app.post("/items/{iid}/list")
    def item_list(request: Request, iid: int):
        return item_action(request, iid, lambda i, u: ops.list_item(i, u, sq()), "Listed for sale.")

    @app.post("/items/{iid}/price")
    def item_price(request: Request, iid: int, price: str = Form(""), mode: str = Form("set"), note: str = Form("")):
        def go(item, user):
            c = cents(price, "Price", required=True)
            if mode == "propose":
                ops.propose_price(item, "shop", c, note.strip() or None, user)
                return None
            return ops.set_price(item, c, user, sq())
        msg = "Price sent to the consignor to approve." if mode == "propose" else "Price saved."
        return item_action(request, iid, go, msg)

    @app.post("/items/{iid}/proposals/{pid}")
    def item_proposal(request: Request, iid: int, pid: int, answer: str = Form(...)):
        def go(item, user):
            p = next((p for p in db.list_proposals(iid) if p["id"] == pid), None)
            if not p:
                raise HTTPException(404)
            if answer == "withdraw":
                if p["status"] != "pending":
                    raise ops.OpError("Already answered.")
                db.decide_proposal(pid, "withdrawn")
                return None
            if p["proposed_by"] != "consignor":
                raise ops.OpError("The consignor answers the shop's proposals.")
            return ops.answer_proposal(item, p, answer == "accept", user, sq())
        return item_action(request, iid, go, "Done.")

    @app.post("/items/{iid}/sale")
    def item_sale(request: Request, iid: int, channel: str = Form(...), price: str = Form(""), fees: str = Form(""),
                  sold_at: str = Form(""), external_ref: str = Form(""), below_floor: str = Form("")):
        def go(item, user):
            _, warning = ops.record_sale(item, channel, cents(price, "Sale price", required=True), cents(fees, "Fees"),
                                         _date(sold_at), user, external_ref.strip() or None, sq=sq(),
                                         allow_below_floor=bool(below_floor))
            return warning
        return item_action(request, iid, go, "Sale recorded.")

    @app.post("/items/{iid}/return")
    def item_return(request: Request, iid: int):
        return item_action(request, iid, lambda i, u: ops.close_item(i, "returned", u, sq()),
                           "Marked as returned to the consignor.")

    @app.post("/items/{iid}/decline")
    def item_decline(request: Request, iid: int):
        return item_action(request, iid, lambda i, u: ops.close_item(i, "declined", u, None), "Submission declined.")

    @app.post("/items/{iid}/relist")
    def item_relist(request: Request, iid: int):
        return item_action(request, iid, ops.relist, "Back in the shop.")

    @app.post("/items/{iid}/square")
    def item_square(request: Request, iid: int):
        def go(item, user):
            client = sq()
            if client is None:
                raise ops.OpError("Square is not connected. See the Square page.")
            try:
                square.push_item(client, item)
            except Exception as e:   # noqa: BLE001
                raise ops.OpError(f"Square did not accept the item: {e}") from None
            db.audit(user, "square_pushed", item["consignor_id"], item["id"])
        return item_action(request, iid, go, "Sent to Square.")

    @app.post("/sales/{sid}/void")
    def sale_void(request: Request, sid: int, reason: str = Form("")):
        user = staff(request, "void")
        sale = db.get_sale(sid)
        if not sale:
            raise HTTPException(404)
        try:
            warning = ops.void_sale(sale, reason, user, sq())
        except ops.OpError as e:
            return back(f"/items/{sale['item_id']}", error=str(e), request=request)
        return back(f"/items/{sale['item_id']}", "Sale voided; the item is back for sale.", warning, request)

    @app.post("/items/bulk")
    async def items_bulk(request: Request):
        staff(request)
        form = await request.form()
        ids = parse_ids(form.getlist("ids"))
        action = form.get("action", "")
        if not ids:
            return back("/items", error="Tick at least one item first.", request=request)
        q = ",".join(map(str, ids))
        if action == "labels":
            return back(f"/labels?ids={q}")
        if action in listings.BUILDERS or action == "square_csv":
            return back(f"/exports/{action}.csv?ids={q}")
        if action == "square_push":
            client = sq()
            if client is None:
                return back("/items", error="Square is not connected.", request=request)
            done, failed = 0, []
            for item in db.list_items(ids=ids):
                try:
                    square.push_item(client, item)
                    done += 1
                except Exception as e:  # noqa: BLE001
                    failed.append(f"{item['sku']}: {e}")
            return back("/items", f"{done} sent to Square.", "; ".join(failed) or None, request)
        return back("/items")

    # --- labels --------------------------------------------------------------

    @app.get("/labels")
    def labels(request: Request, ids: str = "", format: str = "", copies: int = 1):
        staff(request)
        rows = db.list_items(ids=parse_ids(ids))
        rows.sort(key=lambda r: r["id"])
        fmt = format or money.setting("label_format")
        copies = max(1, min(copies, 10))
        return render(request, "labels.html", {"rows": [r for r in rows for _ in range(copies)], "fmt": fmt,
                                               "ids": ids, "copies": copies})

    # --- payouts ---------------------------------------------------------------

    @app.get("/payouts")
    def payouts(request: Request, consignor: str = ""):
        staff(request)
        acct = ledger.accounts()
        consignors = db.list_consignors()
        due = [dict(c, **acct["balances"][c["id"]]) for c in consignors
               if acct["balances"][c["id"]]["available"] > 0]
        return render(request, "payouts.html", {"due": due, "payouts": db.list_payouts(), "acct": acct,
                                                "consignors": consignors,
                                                "selected": int(consignor) if consignor.isdigit() else None,
                                                "min_payout": money.setting("min_payout_cents")})

    @app.post("/payouts")
    def payout_create(request: Request, consignor_id: int = Form(...), amount: str = Form(""),
                      method: str = Form(""), reference: str = Form(""), status: str = Form("sent"),
                      memo: str = Form("")):
        user = staff(request)
        c = db.get_consignor(consignor_id)
        if not c:
            raise HTTPException(404)
        try:
            bal = ledger.consignor_balance(consignor_id)
            ops.create_payout(c, cents(amount, "Amount", required=True), method or c["payout_method"],
                              reference.strip() or None, status == "sent", memo.strip() or None, user, bal["available"])
        except ops.OpError as e:
            return back(f"/payouts?consignor={consignor_id}", error=str(e), request=request)
        return back("/payouts", f"Payout to {c['name']} recorded.", request=request)

    @app.post("/payouts/{pid}/{action}")
    def payout_action(request: Request, pid: int, action: str, reference: str = Form("")):
        user = staff(request, "void" if action == "void" else "work")
        p = db.get_payout(pid)
        if not p:
            raise HTTPException(404)
        try:
            if action == "sent":
                ops.mark_payout_sent(p, reference.strip() or None, user)
            elif action == "void":
                ops.void_payout(p, user)
        except ops.OpError as e:
            return back("/payouts", error=str(e), request=request)
        return back("/payouts", "Updated.", request=request)

    # --- money: the two accounts --------------------------------------------

    @app.get("/money")
    def money_page(request: Request):
        staff(request)
        return render(request, "money.html", {"acct": ledger.accounts(), "transfers": db.list_transfers(),
                                              "adjustments": db.list_adjustments()[:50]})

    @app.post("/transfers")
    def transfer_add(request: Request, direction: str = Form(...), amount: str = Form(""), at: str = Form(""),
                     reference: str = Form(""), memo: str = Form("")):
        user = staff(request)
        if direction not in ("to_consignment", "to_shop"):
            raise HTTPException(400)
        try:
            c = cents(amount, "Amount", required=True)
        except ops.OpError as e:
            return back("/money", error=str(e), request=request)
        db.add_transfer(direction, c, _date(at), reference.strip() or None, memo.strip() or None, user["id"])
        db.audit(user, "transfer", detail=f"{direction} {money.fmt(c)}")
        return back("/money", "Transfer recorded.", request=request)

    # --- Square ----------------------------------------------------------------

    @app.get("/square")
    def square_page(request: Request):
        staff(request)
        listed = db.list_items(status="listed")
        return render(request, "square.html", {
            "configured": square.configured(), "env": os.environ.get("SQUARE_ENVIRONMENT", "production"),
            "last_sync": db.get_setting("square_last_sync"), "listed": len(listed),
            "not_pushed": sum(1 for i in listed if not i["square_item_id"]),
            "webhook_url": f"{base_url(request)}/webhooks/square"})

    def run_square_sync(user: dict | None) -> dict:
        client = sq()
        if client is None:
            raise ops.OpError("Square is not connected.")

        def record(item, price_cents, sold_at, order_id):
            ops.record_sale(item, "instore", price_cents, None, sold_at, user, order_id, square_order_id=order_id,
                            allow_below_floor=True)
        return square.sync_sales(client, record)

    @app.post("/square/sync")
    def square_sync(request: Request):
        user = staff(request)
        try:
            result = run_square_sync(user)
        except Exception as e:   # noqa: BLE001
            return back("/square", error=f"Sync failed: {e}", request=request)
        db.audit(user, "square_sync", detail=f"{len(result['recorded'])} sales")
        return back("/square", f"{len(result['recorded'])} in-store sale(s) recorded.",
                    "; ".join(result["skipped"]) or None, request)

    @app.post("/square/push")
    def square_push_all(request: Request, only_new: str = Form("")):
        user = staff(request)
        client = sq()
        if client is None:
            return back("/square", error="Square is not connected.", request=request)
        done, failed = 0, []
        for item in db.list_items(status="listed"):
            if only_new and item["square_item_id"]:
                continue
            try:
                square.push_item(client, item)
                done += 1
            except Exception as e:   # noqa: BLE001
                failed.append(f"{item['sku']}: {e}")
        db.audit(user, "square_push_all", detail=f"{done} items")
        return back("/square", f"{done} item(s) sent to Square.", "; ".join(failed[:10]) or None, request)

    @app.post("/webhooks/square")
    async def square_webhook(request: Request):
        body = await request.body()
        if not square.verify_webhook(body, request.headers.get("x-square-hmacsha256-signature", "")):
            return Response("bad signature", status_code=401)
        try:
            kind = json.loads(body).get("type", "")
        except ValueError:
            kind = ""
        if kind.startswith(("order.", "payment.")):
            try:
                run_square_sync(None)
            except Exception:   # noqa: BLE001 - Square retries; the next sync picks it up
                pass
        return {"ok": True}

    # --- exports -------------------------------------------------------------

    @app.get("/exports/{name}.csv")
    def export(request: Request, name: str, ids: str = "", status: str = "listed"):
        staff(request)
        rows = db.list_items(ids=parse_ids(ids)) if ids else db.list_items(status=status)
        rows.sort(key=lambda r: r["id"])
        stamp = db.today()
        if name == "square_csv":
            header, data = square.csv_rows(rows)
            return csv_response(f"square-items-{stamp}.csv", header, data)
        if name in listings.BUILDERS:
            header, data = listings.BUILDERS[name](rows, photo_urls(request, rows), money.setting("shop_name"))
            return csv_response(f"{name}-listings-{stamp}.csv", header, data)
        if name == "sales":
            header = ["Date", "SKU", "Item", "Consignor", "Channel", "Order", "Sale price", "Fees", "Commission %",
                      "Shop commission", "Consignor share", "Void"]
            data = [[s["sold_at"], s["sku"], s["title"], s["consignor_name"], money.CHANNELS[s["channel"]],
                     s["external_ref"] or s["square_order_id"] or "", money.plain(s["price_cents"]),
                     money.plain(s["fees_cents"]), f"{s['commission_pct']:g}", money.plain(s["commission_cents"]),
                     money.plain(s["consignor_cents"]), s["void_reason"] or ""] for s in db.list_sales()]
            return csv_response(f"sales-{stamp}.csv", header, data)
        if name == "inventory":
            header = ["SKU", "Consignor", "Title", "Status", "Price", "Floor", "Qty", "Received", "Expires",
                      "Location", "Grader", "Grade", "Cert"]
            data = [[i["sku"], i["consignor_name"], i["title"], STATUS_LABELS[i["status"]], money.plain(i["price_cents"]),
                     money.plain(i["min_cents"]), i["quantity"], i["received_at"] or "", i["expires_at"] or "",
                     i["location"] or "", i["grader"] or "", i["grade"] or "", i["cert_number"] or ""] for i in rows]
            return csv_response(f"inventory-{stamp}.csv", header, data)
        raise HTTPException(404)

    # --- settings, users, activity -------------------------------------------

    @app.get("/settings")
    def settings_page(request: Request):
        staff(request, "settings")
        return render(request, "settings.html", {"s": money.all_settings(), "tiers": money.tiers_text(
            money.setting("commission_tiers"))})

    @app.post("/settings")
    async def settings_save(request: Request):
        user = staff(request, "settings")
        form = await request.form()
        try:
            fees = {}
            for ch in money.CHANNELS:
                fees[ch] = [pct(form.get(f"fee_pct_{ch}"), f"{money.CHANNELS[ch]} fee") or 0.0,
                            cents(form.get(f"fee_fixed_{ch}"), f"{money.CHANNELS[ch]} per-order fee") or 0]
            values = {
                "shop_name": (form.get("shop_name") or "").strip() or money.DEFAULTS["shop_name"],
                "commission_tiers": money.parse_tiers(form.get("commission_tiers", "")),
                "fees_before_commission": bool(form.get("fees_before_commission")),
                "channel_fees": fees,
                "consignment_days": _int(form.get("consignment_days"), "Consignment period"),
                "hold_days": _int(form.get("hold_days"), "Hold period"),
                "min_payout_cents": cents(form.get("min_payout"), "Minimum payout") or 0,
                "opening_consignment_cents": cents(form.get("opening_consignment"), "Opening balance",
                                                   negative=True) or 0,
                "shop_account_name": (form.get("shop_account_name") or "").strip() or money.DEFAULTS["shop_account_name"],
                "consignment_account_name": (form.get("consignment_account_name") or "").strip()
                or money.DEFAULTS["consignment_account_name"],
                "label_format": form.get("label_format") if form.get("label_format") in ("thermal", "avery5160")
                else "thermal",
                "agreement": (form.get("agreement") or "").strip() or money.DEFAULTS["agreement"],
            }
        except (ops.OpError, ValueError) as e:
            return back("/settings", error=str(e), request=request)
        for k, v in values.items():
            db.set_setting(k, v)
        db.audit(user, "settings_saved")
        return back("/settings", "Settings saved.", request=request)

    @app.get("/users")
    def users(request: Request):
        staff(request, "users")
        return render(request, "users.html", {"rows": db.list_users(staff_only=True),
                                              "invite_link": request.session.pop("invite_link", None)})

    @app.post("/users")
    def user_create(request: Request, name: str = Form(...), email: str = Form(...), role: str = Form("staff")):
        actor = staff(request, "users")
        if role not in ("owner", "staff") or "@" not in email:
            return back("/users", error="Enter a name, an email and a role.", request=request)
        if db.get_user_by_email(email):
            return back("/users", error="That email already has an account.", request=request)
        uid = db.create_user(email, name, role)
        request.session["invite_link"] = f"{base_url(request)}/invite/{auth.create_invite_token(uid)}"
        db.audit(actor, "user_created", detail=f"{email} {role}")
        return back("/users", "Account created. Send the link below so they can set a password.", request=request)

    @app.post("/users/{uid}/toggle")
    def user_toggle(request: Request, uid: int):
        actor = staff(request, "users")
        target = db.get_user(uid)
        if not target or target["id"] == actor["id"]:
            return back("/users", error="You cannot turn off your own account.", request=request)
        db.update_user(uid, active=0 if target["active"] else 1)
        db.bump_session_version(uid)
        db.audit(actor, "user_toggled", detail=target["email"])
        return back(f"/consignors/{target['consignor_id']}" if target["consignor_id"] else "/users", "Updated.",
                    request=request)

    @app.get("/activity")
    def activity(request: Request):
        staff(request)
        return render(request, "activity.html", {"rows": db.list_audit(500)})

    # --- consignor portal --------------------------------------------------

    @app.get("/me")
    def me(request: Request):
        user, c = consignor_user(request)
        return render(request, "me.html", {
            "c": c, "bal": ledger.consignor_balance(c["id"]), "items": db.list_items(consignor_id=c["id"]),
            "proposals": db.pending_proposals("shop", c["id"]), "counts": db.item_counts(c["id"]),
            "sales": db.list_sales(consignor_id=c["id"], include_void=False, limit=10),
            "payouts": db.list_payouts(c["id"]), "rate_text": _rate_text(c)})

    @app.post("/me/agree")
    def me_agree(request: Request):
        user, c = consignor_user(request)
        if not c["agreement_accepted_at"]:
            db.update_consignor(c["id"], agreement_accepted_at=db.now())
            db.audit(user, "agreement_accepted", c["id"])
        return back("/me", "Thank you. The consignment agreement is on file.", request=request)

    @app.get("/me/items/{iid}")
    def me_item(request: Request, iid: int):
        user, c = consignor_user(request)
        item = own_item(c["id"], iid)
        price = item["price_cents"] or item["asking_cents"] or 0
        rate = money.commission_pct(item, price)
        return render(request, "me_item.html", {
            "c": c, "item": item, "photos": db.list_photos(iid), "proposals": db.list_proposals(iid),
            "pending": db.pending_proposal(iid), "rate": rate,
            "sales": db.list_sales(item_id=iid, include_void=False),
            "examples": {ch: money.split(price, money.estimate_fees(ch, price), rate)
                         for ch in ("instore", "ebay", "whatnot")} if price else {}})

    @app.post("/me/items/{iid}/propose")
    def me_propose(request: Request, iid: int, price: str = Form(""), note: str = Form("")):
        user, c = consignor_user(request)
        item = own_item(c["id"], iid)
        try:
            ops.propose_price(item, "consignor", cents(price, "Price", required=True), note.strip() or None, user)
        except ops.OpError as e:
            return back(f"/me/items/{iid}", error=str(e), request=request)
        return back(f"/me/items/{iid}", "Sent to the shop.", request=request)

    @app.post("/me/items/{iid}/proposals/{pid}")
    def me_answer(request: Request, iid: int, pid: int, answer: str = Form(...)):
        user, c = consignor_user(request)
        item = own_item(c["id"], iid)
        p = next((p for p in db.list_proposals(iid) if p["id"] == pid), None)
        if not p:
            raise HTTPException(404)
        try:
            if answer == "withdraw":
                if p["proposed_by"] != "consignor" or p["status"] != "pending":
                    raise ops.OpError("That proposal cannot be withdrawn.")
                db.decide_proposal(pid, "withdrawn")
            else:
                if p["proposed_by"] != "shop":
                    raise ops.OpError("The shop answers your proposals.")
                # A Square hiccup is the shop's to fix; it shows on their item page, not the consignor's.
                ops.answer_proposal(item, p, answer == "accept", user, sq())
        except ops.OpError as e:
            return back(f"/me/items/{iid}", error=str(e), request=request)
        return back(f"/me/items/{iid}", "Thanks, the shop has your answer.", request=request)

    @app.get("/me/submit")
    def me_submit_form(request: Request):
        user, c = consignor_user(request)
        return render(request, "me_submit.html", {"c": c})

    @app.post("/me/submit")
    async def me_submit(request: Request):
        user, c = consignor_user(request)
        if not c["agreement_accepted_at"]:
            return back("/me", error="Please read and accept the consignment agreement first.", request=request)
        form = await request.form()
        try:
            fields = item_fields(form)
            fields.pop("commission_pct", None)
            fields.pop("location", None)
            fields.pop("tcgplayer_id", None)
        except ops.OpError as e:
            return back("/me/submit", error=str(e), request=request)
        iid = db.create_item(consignor_id=c["id"], status="submitted", submitted_by="consignor", **fields)
        item = db.get_item(iid)
        db.audit(user, "item_submitted", c["id"], iid, item["title"])
        warning = await save_photos(item, form.getlist("photos"), user)
        return back(f"/me/items/{iid}", "Submitted. Bring the item to the shop; we'll check it in and confirm "
                    "the price.", warning, request)

    @app.post("/me/items/{iid}/photos")
    async def me_photos(request: Request, iid: int, photos: list[UploadFile] = File(default=[])):
        user, c = consignor_user(request)
        item = own_item(c["id"], iid)
        if item["status"] != "submitted":
            return back(f"/me/items/{iid}", error="Photos can be added until the shop checks the item in.",
                        request=request)
        warning = await save_photos(item, photos, user)
        return back(f"/me/items/{iid}", "Photos added." if not warning else None, warning, request)

    @app.get("/me/statement")
    def me_statement(request: Request):
        user, c = consignor_user(request)
        return render(request, "me_statement.html", {"c": c, "rows": list(reversed(ledger.statement(c["id"]))),
                                                     "bal": ledger.consignor_balance(c["id"])})

    @app.get("/me/statement.csv")
    def me_statement_csv(request: Request):
        user, c = consignor_user(request)
        return statement_csv(c["id"])

    return app


def _date(value: str) -> str:
    value = (value or "").strip()
    try:
        return date.fromisoformat(value).isoformat() if value else db.today()
    except ValueError:
        raise ops.OpError("Dates must look like 2026-10-15.") from None


def _int(value, label: str) -> int:
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        raise ops.OpError(f"{label} must be a whole number of days.") from None
    if n < 0:
        raise ops.OpError(f"{label} cannot be negative.")
    return n


def _rate_text(c: dict) -> str:
    if c["commission_pct"] is not None:
        return f"{c['commission_pct']:g}% of each sale"
    tiers = money.setting("commission_tiers")
    parts = []
    for n, (start, rate) in enumerate(tiers):
        nxt = tiers[n + 1][0] if n + 1 < len(tiers) else None
        parts.append(f"{rate:g}% on sales ${start:,}" + (f"–${nxt:,}" if nxt else " and up"))
    return "; ".join(parts)
