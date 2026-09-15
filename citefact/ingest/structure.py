"""Manuscript structure from a Markdown AST.

The checks scan the manuscript as one string with character offsets, so
everything here is expressed as offsets into that exact string. Parsing
goes through markdown-it-py (CommonMark plus GFM tables), whose block
tokens carry `map = [first_line, end_line)`; the line map is turned back
into character offsets against the original text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from markdown_it import MarkdownIt

_BLOCK_QUOTE_MARKERS = re.compile(r"^( {0,3}> ?)+")
_EXCLUDED_BLOCKS = {"fence", "code_block", "table_open"}


@dataclass
class BlockQuote:
    """A `>` block quote: its prose (markers stripped, whitespace collapsed)
    and the [start, end) offsets of the raw block in the manuscript."""

    text: str
    start: int
    end: int


def _parser() -> MarkdownIt:
    return MarkdownIt("commonmark").enable("table")


def _line_starts(text: str) -> list[int]:
    """Character offset at which each line begins, plus a sentinel at len(text)."""
    starts = [0]
    for m in re.finditer(r"\n", text):
        starts.append(m.end())
    if starts[-1] != len(text):
        starts.append(len(text))
    return starts


def _span(text: str, starts: list[int], line_map: tuple[int, int]) -> tuple[int, int]:
    first, end = line_map
    end_offset = starts[end] if end < len(starts) else len(text)
    return starts[first], min(end_offset, len(text))


def block_quotes(text: str) -> list[BlockQuote]:
    """Top-level block quotes in document order. Nested `>` levels are
    flattened into the outer quote."""
    starts = _line_starts(text)
    quotes: list[BlockQuote] = []
    for token in _parser().parse(text):
        if token.type != "blockquote_open" or token.level != 0 or token.map is None:
            continue
        start, end = _span(text, starts, tuple(token.map))
        lines = [_BLOCK_QUOTE_MARKERS.sub("", line) for line in text[start:end].splitlines()]
        prose = " ".join(" ".join(lines).split())
        quotes.append(BlockQuote(prose, start, end))
    return quotes


def excluded_spans(text: str) -> list[tuple[int, int]]:
    """[start, end) offsets of blocks no check should scan for prose:
    fenced and indented code, and tables."""
    starts = _line_starts(text)
    return [
        _span(text, starts, tuple(token.map))
        for token in _parser().parse(text)
        if token.type in _EXCLUDED_BLOCKS and token.map is not None
    ]
