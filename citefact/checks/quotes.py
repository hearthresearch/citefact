"""Quotes check: direct-quote verification. Deterministic; no LLM, no network."""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from citefact.citations.base import MatchedCitation
from citefact.ingest.structure import block_quotes, excluded_spans
from citefact.models import Finding, Source, line_of

MIN_QUOTE_CHARS = 20     # below this, treat as scare quotes and skip
ADJACENCY_WINDOW = 300   # max chars between quote and its citation
QUOTE_SIMILARITY_THRESHOLD = 80  # partial-ratio floor for quote_modified

_QUOTE_RE = re.compile(r'[“"](?P<q>[^“”"]+)[”"]')
_BLOCK_MARKERS = re.compile(r"^( {0,3}> ?)+", re.MULTILINE)

_LIGATURES = str.maketrans({
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
})
_UNIFY = str.maketrans({
    "“": '"', "”": '"', "‘": "'", "’": "'",
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
})


@dataclass
class QuoteSpan:
    """A quotation to verify. `form` is "inline" for text between quotation
    marks and "block" for a Markdown `>` block quote (APA's form for
    quotations of 40+ words, which carry no marks). [start, end) are
    offsets into the manuscript text."""

    text: str
    start: int
    end: int
    form: str = "inline"


def normalize(text: str) -> str:
    """Whitespace collapse, hyphenation repair, ligature expansion,
    quote/dash unification, case folding. Order matters: repair
    line-break hyphenation before collapsing whitespace."""
    text = text.translate(_LIGATURES).translate(_UNIFY)
    text = re.sub(r"-\s*\n\s*", "", text)   # line-break hyphenation
    text = re.sub(r"(\w)-(\w)", r"\1\2", text)  # in-word unicode hyphens (already unified)
    text = re.sub(r"\s+", " ", text)
    # PDF extraction sometimes splits ligatures with spaces mid-word
    # ("de fi nitely"). Bare fi/fl/ff/ffi/ffl tokens never occur in real
    # prose, so joining them to both neighbours is safe.
    text = re.sub(r"(\w) (ffi|ffl|ff|fi|fl) (\w)", r"\1\2\3", text)
    return text.strip().casefold()


def _inside(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(s <= start and end <= e for s, e in spans)


def extract_quotes(text: str) -> list[QuoteSpan]:
    """Inline quotations and block quotes, in document order. Quotation
    marks inside code, tables or a block quote are not extracted on their
    own: the first two are not prose, and the block is checked whole."""
    blocks = [QuoteSpan(b.text, b.start, b.end, "block") for b in block_quotes(text)]
    skip = excluded_spans(text) + [(b.start, b.end) for b in blocks]
    inline = [
        QuoteSpan(m.group("q"), m.start("q"), m.end("q"))
        for m in _QUOTE_RE.finditer(text)
        if not _inside(m.start(), m.end(), skip)
    ]
    return sorted(
        (q for q in inline + blocks if len(q.text) >= MIN_QUOTE_CHARS),
        key=lambda q: q.start,
    )


def _nearest_citation(quote: QuoteSpan, matched: list[MatchedCitation]) -> MatchedCitation | None:
    best: tuple[int, MatchedCitation] | None = None
    for m in matched:
        gap = max(m.citation.start - quote.end, quote.start - m.citation.end, 0)
        if gap <= ADJACENCY_WINDOW and (best is None or gap < best[0]):
            best = (gap, m)
    return best[1] if best else None


def _strip_trailing_attribution(quote: QuoteSpan, text: str, cite: MatchedCitation) -> str:
    """APA closes a block quote with a parenthetical citation after the final
    punctuation, inside the block. That citation is not part of what the
    source said, so it is cut from the text compared with the source. A
    citation the quoted author wrote (narrative, or mid-block) stays: only
    a parenthetical group followed by nothing but whitespace or punctuation
    up to the block end counts as the attribution."""
    if quote.form != "block" or not (quote.start <= cite.citation.start < quote.end):
        return quote.text
    before = text[quote.start:cite.citation.start]
    paren = before.rfind("(")
    if paren == -1 or ")" in before[paren:]:
        return quote.text
    close = text.find(")", cite.citation.end, quote.end)
    if close == -1 or not re.fullmatch(r"[\s.,;:!?]*", text[close + 1:quote.end]):
        return quote.text
    head = _BLOCK_MARKERS.sub("", before[:paren])
    return " ".join(head.split())


def check_quotes(
    text: str,
    matched: list[MatchedCitation],
    sources: dict[str, Source],
) -> list[Finding]:
    findings: list[Finding] = []
    for quote in extract_quotes(text):
        location = {"line": line_of(text, quote.start)}
        cite = _nearest_citation(quote, matched)
        if cite is None:
            findings.append(Finding(
                level="quotes", type="quote_unattributed", severity="warning",
                details={"quote": quote.text, "form": quote.form, "location": location},
            ))
            continue
        if cite.paper_id is None:
            continue  # orphan citation; the citations check already reported it
        source = sources.get(cite.paper_id)
        if source is None or source.text is None:
            continue  # missing_source; the citations check already reported it

        quoted = _strip_trailing_attribution(quote, text, cite)
        norm_quote = normalize(quoted)
        norm_source = normalize(source.text)
        base = {"source_id": cite.paper_id, "quote": quoted, "form": quote.form,
                "location": location}
        if norm_quote in norm_source:
            findings.append(Finding(
                level="quotes", type="quote_verified", severity="info", details=base,
            ))
            continue
        alignment = fuzz.partial_ratio_alignment(norm_quote, norm_source)
        if alignment is not None and alignment.score >= QUOTE_SIMILARITY_THRESHOLD:
            closest = norm_source[alignment.dest_start:alignment.dest_end]
            findings.append(Finding(
                level="quotes", type="quote_modified", severity="error",
                details={**base, "closest_match": closest,
                         "similarity": round(alignment.score, 1)},
            ))
        else:
            findings.append(Finding(
                level="quotes", type="quote_not_found", severity="error", details=base,
            ))
    return findings
