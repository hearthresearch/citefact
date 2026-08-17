"""APA parser (parenthetical + narrative) with Zotero-markdown support.

Patterns covered, all observed in the sample manuscript:

- `Author et al., [(Year)](zotero-url)`      — Zotero markdown, year-only link
- `[(Author et al., Year)](zotero-url)`      — Zotero markdown, full-cite link
- `Author [(Year)](zotero-url)`              — Zotero markdown, single author
- `Author & Coauthor [(Year)](zotero-url)`   — Zotero markdown, ampersand
- `Author (Year)`                            — plain narrative
- `(Author, Year)`                           — plain parenthetical
- `(A et al., Year; B et al., Year)`         — semicolon-separated group

The Zotero preprocess step strips `]()` noise so everything collapses to
the plain forms before extraction. This keeps the extraction regexes small.
"""

from __future__ import annotations

import re
from typing import Any

from .base import Citation

# `[(something)](https://www.zotero.org/google-docs/?XXXX)` wrapper. The `?XXXX`
# suffix is a Google Docs session id, opaque and useless for matching, so we
# unwrap to plain `(something)` before extracting citations.
_ZOTERO_MARKDOWN_LINK = re.compile(
    r"\[([^\]]+)\]\(https?://[^)]*zotero\.org/google-docs/[^)]*\)"
)

# Parenthetical citation: `(Author, Year)`, allowing multi-author strings
# like "Smith et al." and semicolon-separated groups.
_PARENTHETICAL = re.compile(
    r"""
    \(                                   # opening paren
    (?P<body>
        (?:[^()]+)                       # non-paren body
    )
    \)                                   # closing paren
    """,
    re.VERBOSE,
)

# A single author-year item inside a parenthetical group, separated by `;`.
# Handles "Smith, 2023", "Smith et al., 2023", "Smith & Jones, 2023",
# "van der Berg, 2023". Year is 4 digits with optional letter suffix ("2023a")
# or tokens like "in press" / "n.d.".
_AUTHOR_YEAR_ITEM = re.compile(
    r"""
    (?P<authors>
        [A-ZÀ-Ö][\w\-\u00C0-\u017F'.]+           # First surname (unicode-aware)
        (?:
            \s+et\s+al\.?                        # "et al."
          | \s*(?:&|and)\s*                      # "&" or "and"
            [A-ZÀ-Ö][\w\-\u00C0-\u017F'.]+       # Second surname
          | \s+[A-ZÀ-Ö][\w\-\u00C0-\u017F'.]+    # surname components ("van der Berg")
        )*
    )
    \s*,\s*                                      # separator before year
    (?P<year>\d{4}[a-z]?|in\s+press|n\.?\s*d\.?) # year with variants
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Narrative citation: `Author (Year)`, `Author et al. (Year)`,
# `A & B (Year)`, and full author lists `A, B(,) and C (Year)` (up to five
# comma-separated surnames ending in an `and`/`&` conjunction). Without the
# list form, "Lebovitz, Levina, and Lifshitz-Assaf (2021)" captured only the
# last surname, which then failed first-author resolution and produced a
# false orphan_citation. The surname must start with a capital letter; the
# optional `,` before the year paren covers the Zotero pattern
# "Author et al., (2023)" (after preprocess).
_NARRATIVE = re.compile(
    r"""
    (?P<authors>
        [A-ZÀ-Ö][\w\-\u00C0-\u017F']+            # First surname
        (?:
            \s+et\s+al\.?                        # "et al."
          | (?:\s*,\s*[A-ZÀ-Ö][\w\-\u00C0-\u017F']+){0,4}    # middle surnames
            \s*,?\s*(?:&|and)\s+[A-ZÀ-Ö][\w\-\u00C0-\u017F']+  # final conjunction
          | \s*(?:&|and)\s*[A-ZÀ-Ö][\w\-\u00C0-\u017F']+
        )?
    )
    \s*,?\s*                                     # optional trailing comma
    \(                                           # opening paren
    (?P<year>\d{4}[a-z]?)                        # year (narrative rarely uses "in press")
    \)                                           # closing paren
    """,
    re.VERBOSE,
)


class ApaParser:
    """APA parenthetical + narrative, with Zotero-markdown preprocess."""

    name = "apa"

    def detect(self, text: str) -> float:
        """Score based on parenthetical and narrative citation density.

        Rough heuristic: ratio of author-year matches to total word count,
        clamped to [0, 1]. Real APA manuscripts come in around 0.3-0.6;
        we don't need a tight calibration, only relative ranking against
        other parsers.
        """
        if not text:
            return 0.0
        stripped = self.preprocess(text)
        author_year_hits = len(_AUTHOR_YEAR_ITEM.findall(stripped))
        narrative_hits = len(_NARRATIVE.findall(stripped))
        word_count = max(1, len(stripped.split()))
        density = (author_year_hits + narrative_hits) / word_count * 100.0
        # 0.3 hits per 100 words = 100% confidence; below that scales linearly.
        return min(1.0, density / 0.3)

    def preprocess(self, text: str) -> str:
        """Strip Zotero Google Docs markdown-link wrappers.

        Replaces `[(anything)](zotero-url)` with `(anything)`, which normalizes
        all four Zotero patterns into the plain APA forms the regexes below
        already handle.
        """
        return _ZOTERO_MARKDOWN_LINK.sub(r"\1", text)

    def extract(self, text: str) -> list[Citation]:
        """Return every in-text citation in the (already-preprocessed) text.

        Parenthetical citations may contain multiple author-year items
        separated by `;`; each becomes its own Citation so the matcher can
        resolve them independently. Narrative citations yield one Citation.
        """
        citations: list[Citation] = []

        # Parenthetical form. Walk each (...) group and try to split into
        # author-year items.
        for match in _PARENTHETICAL.finditer(text):
            body = match.group("body")
            group_start = match.start()
            # Look for author-year items inside this group. A group may
            # legitimately contain no citations (e.g. "(see Methods)") — skip.
            items = list(_AUTHOR_YEAR_ITEM.finditer(body))
            if not items:
                continue
            for item in items:
                citations.append(
                    Citation(
                        raw=item.group(0),
                        author_string=item.group("authors").strip(),
                        year=_parse_year(item.group("year")),
                        start=group_start + item.start(),
                        end=group_start + item.end(),
                    )
                )

        # Narrative form: `Author (Year)`. Only emit when not already
        # inside a parenthetical span we matched above.
        parenthetical_spans = [
            (m.start(), m.end()) for m in _PARENTHETICAL.finditer(text)
        ]
        for match in _NARRATIVE.finditer(text):
            if _inside_any_span(match.start(), match.end(), parenthetical_spans):
                continue
            citations.append(
                Citation(
                    raw=match.group(0),
                    author_string=match.group("authors").strip(),
                    year=_parse_year(match.group("year")),
                    start=match.start(),
                    end=match.end(),
                )
            )

        # Order by position so downstream consumers (UI highlights, claim
        # boundary detection) can iterate linearly.
        citations.sort(key=lambda c: c.start)
        return citations

    def resolve_references(self, text: str) -> dict[str, tuple[str, Any]]:
        """Author-date styles don't need a numbered bibliography map."""
        return {}


def _parse_year(raw: str) -> int | str:
    """Return an int for clean years, else the raw token ("2023a", "in press")."""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return raw.strip()


def _inside_any_span(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    """True if [start, end) falls entirely within any span."""
    return any(s <= start and end <= e for s, e in spans)
