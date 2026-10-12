"""Read every pending certificate in an engagement."""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor

from . import db
from .extraction.schema import Extraction, Reading
from .ingest import read_file

log = logging.getLogger(__name__)


def process_engagement(eid: int, extractor, workers: int | None = None) -> int:
    pending = db.claim_pending(eid)
    workers = workers or int(os.environ.get("CERTAPP_WORKERS", "4"))

    def run(cert) -> None:
        try:
            reading = extractor.extract(read_file(cert["storage_path"]), cert["filename"],
                                        json.loads(cert["filename_json"]))
            save_reading(cert["id"], extractor.name, reading)
        except Exception as exc:  # one bad file must not stop the batch
            log.exception("extraction failed for %s", cert["filename"])
            db.save_error(cert["id"], f"{type(exc).__name__}: {exc}")

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(run, pending))
    return len(pending)


def page_label(pages: list[int]) -> str | None:
    """[3, 4, 5] -> "3-5", [1, 3] -> "1, 3"."""
    pages = sorted(set(pages))
    if not pages:
        return None
    if pages == list(range(pages[0], pages[-1] + 1)):
        return str(pages[0]) if len(pages) == 1 else f"{pages[0]}-{pages[-1]}"
    return ", ".join(map(str, pages))


def save_reading(cid: int, extractor_name: str, reading: Reading | Extraction) -> list[int]:
    """Save a reading; a file holding several certificates becomes one row per certificate."""
    if isinstance(reading, Extraction):            # extractors that read one certificate per file
        reading = Reading(certificates=[reading], other_pages=[])
    if not reading.certificates:
        what = "; ".join(f"pages {page_label(o.pages)}: {o.description}" for o in reading.other_pages)
        raise ValueError("No certificate found in this file" + (f" ({what})" if what else ""))
    others = json.dumps([o.model_dump() for o in reading.other_pages]) if reading.other_pages else None
    several = len(reading.certificates) > 1
    return db.save_reading(cid, extractor_name, [c.model_dump_json() for c in reading.certificates],
                           [page_label(c.source_pages) if several or reading.other_pages else None
                            for c in reading.certificates], others)
