"""BibTeX bibliography loading (RIS and Zotero arrive post-v0.1)."""

from __future__ import annotations

import re
from pathlib import Path

import bibtexparser
from bibtexparser.bparser import BibTexParser

from citefact.models import Source


def load_bibtex(path: Path) -> dict[str, Source]:
    parser = BibTexParser(common_strings=True, ignore_nonstandard_types=False)
    with path.open(encoding="utf-8") as fh:
        db = bibtexparser.load(fh, parser)
    sources: dict[str, Source] = {}
    for entry in db.entries:
        year_raw = entry.get("year", "")
        try:
            year: int | str | None = int(year_raw)
        except (TypeError, ValueError):
            year = year_raw or None
        sources[entry["ID"]] = Source(
            id=entry["ID"],
            title=re.sub(r"[{}]", "", entry.get("title", "")),
            authors=entry.get("author", ""),
            year=year,
        )
    return sources


def to_catalog(sources: dict[str, Source]) -> dict[str, dict]:
    """Shape sources for `citations.matcher.resolve_to_catalog`."""
    return {
        sid: {"authors": s.authors, "year": s.year, "title": s.title}
        for sid, s in sources.items()
    }
