"""Read every pending certificate in an engagement."""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor

from . import db
from .ingest import read_file

log = logging.getLogger(__name__)


def process_engagement(eid: int, extractor, workers: int | None = None) -> int:
    pending = db.claim_pending(eid)
    workers = workers or int(os.environ.get("CERTAPP_WORKERS", "4"))

    def run(cert) -> None:
        try:
            ex = extractor.extract(read_file(cert["storage_path"]), cert["filename"],
                                   json.loads(cert["filename_json"]))
            db.save_extraction(cert["id"], extractor.name, ex.model_dump_json())
        except Exception as exc:  # one bad file must not stop the batch
            log.exception("extraction failed for %s", cert["filename"])
            db.save_error(cert["id"], f"{type(exc).__name__}: {exc}")

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(run, pending))
    return len(pending)
