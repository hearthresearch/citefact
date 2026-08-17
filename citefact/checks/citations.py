"""Citations check: do the in-text citations exist in the bibliography?. Deterministic; no LLM, no network."""

from __future__ import annotations

from citefact.citations.base import MatchedCitation
from citefact.models import Finding, Source, line_of


def check_citations(
    text: str,
    matched: list[MatchedCitation],
    sources: dict[str, Source],
    *,
    bibliography_is_library: bool = False,
) -> list[Finding]:
    findings: list[Finding] = []

    # orphan_citation: in-text citation with no bibliography entry.
    seen_orphans: set[tuple[str, str]] = set()
    for m in matched:
        if m.paper_id is not None:
            continue
        key = (m.citation.author_string.lower(), str(m.citation.year))
        if key in seen_orphans:
            continue
        seen_orphans.add(key)
        findings.append(Finding(
            level="citations", type="orphan_citation", severity="error",
            details={
                "citation": m.citation.raw,
                "location": {"line": line_of(text, m.citation.start)},
            },
        ))

    cited_ids = {m.paper_id for m in matched if m.paper_id is not None}

    # uncited_reference: bibliography entry never cited. From a curated
    # .bib this is a hygiene warning; from a Zotero collection (a reading
    # library, not the paper's reference list) it is normal and only
    # recorded as info.
    uncited_severity = "info" if bibliography_is_library else "warning"
    for sid in sources:
        if sid not in cited_ids:
            findings.append(Finding(
                level="citations", type="uncited_reference",
                severity=uncited_severity,
                details={"source_id": sid, "title": sources[sid].title},
            ))

    # missing_source: cited but no converted text, so the quote and claim
    # checks cannot run for it.
    for sid in sorted(cited_ids):
        source = sources.get(sid)
        if source is not None and source.text is None:
            findings.append(Finding(
                level="citations", type="missing_source", severity="warning",
                details={"source_id": sid, "title": source.title},
            ))

    return findings
