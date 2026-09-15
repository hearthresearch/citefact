"""Page structure of a converted source.

pagebound records, for every block, the PDF page it came from and its
[md_start, md_end) offsets into the markdown the checks search. That
gives the PDF page of any matched passage. The page a manuscript cites
(`p. 47`) is the printed page, which differs from the PDF page whenever
the PDF is an offprint of a paginated journal issue, so a printed-page
offset is derived where it can be established and left unknown where it
cannot; an unknown offset never produces a page mismatch finding.

Two sources for the offset, in order:

1. Running headers or footers: furniture blocks whose whole text is one
   integer. When at least three pages carry such a number and a clear
   majority agree on `printed - pdf_page`, that is the offset.
2. The bibliography entry's page range (`233--250`), when its length
   equals the PDF's page count, so the PDF is the article and nothing else.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

MIN_NUMBERED_PAGES = 3
MAJORITY = 0.75

_BARE_INT = re.compile(r"\d{1,4}")
_BIB_RANGE = re.compile(r"^\s*(?P<a>\d+)\s*(?:-{1,2}|[\u2010-\u2015])\s*(?P<b>\d+)\s*$")


def offset_from_furniture(blocks: Sequence[Mapping[str, Any]]) -> int | None:
    """`printed - pdf_page` from page numbers in running headers/footers."""
    per_page: dict[int, set[int]] = {}
    for b in blocks:
        if b.get("layer") != "furniture":
            continue
        text = str(b.get("text", "")).strip()
        if _BARE_INT.fullmatch(text):
            per_page.setdefault(int(b["page"]), set()).add(int(text))
    if len(per_page) < MIN_NUMBERED_PAGES:
        return None
    offsets = Counter(
        number - page for page, numbers in per_page.items() for number in numbers
    )
    offset, votes = offsets.most_common(1)[0]
    if votes < MIN_NUMBERED_PAGES or votes < MAJORITY * len(per_page):
        return None
    return offset


def offset_from_bib_pages(pages: str | None, *, page_count: int) -> int | None:
    """`printed - pdf_page` from a bibliography page range that spans
    exactly the PDF's pages."""
    if pages is None:
        return None
    m = _BIB_RANGE.match(pages)
    if m is None:
        return None
    first, last = int(m.group("a")), int(m.group("b"))
    if last - first + 1 != page_count:
        return None
    return first - 1


@dataclass(frozen=True)
class PageMap:
    """Where each markdown offset of a source sits in its PDF.

    `spans` are (md_start, md_end, pdf_page) for every block that has
    markdown offsets, in document order; `offset` is `printed - pdf_page`
    or None when unknown.
    """

    spans: list[tuple[int, int, int]]
    page_count: int
    offset: int | None = None

    def pdf_page_at(self, md_offset: int) -> int | None:
        """PDF page of a markdown offset. An offset in the whitespace
        between two blocks belongs to the block before it; None only
        before the first block or without spans."""
        starts = [s for s, _, _ in self.spans]
        i = bisect_right(starts, md_offset) - 1
        return None if i < 0 else self.spans[i][2]

    def printed(self, pdf_page: int) -> int | None:
        return None if self.offset is None else pdf_page + self.offset

    def with_bib_pages(self, pages: str | None) -> PageMap:
        """Fill a missing offset from the bibliography's page range."""
        if self.offset is not None:
            return self
        return replace(self, offset=offset_from_bib_pages(pages, page_count=self.page_count))

    @classmethod
    def from_blocks(cls, blocks: Sequence[Mapping[str, Any]], *, page_count: int) -> PageMap:
        spans = sorted(
            (int(b["md_start"]), int(b["md_end"]), int(b["page"]))
            for b in blocks
            if b.get("md_start") is not None and b.get("md_end") is not None
        )
        return cls(spans=spans, page_count=page_count, offset=offset_from_furniture(blocks))
