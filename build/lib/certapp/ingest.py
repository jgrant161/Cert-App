"""Bring certificate files into an engagement, one batch per upload.

A batch is whatever the client sent this time ("9.30.26 Certs.zip"). Files
already in the engagement are recognised by content hash, so re-sending a
folder that overlaps an earlier one only adds what is new.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from . import db
from .filenames import parse_filename


@dataclass
class IntakeReport:
    batch_id: int
    received: int = 0
    added: list[str] = field(default_factory=list)
    duplicates_linked: list[str] = field(default_factory=list)   # same bytes, new name
    already_on_file: list[str] = field(default_factory=list)     # same bytes, same name
    skipped: list[str] = field(default_factory=list)             # not PDFs


def _iter_files(name: str, data: bytes):
    """Yield (filename, bytes) for a PDF or every PDF inside a (nested) zip."""
    if name.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                inner = PurePosixPath(info.filename)
                if inner.name.startswith(".") or "__MACOSX" in inner.parts:
                    continue
                yield from _iter_files(inner.name, zf.read(info))
    else:
        yield PurePosixPath(name).name, data


def ingest(eid: int, uploads: list[tuple[str, bytes]], label: str) -> IntakeReport:
    bid = db.create_batch(eid, label)
    report = IntakeReport(batch_id=bid)
    store = db.files_dir() / str(eid)
    store.mkdir(parents=True, exist_ok=True)

    for upload_name, upload_bytes in uploads:
        for filename, data in _iter_files(upload_name, upload_bytes):
            report.received += 1
            if not filename.lower().endswith(".pdf"):
                report.skipped.append(filename)
                continue
            sha = hashlib.sha256(data).hexdigest()
            existing = db.find_by_hash(eid, sha)
            if any(e["filename"] == filename for e in existing):
                report.already_on_file.append(filename)
                continue
            path = store / f"{sha[:16]}.pdf"
            if not path.exists():
                path.write_bytes(data)
            original = existing[0]["id"] if existing else None
            db.add_certificate(eid, bid, filename, sha, str(path),
                               parse_filename(filename).as_dict(), duplicate_of=original)
            (report.duplicates_linked if original else report.added).append(filename)

    db.update_batch_counts(bid, report.received, len(report.added) + len(report.duplicates_linked),
                           len(report.already_on_file))
    return report


def read_file(path: str) -> bytes:
    return Path(path).read_bytes()
