"""Citation parsing and catalog resolution for manuscript validation.

Structure:
- `base`: `Citation` dataclass + `CitationParser` protocol.
- `matcher`: style-agnostic fuzzy match from (surname, year) to a paper_id.
- `apa`: first parser — APA parenthetical and narrative, plus the Zotero
  Google Docs markdown-link wrapper that Zotero exports wrap citations in.

Adding a new format: drop a `<name>.py` that defines a class implementing
`CitationParser`, register it in `PARSERS` below. No other files change.
"""

from .apa import ApaParser
from .base import Citation, CitationParser, MatchedCitation
from .matcher import resolve_to_catalog

# Ordered by specificity — detect() breaks ties by order.
PARSERS: list[CitationParser] = [ApaParser()]


def pick_parser(manuscript: str) -> CitationParser:
    """Return the parser with the highest detection confidence.

    Falls back to the first parser (APA) if every parser scores 0, so even
    a manuscript with no recognized patterns gets some handling.
    """
    scored = [(p.detect(manuscript), p) for p in PARSERS]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


__all__ = [
    "Citation",
    "CitationParser",
    "MatchedCitation",
    "PARSERS",
    "pick_parser",
    "resolve_to_catalog",
]
