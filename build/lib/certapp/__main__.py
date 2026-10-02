"""Command line: run the web app, or process a folder straight to Excel.

    python -m certapp serve [--port 8000] [--share]
    python -m certapp run "Certificates.zip" --client "Additive Manufacturing, LLC" -o schedule.xlsx
    python -m certapp create-admin --email you@firm.com --name "Your Name" [--microsoft]
    python -m certapp sign-in-link --email someone@firm.com [--base-url https://certs.firm.com]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="certapp")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="run the web app")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--share", action="store_true",
                       help="let other computers on the same office network open the app")

    run = sub.add_parser("run", help="read certificates and write the Excel schedule")
    run.add_argument("inputs", nargs="+", help="PDF files, .zip archives, or folders")
    run.add_argument("--client", required=True, help="client name (an existing engagement is reused)")
    run.add_argument("--batch", default=None, help="batch label (defaults to the first input's name)")
    run.add_argument("-o", "--output", default=None, help="output .xlsx path")

    admin = sub.add_parser("create-admin", help="create a firm administrator (first setup or recovery)")
    admin.add_argument("--email", required=True)
    admin.add_argument("--name", required=True)
    admin.add_argument("--microsoft", action="store_true", help="sign in with Microsoft 365 instead of a password")

    link = sub.add_parser("sign-in-link", help="print a one-time link for a password user to set a new password")
    link.add_argument("--email", required=True)
    link.add_argument("--base-url", default=None, help="the app's address (defaults to CERTAPP_BASE_URL or localhost)")

    args = parser.parse_args(argv)

    if args.cmd in ("create-admin", "sign-in-link"):
        import os
        from . import auth, db
        db.init()
        base = (args.base_url if args.cmd == "sign-in-link" and args.base_url else
                os.environ.get("CERTAPP_BASE_URL", "http://127.0.0.1:8000")).rstrip("/")
        if args.cmd == "create-admin":
            if db.get_user_by_email(args.email):
                print(f"A user with {args.email} already exists.")
                return 1
            uid = db.create_user(args.email, args.name, "firm_admin",
                                 "microsoft" if args.microsoft else "password")
            db.audit(None, "user_created", detail=f"{args.email} as firm administrator (command line)")
            if args.microsoft:
                print(f"Created {args.email}. They sign in with Microsoft 365.")
            else:
                print(f"Created {args.email}. Send them this link to set a password (works once, 7 days):")
                print(f"  {base}/invite/{auth.create_invite_token(uid)}")
            return 0
        user = db.get_user_by_email(args.email)
        if not user or user["auth_method"] != "password":
            print(f"No password user with {args.email}.")
            return 1
        db.audit(None, "invite_link_created", user.get("engagement_id"), f"{args.email} (command line)")
        print(f"One-time link for {args.email} (works once, 7 days):")
        print(f"  {base}/invite/{auth.create_invite_token(user['id'])}")
        return 0

    if args.cmd == "serve":
        import uvicorn
        from .web.app import create_app
        host = "0.0.0.0" if args.share else args.host
        print(f"\nOn this computer, open:  http://127.0.0.1:{args.port}")
        if args.share:
            print(f"Teammates on the same network open:  http://{_lan_address()}:{args.port}")
            print("Anyone on this network can open it while this window is running. There is no login yet,")
            print("so share client data this way only on a trusted office network.")
        print("Keep this window open while you use the app. Close it to stop.\n")
        uvicorn.run(create_app(), host=host, port=args.port, log_level="warning")
        return 0

    from . import db
    from .export import build_workbook
    from .extraction import default_extractor
    from .ingest import ingest
    from .pipeline import process_engagement
    from .rules import Policy, score_engagement

    db.init()
    existing = [e for e in db.list_engagements() if e["client_name"] == args.client]
    eid = existing[0]["id"] if existing else db.create_engagement(args.client)

    uploads = []
    for item in args.inputs:
        p = Path(item)
        files = sorted(p.rglob("*")) if p.is_dir() else [p]
        uploads += [(f.name, f.read_bytes()) for f in files if f.is_file()]
    report = ingest(eid, uploads, args.batch or Path(args.inputs[0]).stem)
    print(f"Received {report.received} files: {len(report.added)} new, "
          f"{len(report.duplicates_linked)} byte-identical copies, {len(report.already_on_file)} already on file.")

    extractor = default_extractor()
    print(f"Reading with the {extractor.name} extractor ...")
    process_engagement(eid, extractor)

    e = dict(db.get_engagement(eid))
    policy = Policy.from_json(e["policy_json"])
    result = score_engagement([dict(r) for r in db.list_certificates(eid)], policy,
                              db.overrides_for_engagement(eid))
    out = Path(args.output or f"{args.client.replace(' ', '_')}_certificate_schedule.xlsx")
    out.write_bytes(build_workbook(e, result, policy, [dict(b) for b in db.list_batches(eid)]))
    counts = result.flag_counts()
    print(f"{len(result.rows)} schedule rows across {len(result.by_state())} states; "
          f"{counts.get('follow_up', 0)} client follow-ups, {counts.get('review', 0)} reviewer checks.")
    print(f"Wrote {out}")
    return 0


def _lan_address() -> str:
    """This computer's address on the local network (no traffic is sent)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        s.close()


if __name__ == "__main__":
    sys.exit(main())
