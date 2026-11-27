"""Saved web pages (HTML snapshots) as source text.

A web page is cited by what it said when it was read, so the source is a
saved copy: a Zotero snapshot attachment or an `.html` file in the
sources folder. Nothing is fetched; the check stays offline.

The text is the page's visible prose: `<script>`, `<style>` and other
non-rendered elements are dropped, block elements become line breaks,
and character references are decoded, so a quote copied from the page
in a browser matches the text checked here. Standard library only.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from citefact import __version__
from citefact.ingest.convert import Converted

log = logging.getLogger(__name__)

SNAPSHOT_SUFFIXES = (".html", ".htm")

# Elements whose content is never rendered as page text.
# <title> is read for matching, not as page text. <head> is not listed:
# HTML lets its end tag be omitted, which would hide the whole page.
_HIDDEN = {"script", "style", "noscript", "template", "title", "svg", "iframe", "object"}
# Elements that end a line of text when they open or close.
_BLOCK = {
    "address", "article", "aside", "blockquote", "br", "dd", "details", "div",
    "dl", "dt", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
    "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre",
    "section", "summary", "table", "td", "th", "tr", "ul",
}
# Void elements never get an end tag, so they must not open a hidden span.
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
         "meta", "source", "track", "wbr"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self._hidden_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "title":
            self._in_title = True
        if tag in _HIDDEN and tag not in _VOID:
            self._hidden_depth += 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in _HIDDEN and self._hidden_depth:
            self._hidden_depth -= 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if not self._hidden_depth:
            self.parts.append(data)


def _extract(html: str) -> tuple[str, str]:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    lines = (" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    text = "\n".join(line for line in lines if line)
    return text, " ".join("".join(parser.title_parts).split())


def is_snapshot(path: Path) -> bool:
    return path.suffix.lower() in SNAPSHOT_SUFFIXES


def snapshot_title(path: Path) -> str:
    """The page's `<title>`, or "" when the file can't be read."""
    try:
        return _extract(path.read_text(encoding="utf-8", errors="replace"))[1]
    except OSError:
        return ""


def snapshot_text(path: Path) -> str:
    """The page's visible text, or "" when the file can't be read."""
    try:
        return _extract(path.read_text(encoding="utf-8", errors="replace"))[0]
    except OSError:
        return ""


def convert_snapshot(path: Path) -> Converted | None:
    """Return a saved page's visible text with provenance, or None.

    None (an unreadable file or a page with no text) degrades the source
    to `missing_source`, as a failed PDF conversion does.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        log.warning("Could not read %s: %s", path.name, exc)
        return None
    text, _title = _extract(raw.decode("utf-8", errors="replace"))
    if not text:
        log.warning("No text in %s", path.name)
        return None
    return Converted(
        text=text,
        sha256=hashlib.sha256(raw).hexdigest(),
        converter="citefact-html",
        converter_version=__version__,
        converted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        text_layer=None,
        page_map=None,
    )
