"""Manuscript loading. v0.1: Markdown only (DOCX arrives in v0.2)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from citefact.models import Manuscript


def load_manuscript(path: Path) -> Manuscript:
    if path.suffix.lower() != ".md":
        raise ValueError(
            f"Unsupported manuscript format {path.suffix!r}: "
            "v0.1 supports Markdown (.md) only."
        )
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    return Manuscript(
        path=path,
        text=text,
        sha256=hashlib.sha256(raw).hexdigest(),
        words=len(text.split()),
    )
