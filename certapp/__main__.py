"""Command line: run the web app, or process a folder straight to Excel.

    python -m certapp serve [--port 8000]
    python -m certapp run "Certificates.zip" --client "Additive Manufacturing, LLC" -o schedule.xlsx
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

    run = sub.add_parser("run", help="read certificates and write the Excel schedule")
    run.add_argument("inputs", nargs="+", help="PDF files, .zip archives, or folders")
    run.add_argument("--client", required=True, help="client name (an existing engagement is reused)")
    run.add_argument("--batch", default=None, help="batch label (defaults to the first input's name)")
    run.add_argument("-o", "--output", default=None, help="output .xlsx path")

    args = parser.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn
        from .web.app import create_app
        uvicorn.run(create_app(), host=args.host, port=args.port)
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


if __name__ == "__main__":
    sys.exit(main())
