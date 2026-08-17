"""Style-agnostic resolution of `Citation` records to catalog paper_ids.

Matching strategy:
1. Filter catalog to entries with matching year (exact integer match; tolerant
   when a Citation's year is a non-numeric token like "in press").
2. Fuzzy-match the Citation's author_string against each candidate's
   first-author surname using rapidfuzz.
3. Accept the top match when its score clears a confidence floor.

This replaces the LLM's previous job of guessing paper_ids from titles.
Every matched citation comes with an explicit confidence; unmatched ones
fall through to the LLM for a last attempt.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from rapidfuzz import fuzz, process

from .base import Citation, MatchedCitation

# Score floor for a confident surname match. Chosen empirically so
# "Smith" vs "Smyth" (85) accepts but "Smith" vs "Jones" (20) doesn't.
# token_set_ratio handles "et al.", "&", "and", extra initials.
_MIN_SURNAME_SCORE = 80


def _first_author_surname(authors: Any) -> str:
    """Return the first author's surname, robust across stored formats.

    Mirrors claim_extraction._first_author_surname; duplicated here so the
    citations package stays import-independent. Handles:

    - BibTeX-ish "Simkute A., Surana A., …"           → "Simkute"
    - Zotero API "Bornmann Lutz, Mutz Rüdiger"        → "Bornmann"
    - BibTeX comma-inverted "Smith, J., Jones, K."    → "Smith"
    - Python list ["Floridi, L.", "Cowls, J."]        → "Floridi"
    """
    if not authors:
        return ""
    if isinstance(authors, list):
        authors = authors[0] if authors else ""
    if not isinstance(authors, str):
        return ""
    first_entry = authors.split(",", 1)[0].strip()
    if not first_entry:
        return ""
    tokens = first_entry.split()
    non_initials = [t for t in tokens if not (len(t) <= 4 and t.endswith("."))]
    return non_initials[0] if non_initials else tokens[0]


def _citation_surname(author_string: str) -> str:
    """Pull the first surname out of a citation's author_string.

    Examples:
        "Smith et al."           → "Smith"
        "Smith & Jones"          → "Smith"
        "Smith and Jones"        → "Smith"
        "van der Berg"           → "van"   (compound surname edge case,
                                            accepted — catalog side is
                                            equally imperfect)
    """
    if not author_string:
        return ""
    # Remove trailing "et al." and common conjunctions.
    cleaned = re.sub(r"\bet\s+al\.?", "", author_string, flags=re.IGNORECASE)
    cleaned = cleaned.split("&")[0]
    cleaned = re.split(r"\band\b", cleaned, flags=re.IGNORECASE)[0]
    # Author lists ("Lebovitz, Levina, and Lifshitz-Assaf"): keep only the
    # first surname, without a trailing comma.
    cleaned = cleaned.split(",")[0]
    cleaned = cleaned.strip(" ,")
    return cleaned.split()[0] if cleaned else ""


def _year_matches(citation_year: int | str, catalog_year: Any) -> bool:
    """Accept exact integer equality; fall back to substring when either
    side is non-numeric (handles "2023a", "in press", "n.d.")."""
    if catalog_year in (None, ""):
        return False
    try:
        return int(citation_year) == int(catalog_year)
    except (TypeError, ValueError):
        # At least one side wasn't a clean int. Compare as strings.
        return str(citation_year).strip() in str(catalog_year).strip()


def resolve_to_catalog(
    citations: Iterable[Citation],
    catalog: dict[str, dict[str, Any]],
) -> list[MatchedCitation]:
    """Resolve each citation to a paper_id from the catalog.

    Args:
        citations: iterable of `Citation` records (usually from a parser).
        catalog: paper_id → paper dict (mergeable corpus + references; pass
            the bibliography catalog from `ingest.bibliography.to_catalog`).

    Returns:
        List of `MatchedCitation` in the same order as the input. Unmatched
        citations carry `paper_id=None` and `confidence=0.0`.
    """
    # Pre-compute surnames once per catalog entry.
    catalog_entries = []
    for paper_id, paper in catalog.items():
        surname = _first_author_surname(paper.get("authors", ""))
        if not surname:
            continue
        catalog_entries.append(
            {
                "paper_id": paper_id,
                "surname": surname,
                "year": paper.get("year"),
                "source": paper.get("_source"),
            }
        )

    results: list[MatchedCitation] = []
    for cite in citations:
        cite_surname = _citation_surname(cite.author_string)
        if not cite_surname:
            results.append(MatchedCitation(citation=cite, paper_id=None, confidence=0.0))
            continue

        # Narrow to candidates with a matching year first — avoids
        # wasting fuzzy scoring on clearly wrong years.
        candidates = [e for e in catalog_entries if _year_matches(cite.year, e["year"])]
        if not candidates:
            results.append(MatchedCitation(citation=cite, paper_id=None, confidence=0.0))
            continue

        # Fuzzy-match citation surname against candidate surnames.
        surname_choices = {e["paper_id"]: e["surname"] for e in candidates}
        best = process.extractOne(
            cite_surname, surname_choices, scorer=fuzz.token_set_ratio
        )
        if best is None:
            results.append(MatchedCitation(citation=cite, paper_id=None, confidence=0.0))
            continue

        _surname, score, paper_id = best
        if score < _MIN_SURNAME_SCORE:
            results.append(MatchedCitation(citation=cite, paper_id=None, confidence=0.0))
            continue

        entry = next(e for e in candidates if e["paper_id"] == paper_id)
        results.append(
            MatchedCitation(
                citation=cite,
                paper_id=paper_id,
                confidence=score / 100.0,
                source=entry.get("source"),
            )
        )

    return results
