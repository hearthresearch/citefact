"""Orchestration: ingest -> citations -> quotes -> claims -> report artifacts.

Thin by design: every check is a pure function; this module only wires
typed inputs together and owns timing/cost accounting.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Optional

from citefact.checks.claims import check_claims
from citefact.checks.citations import check_citations
from citefact.checks.quotes import check_quotes
from citefact.citations import pick_parser, resolve_to_catalog
from citefact.ingest.bibliography import load_bibtex, to_catalog
from citefact.ingest.convert import convert_pdf, pdf_sha256
from citefact.ingest.manuscript import load_manuscript
from citefact.ingest.pdfs import match_pdfs
from citefact.ingest.zotero import load_zotero_collection
from citefact.models import SEVERITY_ORDER, line_of
from citefact.progress import ProgressEvent, ProgressFn
from citefact.report.json_out import build_report

ALL_LEVELS = ["citations", "quotes", "claims"]


def _citation_inventory(text, matched) -> list[dict]:
    """Every in-text citation found, grouped by (author, year, resolution),
    with occurrence count and line numbers. This is the report's
    citation-centric inventory view."""
    grouped: dict = {}
    for m in matched:
        key = (m.citation.author_string.lower(), str(m.citation.year), m.paper_id)
        entry = grouped.setdefault(key, {
            "citation": m.citation.raw,
            "author": m.citation.author_string,
            "year": m.citation.year,
            "source_id": m.paper_id,
            "confidence": round(m.confidence, 2),
            "count": 0,
            "lines": [],
        })
        entry["count"] += 1
        line = line_of(text, m.citation.start)
        if line not in entry["lines"]:
            entry["lines"].append(line)
    return list(grouped.values())


def exit_code_for(findings: list[dict[str, Any]], fail_on: str) -> int:
    if fail_on == "none":
        return 0
    threshold = SEVERITY_ORDER[fail_on]
    hit = any(SEVERITY_ORDER[f["severity"]] >= threshold for f in findings)
    return 1 if hit else 0


def run_check(
    manuscript_path: Path,
    *,
    bib_path: Optional[Path],
    pdf_dir: Optional[Path],
    cache_dir: Path,
    levels: list[str],
    model: Optional[str],
    zotero_collection: Optional[str] = None,
    force: bool = False,
    progress: ProgressFn,
) -> dict[str, Any]:
    started = time.monotonic()

    def say(message: str) -> None:
        progress(ProgressEvent(phase="load", message=message))

    say(f"Loading manuscript {manuscript_path.name}...")
    manuscript = load_manuscript(manuscript_path)
    if zotero_collection is not None:
        sources = load_zotero_collection(zotero_collection)
        with_pdf = sum(1 for s in sources.values() if s.pdf_path is not None)
        say(
            f"✓ Loaded {len(sources)} entries from Zotero collection "
            f"{zotero_collection!r} ({with_pdf} with PDF attachments)."
        )
    elif bib_path is not None:
        sources = load_bibtex(bib_path)
        say(f"✓ Loaded {len(sources)} bibliography entries.")
    else:
        raise ValueError("A bibliography source is required: --bib or --zotero-collection.")

    if pdf_dir is not None:
        # Fill only the gaps: Zotero attachment paths already set stay set.
        unmatched = {sid: s for sid, s in sources.items() if s.pdf_path is None}
        matched_pdfs = match_pdfs(unmatched, pdf_dir)
        say(f"✓ Matched {len(matched_pdfs)}/{len(unmatched)} sources to PDFs.")
        for sid, pdf in matched_pdfs.items():
            sources[sid].pdf_path = pdf

    to_convert = [s for s in sources.values()
                  if s.pdf_path is not None and s.text is None]
    if to_convert:
        say(f"Converting {len(to_convert)} PDFs "
            "(a cold Docling start makes the first one slower)...")
    for i, source in enumerate(to_convert, start=1):
        progress(ProgressEvent(
            phase="convert", message=source.pdf_path.name,
            current=i, total=len(to_convert),
        ))
        source.content_hash = pdf_sha256(source.pdf_path)
        source.text = convert_pdf(source.pdf_path, cache_dir, force=force)

    # Citation parsing runs once; all levels share the preprocessed text so
    # character offsets stay consistent (Zotero wrappers stripped here).
    parser = pick_parser(manuscript.text)
    text = parser.preprocess(manuscript.text)
    matched = resolve_to_catalog(parser.extract(text), to_catalog(sources))
    say(f"✓ Found {len(matched)} in-text citations.")

    findings = []
    claims_summary: dict[str, Any] | None = None
    partial = False
    if "citations" in levels:
        findings += check_citations(
            text, matched, sources,
            bibliography_is_library=zotero_collection is not None,
        )
    if "quotes" in levels:
        findings += check_quotes(text, matched, sources)
    cost = 0.0
    if "claims" in levels:
        if model is None:
            # Not `assert`: asserts are stripped under `python -O`, which
            # would let this silently fall through to check_claims(model=None).
            raise ValueError("claims level requires a model")
        claim_findings, claims_summary = check_claims(
            text, matched, sources, model=model, cache_dir=cache_dir, progress=progress,
        )
        findings += claim_findings
        cost = claims_summary["cost_usd"]
        partial = claims_summary["partial"]

    return build_report(
        manuscript, sources, findings,
        levels=levels, model=model if "claims" in levels else None,
        cost_usd=cost, duration_seconds=time.monotonic() - started,
        claims_summary=claims_summary, partial=partial,
        citations=_citation_inventory(text, matched),
    )
