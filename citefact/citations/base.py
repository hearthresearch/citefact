"""Protocol + data types for the citation-parser registry.

Every parser returns `Citation` records in the same shape regardless of
style. The style-agnostic matcher (`citations.matcher`) then resolves them
against the project's catalog. This keeps the LLM and the matcher unaware
of which parser ran.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class Citation:
    """An in-text citation span extracted from a manuscript.

    Fields:
        raw: The original citation text as it appears in the manuscript,
            e.g. "(Smith et al., 2023)". Useful for UI highlights and for
            giving the LLM the surface form when it assembles the claim.
        author_string: Human-readable author component, e.g. "Smith et al."
            Shown to the LLM when auto-match fails, so it can apply its own
            judgment.
        year: Publication year as integer when parseable, else the raw
            year token ("in press", "n.d.", "2023a").
        start / end: Character offsets of `raw` in the manuscript. Used
            for position-sensitive claim extraction and UI linking.
    """

    raw: str
    author_string: str
    year: int | str
    start: int
    end: int


@dataclass
class MatchedCitation:
    """A `Citation` after catalog resolution.

    `paper_id` is None when no catalog entry matched confidently. Callers
    decide what to do with unmatched citations (usually pass them to the
    LLM as unresolved so it can try a fallback).
    """

    citation: Citation
    paper_id: Optional[str]
    confidence: float  # 0..1; 0 when unmatched
    source: Optional[str] = None  # "corpus" | "reference" when available


class CitationParser(Protocol):
    """Interface every format-specific parser implements.

    Parsers stay small and testable by keeping exactly these responsibilities:

    1. `detect` — score a manuscript against this parser's style markers.
       The registry picks the highest-scoring parser.
    2. `preprocess` — normalize the input (strip markdown-link noise, unify
       punctuation). Default is identity; style-specific cleanup lives here.
    3. `extract` — walk the text and emit `Citation` records. No catalog
       work; this method never needs to know what papers exist.
    4. `resolve_references` — optional. Numbered styles (Vancouver, IEEE)
       parse the bibliography into `key -> (surname, year)` so the matcher
       can resolve `[23]` → `(smith, 2023)`. Author-date styles return {}.
    """

    name: str

    def detect(self, text: str) -> float:
        """Confidence in [0, 1] that the manuscript uses this style."""
        ...

    def preprocess(self, text: str) -> str:
        """Return a cleaned-up manuscript; default may be identity."""
        ...

    def extract(self, text: str) -> list[Citation]:
        """Find every in-text citation in the (already-preprocessed) text."""
        ...

    def resolve_references(self, text: str) -> dict[str, tuple[str, int | str]]:
        """For numbered styles: map the citation key to (surname, year).
        Author-date styles return an empty dict."""
        ...
