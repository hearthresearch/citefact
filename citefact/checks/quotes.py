"""Quotes check: direct-quote verification. Deterministic; no LLM, no network."""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from citefact.citations.base import MatchedCitation, locator_pages
from citefact.ingest.structure import block_quotes, excluded_spans
from citefact.ingest.pages import PageMap
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


_HYPHEN_BREAK = re.compile(r"-\s*\n\s*")
_IN_WORD_HYPHEN = re.compile(r"(\w)-(\w)")
_WHITESPACE = re.compile(r"\s+")
_SPLIT_LIGATURE = re.compile(r"(\w) (ffi|ffl|ff|fi|fl) (\w)")


def normalize(text: str) -> str:
    """Whitespace collapse, hyphenation repair, ligature expansion,
    quote/dash unification, case folding. Order matters: repair
    line-break hyphenation before collapsing whitespace."""
    return normalize_with_map(text)[0]


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """`normalize`, plus for every character of the result the offset in
    `text` it came from, so a match position in the normalized source can
    be turned back into a place in the PDF (see `PageMap`)."""
    chars: list[str] = []
    origin: list[int] = []
    for i, ch in enumerate(text):
        code = ord(ch)
        rep = _LIGATURES.get(code) or _UNIFY.get(code) or ch
        for c in str(rep):
            chars.append(c)
            origin.append(i)

    def rewrite(pattern: re.Pattern[str], keep_groups: tuple[int, ...], *, literal: str | None = None) -> None:
        nonlocal chars, origin
        text_now = "".join(chars)
        out_chars: list[str] = []
        out_origin: list[int] = []
        last = 0
        for m in pattern.finditer(text_now):
            out_chars.extend(chars[last:m.start()])
            out_origin.extend(origin[last:m.start()])
            if literal is not None:
                out_chars.append(literal)
                out_origin.append(origin[m.start()])
            for g in keep_groups:
                out_chars.extend(chars[m.start(g):m.end(g)])
                out_origin.extend(origin[m.start(g):m.end(g)])
            last = m.end()
        out_chars.extend(chars[last:])
        out_origin.extend(origin[last:])
        chars, origin = out_chars, out_origin

    rewrite(_HYPHEN_BREAK, ())                    # line-break hyphenation
    rewrite(_IN_WORD_HYPHEN, (1, 2))              # in-word hyphens (already unified)
    rewrite(_WHITESPACE, (), literal=" ")
    # PDF extraction sometimes splits ligatures with spaces mid-word
    # ("de fi nitely"). Bare fi/fl/ff/ffi/ffl tokens never occur in real
    # prose, so joining them to both neighbours is safe.
    rewrite(_SPLIT_LIGATURE, (1, 2, 3))

    # strip, then casefold char by char (casefolding can change length)
    lo, hi = 0, len(chars)
    while lo < hi and chars[lo] == " ":
        lo += 1
    while hi > lo and chars[hi - 1] == " ":
        hi -= 1
    out: list[str] = []
    out_origin: list[int] = []
    for c, o in zip(chars[lo:hi], origin[lo:hi]):
        folded = c.casefold()
        out.append(folded)
        out_origin.extend([o] * len(folded))
    return "".join(out), out_origin


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
    """The citation that attributes the quote: the nearest one within the
    adjacency window. For an inline quote, a citation between the marks
    belongs to the quoted author and never counts; a block quote carries
    its attribution inside the block by design."""
    best: tuple[int, MatchedCitation] | None = None
    for m in matched:
        if quote.form == "inline" and quote.start <= m.citation.start < quote.end:
            continue
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


def _page_details(
    page_map: PageMap | None, origin: list[int], norm_start: int, norm_end: int,
    claimed: list[int] | None,
) -> tuple[dict, bool]:
    """Page fields for a quote matched at [norm_start, norm_end) of the
    normalized source, and whether the manuscript's page claim is wrong.
    Printed page numbers are only compared when the source's numbering
    is known; a mismatch needs the claimed pages and the pages the match
    spans to be disjoint."""
    details: dict = {}
    if claimed is not None:
        details["page_claimed"] = claimed
    if page_map is None or not origin:
        return details, False
    first = page_map.pdf_page_at(origin[norm_start])
    last = page_map.pdf_page_at(origin[max(norm_start, norm_end - 1)])
    if first is None:
        return details, False
    details["pdf_page"] = first
    details["page_found"] = page_map.printed(first)
    if claimed is None or details["page_found"] is None:
        return details, False
    spanned = {page_map.printed(pg) for pg in range(first, (last or first) + 1)}
    return details, spanned.isdisjoint(claimed)


def check_quotes(
    text: str,
    matched: list[MatchedCitation],
    sources: dict[str, Source],
) -> list[Finding]:
    findings: list[Finding] = []
    normalized: dict[str, tuple[str, list[int]]] = {}
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
        if cite.paper_id not in normalized:
            normalized[cite.paper_id] = normalize_with_map(source.text)
        norm_source, origin = normalized[cite.paper_id]
        claimed = locator_pages(cite.citation.locator)
        base = {"source_id": cite.paper_id, "quote": quoted, "form": quote.form,
                "location": location}
        at = norm_source.find(norm_quote)
        if at != -1:
            pages, mismatch = _page_details(
                source.page_map, origin, at, at + len(norm_quote), claimed,
            )
            if mismatch:
                findings.append(Finding(
                    level="quotes", type="quote_page_mismatch", severity="warning",
                    details={**base, **pages},
                ))
            else:
                findings.append(Finding(
                    level="quotes", type="quote_verified", severity="info",
                    details={**base, **pages},
                ))
            continue
        alignment = fuzz.partial_ratio_alignment(norm_quote, norm_source)
        if alignment is not None and alignment.score >= QUOTE_SIMILARITY_THRESHOLD:
            closest = norm_source[alignment.dest_start:alignment.dest_end]
            pages, _ = _page_details(
                source.page_map, origin, alignment.dest_start, alignment.dest_end, claimed,
            )
            findings.append(Finding(
                level="quotes", type="quote_modified", severity="error",
                details={**base, "closest_match": closest,
                         "similarity": round(alignment.score, 1), **pages},
            ))
        else:
            findings.append(Finding(
                level="quotes", type="quote_not_found", severity="error",
                details={**base, **({"page_claimed": claimed} if claimed is not None else {})},
            ))
    return findings
