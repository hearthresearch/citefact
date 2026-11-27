"""Match PDFs (and saved web pages) in a folder to bibliography entries.

Strategy per PDF: exact filename-stem match against source ids first,
then fuzzy scoring of the bibliography title against PDF-embedded
metadata title and first-page text. Greedy one-to-one assignment by
descending score. Unresolvable PDFs are skipped; the corresponding
sources surface later as `missing_source` findings. A saved web page
(`.html`/`.htm`) is matched the same way, using its `<title>` and text.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pypdf import PdfReader
from rapidfuzz import fuzz

from citefact.ingest.web import SNAPSHOT_SUFFIXES, is_snapshot, snapshot_text, snapshot_title
from citefact.models import Source

log = logging.getLogger(__name__)

_MIN_TITLE_SCORE = 70


def _pdf_probe(path: Path) -> tuple[str, str]:
    """Return (embedded metadata title, first-page text). Never raises:
    a corrupt PDF probes as empty and simply won't match."""
    if is_snapshot(path):
        return snapshot_title(path), snapshot_text(path)[:4000]
    try:
        reader = PdfReader(path)
        meta_title = (reader.metadata.title or "") if reader.metadata else ""
        first_page = reader.pages[0].extract_text() or "" if reader.pages else ""
        return meta_title, first_page[:4000]
    except Exception as exc:  # pypdf raises a zoo of exception types
        log.warning("Could not read %s: %s", path.name, exc)
        return "", ""


def _score(source: Source, stem: str, meta_title: str, first_page: str) -> float:
    title = source.title.lower()
    if not title:
        return 0.0
    score = max(
        fuzz.token_set_ratio(title, meta_title.lower()),
        fuzz.partial_ratio(title, first_page.lower()),
    )
    # Require the year to corroborate when we know it.
    if source.year is not None and str(source.year) not in (first_page + stem + meta_title):
        score *= 0.5
    return score


def match_pdfs(sources: dict[str, Source], pdf_dir: Path) -> dict[str, Path]:
    # PDFs first, so a PDF wins over a saved page when both could match.
    pdfs = sorted((p for p in pdf_dir.iterdir()
                   if p.is_file() and p.suffix.lower() in (".pdf", *SNAPSHOT_SUFFIXES)),
                  key=lambda p: (is_snapshot(p), p.name))
    matched: dict[str, Path] = {}
    remaining = dict(sources)

    # Pass 1: filename stem == source id (case-insensitive).
    by_lower_id = {sid.lower(): sid for sid in remaining}
    unclaimed: list[Path] = []
    for pdf in pdfs:
        sid = by_lower_id.get(pdf.stem.lower())
        if sid is not None and sid in remaining:
            matched[sid] = pdf
            del remaining[sid]
        else:
            unclaimed.append(pdf)

    # Pass 2: fuzzy content match, greedy by descending score.
    candidates: list[tuple[float, str, Path]] = []
    for pdf in unclaimed:
        meta_title, first_page = _pdf_probe(pdf)
        for sid, source in remaining.items():
            s = _score(source, pdf.stem, meta_title, first_page)
            if s >= _MIN_TITLE_SCORE:
                candidates.append((s, sid, pdf))
    used_pdfs: set[Path] = set()
    for s, sid, pdf in sorted(candidates, reverse=True):
        if sid in matched or pdf in used_pdfs:
            continue
        matched[sid] = pdf
        used_pdfs.add(pdf)
    return matched
