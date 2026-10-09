"""Routes for the vendor certificate portal (see certapp.portal)."""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from .. import auth, db, portal
from ..states import STATES, normalize_state


SETUP_ERRORS = {
    "pdf": "That file is not a PDF. Upload the signed certificate as a PDF.",
    "entity": "Give the entity a name.",
    "project": "A project needs a name, a valid state, an entity and a project type.",
    "category": "Give the category a name.",
    "rule": "A rule needs a category, and an exempt rule needs a certificate type.",
    "certificate": "Choose a PDF, an entity and a certificate type.",
}


def register(app, *, render, signed_in, need, back, forbidden):
    """Attach the portal routes. Helpers come from create_app so the same checks apply."""

    def portal_engagement(user: dict, eid: int | None) -> int:
        """Vendors use their own client's portal; firm staff may try any portal they manage."""
        if user["role"] == "vendor":
            if not user.get("engagement_id"):
                raise forbidden()
            return user["engagement_id"]
        if eid is None:
            raise HTTPException(404, "Not found")
        need(user, eid, "portal_manage")
        return eid

    def form_ctx(eid: int, user: dict, error: str | None = None) -> dict:
        entities = {e["id"]: e for e in portal.listing("entity", eid)}
        projects = portal.listing("project", eid)
        for p in projects:
            p["entity_name"] = entities.get(p["entity_id"], {}).get("name", "")
        projects.sort(key=lambda p: (p["entity_name"], p["name"]))
        return {"e": db.get_engagement(eid), "projects": projects, "categories": portal.listing("category", eid),
                "history": portal.requests(eid, user_id=user["id"], limit=20), "error": error,
                "contact": portal.contact(eid), "preview": user["role"] != "vendor"}

    def request_or_404(user: dict, rid: int) -> tuple[int, dict]:
        """A vendor sees only their own requests; firm staff see requests of portals they manage."""
        with db.connect() as conn:
            row = conn.one("SELECT engagement_id, user_id FROM portal_request WHERE id = ?", (rid,))
        if not row:
            raise HTTPException(404, "Not found")
        eid = row["engagement_id"]
        if user["role"] == "vendor":
            if row["user_id"] != user["id"] or user.get("engagement_id") != eid:
                raise HTTPException(404, "Not found")
        else:
            if not auth.can_access_engagement(user, eid):
                raise HTTPException(404, "Not found")
            if not auth.can(user, "portal_manage"):
                raise forbidden()
        return eid, portal.get_request(eid, rid)

    # --- vendor side -------------------------------------------------------

    @app.get("/portal")
    def portal_home(request: Request, user: dict = Depends(signed_in)):
        if user["role"] != "vendor":
            return back("/")
        eid = portal_engagement(user, None)
        return render(request, "portal_home.html", form_ctx(eid, user))

    @app.get("/e/{eid}/portal/try")
    def portal_try(request: Request, eid: int, user: dict = Depends(signed_in)):
        if user["role"] == "vendor":
            raise forbidden()                               # the preview is for the firm only
        portal_engagement(user, eid)
        return render(request, "portal_home.html", form_ctx(eid, user))

    @app.post("/portal/check")
    async def portal_check(request: Request, user: dict = Depends(signed_in)):
        form = await request.form()
        if user["role"] != "vendor" and not form.get("eid"):
            raise HTTPException(404, "Not found")
        eid = portal_engagement(user, int(form["eid"]) if user["role"] != "vendor" else None)
        project = portal.get("project", eid, int(form.get("project_id") or 0))
        categories = {c["id"]: c for c in portal.listing("category", eid)}
        items = []
        for desc, cat in zip(form.getlist("description"), form.getlist("category_id")):
            desc = desc.strip()
            if not desc and not cat:
                continue
            if not desc or not cat or int(cat) not in categories:
                return render(request, "portal_home.html",
                              form_ctx(eid, user, "Give every item a description and choose its category."), 400)
            items.append({"description": desc[:300], "category_id": int(cat)})
        if not project:
            return render(request, "portal_home.html", form_ctx(eid, user, "Choose the project you are selling to."), 400)
        if not items:
            return render(request, "portal_home.html", form_ctx(eid, user, "List at least one item."), 400)
        decision = portal.decide(project, items, categories, portal.listing("rule", eid),
                                 portal.listing("certificate", eid))
        reference = (form.get("reference") or "").strip()[:100]
        rid = portal.record_request(eid, user, project["id"], reference, items, decision)
        db.audit(user, "portal_request", eid,
                 f"#{rid} {project['name']}: {decision.status}, {len(items)} item(s)")
        return back(f"/portal/r/{rid}")

    @app.get("/portal/r/{rid}")
    def portal_result(request: Request, rid: int, user: dict = Depends(signed_in)):
        eid, req = request_or_404(user, rid)
        return render(request, "portal_result.html", {
            "e": db.get_engagement(eid), "req": req, "contact": portal.contact(eid),
            "project": portal.get("project", eid, req["project_id"] or 0),
            "outcomes": portal.OUTCOMES, "preview": user["role"] != "vendor"})

    @app.get("/portal/r/{rid}/cert/{cid}")
    def portal_certificate(rid: int, cid: int, user: dict = Depends(signed_in)):
        eid, req = request_or_404(user, rid)
        if cid not in {c["id"] for c in req["result"]["certificates"]}:
            raise HTTPException(404, "Not found")           # only certificates this request was given
        cert = portal.get("certificate", eid, cid)
        if not cert or not Path(cert["storage_path"]).exists():
            raise HTTPException(404, "Not found")
        db.audit(user, "certificate_issued", eid, f"request #{rid}: {cert['label']}")
        return FileResponse(cert["storage_path"], media_type="application/pdf", filename=cert["filename"])

    # --- firm setup --------------------------------------------------------

    @app.get("/e/{eid}/portal")
    def portal_setup(request: Request, eid: int, user: dict = Depends(signed_in)):
        e = need(user, eid, "portal_manage")
        lists = {k: portal.listing(k, eid) for k in portal.TABLES}
        names = {k: {r["id"]: r for r in lists[k]} for k in ("entity", "category")}
        return render(request, "portal_setup.html", {
            "e": e, **lists, "names": names, "contact": portal.contact(eid),
            "error": SETUP_ERRORS.get(request.query_params.get("error", "")),
            "requests": portal.requests(eid, limit=50), "project_types": portal.PROJECT_TYPES,
            "cert_types": portal.CERT_TYPES, "outcomes": portal.OUTCOMES, "STATES": STATES,
            "vendors": [u for u in db.list_users(eid) if u["role"] == "vendor"]})

    def state_or_any(value: str) -> str:
        value = (value or "").strip().upper()
        return "*" if value in ("*", "", "ANY") else (normalize_state(value) or "*")

    @app.post("/e/{eid}/portal/add/{kind}")
    async def portal_add(request: Request, eid: int, kind: str, user: dict = Depends(signed_in),
                         file: UploadFile | None = File(None)):
        need(user, eid, "portal_manage")
        if kind not in portal.TABLES:
            raise HTTPException(404, "Not found")
        f = await request.form()
        text = lambda k: (f.get(k) or "").strip()
        if kind == "entity" and text("name"):
            portal.add("entity", eid, name=text("name"), home_state=state_or_any(text("home_state")),
                       notes=text("notes"))
        elif kind == "project" and text("name") and portal.get("entity", eid, int(text("entity_id") or 0)) \
                and normalize_state(text("state")) and text("project_type") in portal.PROJECT_TYPES:
            portal.add("project", eid, entity_id=int(text("entity_id")), name=text("name"), number=text("number"),
                       state=normalize_state(text("state")), project_type=text("project_type"))
        elif kind == "category" and text("name"):
            portal.add("category", eid, name=text("name"), examples=text("examples"))
        elif kind == "rule" and portal.get("category", eid, int(text("category_id") or 0)) \
                and text("outcome") in portal.OUTCOMES \
                and (text("project_type") in portal.PROJECT_TYPES or text("project_type") == "*") \
                and (text("outcome") != "exempt" or text("cert_type") in portal.CERT_TYPES):
            portal.add("rule", eid, category_id=int(text("category_id")), state=state_or_any(text("state")),
                       project_type=text("project_type"), outcome=text("outcome"),
                       cert_type=text("cert_type") if text("outcome") == "exempt" else None, note=text("note"))
        elif kind == "certificate" and file is not None and file.filename \
                and portal.get("entity", eid, int(text("entity_id") or 0)) and text("cert_type") in portal.CERT_TYPES:
            data = await file.read()
            if not data.startswith(b"%PDF"):
                return back(f"/e/{eid}/portal?error=pdf#certificate")
            folder = db.files_dir() / "portal" / str(eid)
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{secrets.token_hex(8)}.pdf"
            path.write_bytes(data)
            portal.add("certificate", eid, entity_id=int(text("entity_id")), state=state_or_any(text("state")),
                       cert_type=text("cert_type"), label=text("label") or Path(file.filename).stem,
                       filename=Path(file.filename).name, storage_path=str(path), expires=text("expires") or None)
        else:
            return back(f"/e/{eid}/portal?error={kind}#{kind}")
        db.audit(user, "portal_setup_added", eid, kind)
        return back(f"/e/{eid}/portal#{kind}")

    @app.post("/e/{eid}/portal/delete/{kind}/{item_id}")
    def portal_delete(eid: int, kind: str, item_id: int, user: dict = Depends(signed_in)):
        need(user, eid, "portal_manage")
        if kind not in portal.TABLES:
            raise HTTPException(404, "Not found")
        row = portal.remove(kind, eid, item_id)
        if row is None:
            raise HTTPException(404, "Not found")
        if kind == "certificate":
            Path(row["storage_path"]).unlink(missing_ok=True)
        db.audit(user, "portal_setup_removed", eid, f"{kind} #{item_id}")
        return back(f"/e/{eid}/portal#{kind}")

    @app.post("/e/{eid}/portal/contact")
    def portal_contact(eid: int, name: str = Form(""), email: str = Form(""), phone: str = Form(""),
                       message: str = Form(""), user: dict = Depends(signed_in)):
        need(user, eid, "portal_manage")
        portal.save_contact(eid, name.strip(), email.strip(), phone.strip(), message.strip())
        db.audit(user, "portal_contact_saved", eid)
        return back(f"/e/{eid}/portal#contact")
