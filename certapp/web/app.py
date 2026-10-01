"""Web front end: engagements, uploads, the review workspace, and exports."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import assistant, db
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


def create_app(extractor=None, ask_fn=None) -> FastAPI:
    db.init()
    app = FastAPI(title="Certificate Review")
    app.state.extractor = extractor or default_extractor()
    app.state.ask = ask_fn or assistant.ask
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def engagement_or_404(eid: int) -> dict:
        e = db.get_engagement(eid)
        if not e:
            raise HTTPException(404, "Engagement not found")
        return dict(e)

    def score(eid: int):
        e = engagement_or_404(eid)
        policy = Policy.from_json(e["policy_json"])
        certs = [dict(r) for r in db.list_certificates(eid)]
        result = score_engagement(certs, policy, db.overrides_for_engagement(eid))
        return e, policy, result

    def back(url: str) -> RedirectResponse:
        return RedirectResponse(url, status_code=303)

    # --- engagements -------------------------------------------------------

    @app.get("/")
    def index(request: Request):
        return templates.TemplateResponse(request, "index.html", {
            "engagements": db.list_engagements(), "ai": ai_available(),
            "extractor": app.state.extractor.name})

    @app.post("/engagements")
    def create(client_name: str = Form(...), seller_name: str = Form("")):
        eid = db.create_engagement(client_name.strip(), seller_name.strip() or None)
        return back(f"/e/{eid}")

    @app.get("/e/{eid}")
    def engagement(request: Request, eid: int, tab: str = "schedule", q: str = "", state: str = ""):
        e, policy, result = score(eid)
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
        return templates.TemplateResponse(request, "engagement.html", {
            "e": e, "policy": policy, "labels": POLICY_LABELS, "result": result, "rows": rows,
            "statuses": statuses, "follow": follow, "tab": tab, "q": q, "state": state,
            "batches": db.list_batches(eid), "qa": db.list_qa(eid), "ai": ai_available(),
            "extractor": app.state.extractor.name,
            "busy": statuses["pending"] + statuses["processing"] > 0,
        })

    @app.post("/e/{eid}/upload")
    async def upload(eid: int, background: BackgroundTasks, label: str = Form(""),
                     files: list[UploadFile] = File(...)):
        engagement_or_404(eid)
        uploads = [(f.filename or "upload.pdf", await f.read()) for f in files]
        label = label.strip() or (Path(uploads[0][0]).stem if len(uploads) == 1 else f"Upload {date.today()}")
        ingest(eid, uploads, label)
        background.add_task(process_engagement, eid, app.state.extractor)
        return back(f"/e/{eid}?tab=certificates")

    @app.post("/e/{eid}/process")
    def process(eid: int, background: BackgroundTasks):
        engagement_or_404(eid)
        background.add_task(process_engagement, eid, app.state.extractor)
        return back(f"/e/{eid}?tab=certificates")

    @app.post("/e/{eid}/policy")
    async def policy(request: Request, eid: int):
        engagement_or_404(eid)
        form = await request.form()
        p = Policy()
        for key in POLICY_LABELS:
            setattr(p, key, form.get(key) == "on")
        try:
            p.expiring_soon_days = int(form.get("expiring_soon_days") or 90)
        except ValueError:
            pass
        db.save_policy(eid, p.as_dict())
        return back(f"/e/{eid}?tab=policy")

    @app.get("/e/{eid}/export.xlsx")
    def export(eid: int):
        e, policy, result = score(eid)
        data = build_workbook(e, result, policy, [dict(b) for b in db.list_batches(eid)])
        name = re.sub(r"[^A-Za-z0-9]+", "_", e["client_name"]).strip("_") or "client"
        return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{name}_certificate_schedule.xlsx"'})

    @app.post("/e/{eid}/ask")
    def ask(eid: int, question: str = Form(...)):
        e, policy, result = score(eid)
        if not ai_available():
            answer = "Follow-up questions need an Anthropic API key (set ANTHROPIC_API_KEY and restart)."
        else:
            history = [(r["question"], r["answer"]) for r in db.list_qa(eid)]
            try:
                answer = app.state.ask(question, assistant.engagement_context(e, result, policy), history)
            except Exception as exc:
                answer = f"Could not get an answer: {type(exc).__name__}: {exc}"
        db.add_qa(eid, question, answer)
        return back(f"/e/{eid}?tab=ask")

    # --- certificates ------------------------------------------------------

    @app.get("/e/{eid}/c/{cid}")
    def certificate(request: Request, eid: int, cid: int):
        e, policy, result = score(eid)
        match = next((c for c in result.certificates if c.cert["id"] == cid), None)
        if not match:
            raise HTTPException(404, "Certificate not found")
        ids = [c.cert["id"] for c in sorted(result.certificates, key=lambda c: c.cert["filename"].lower())]
        i = ids.index(cid)
        return templates.TemplateResponse(request, "certificate.html", {
            "e": e, "c": match, "fn": json.loads(match.cert["filename_json"]),
            "prev_id": ids[i - 1] if i > 0 else None,
            "next_id": ids[i + 1] if i + 1 < len(ids) else None,
        })

    @app.get("/e/{eid}/c/{cid}/pdf")
    def pdf(eid: int, cid: int):
        c = db.get_certificate(cid)
        if not c or c["engagement_id"] != eid:
            raise HTTPException(404)
        return FileResponse(c["storage_path"], media_type="application/pdf",
                            headers={"Content-Disposition": "inline"})

    @app.post("/e/{eid}/c/{cid}/override")
    def override(eid: int, cid: int, state: str = Form(...), decision: str = Form(...), note: str = Form("")):
        c = db.get_certificate(cid)
        if not c or c["engagement_id"] != eid:
            raise HTTPException(404)
        db.set_override(cid, state.upper(), None if decision == "clear" else decision, note.strip() or None)
        return back(f"/e/{eid}/c/{cid}")

    @app.post("/e/{eid}/c/{cid}/review")
    def review(eid: int, cid: int, review_status: str = Form(...), note: str = Form("")):
        c = db.get_certificate(cid)
        if not c or c["engagement_id"] != eid:
            raise HTTPException(404)
        db.set_review(cid, review_status, note.strip() or None)
        return back(f"/e/{eid}/c/{cid}")

    @app.post("/e/{eid}/c/{cid}/retry")
    def retry(eid: int, cid: int, background: BackgroundTasks):
        c = db.get_certificate(cid)
        if not c or c["engagement_id"] != eid:
            raise HTTPException(404)
        db.requeue(cid)
        background.add_task(process_engagement, eid, app.state.extractor)
        return back(f"/e/{eid}/c/{cid}")

    return app
