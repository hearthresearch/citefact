"""Saved web pages (HTML snapshots) as sources: text extraction, folder
matching, Zotero snapshot attachments, and the offline quote check."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from citefact import pipeline
from citefact.ingest.pdfs import match_pdfs
from citefact.ingest.web import convert_snapshot, snapshot_text, snapshot_title
from citefact.models import Source

_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Incident report &amp; timeline</title>
<style>p { color: red }</style>
<script>var quote = "this script text must never match";</script>
<body>
<nav>Home | Blog</nav>
<article>
<h1>What happened</h1>
<p>The attacker published a malicious release that ran code
at install time, before any import.</p>
<p>Caf&eacute; owners &#8220;were not affected&#8221;.</p>
<noscript>Enable JavaScript</noscript>
</article>
</body></html>
"""

_QUOTE = "published a malicious release that ran code at install time"

_BIB = """@misc{doe2026,
  author = {Doe, Jane},
  title = {Incident report & timeline},
  year = {2026},
  howpublished = {\\url{https://example.org/incident}},
}
"""


def _page(tmp_path: Path, name: str = "doe2026.html", html: str = _PAGE) -> Path:
    path = tmp_path / name
    path.write_text(html, encoding="utf-8")
    return path


class TestExtraction:
    def test_keeps_visible_prose_and_drops_scripts_and_styles(self, tmp_path):
        text = snapshot_text(_page(tmp_path))
        assert "ran code at install time" in " ".join(text.split())
        assert "script text" not in text
        assert "color: red" not in text
        assert "Enable JavaScript" not in text

    def test_decodes_character_references(self, tmp_path):
        text = snapshot_text(_page(tmp_path))
        assert "Café owners “were not affected”." in text

    def test_title_is_read_but_not_page_text(self, tmp_path):
        path = _page(tmp_path)
        assert snapshot_title(path) == "Incident report & timeline"
        assert "Incident report" not in snapshot_text(path)

    def test_omitted_head_end_tag_does_not_hide_the_page(self, tmp_path):
        # HTML lets </head> be left out; the body must still be read.
        path = _page(tmp_path, html="<html><head><title>T</title><body><p>Body text here.</p>")
        assert snapshot_text(path) == "Body text here."

    def test_block_elements_break_lines(self, tmp_path):
        path = _page(tmp_path, html="<p>one</p><p>two</p><ul><li>three</li></ul>")
        assert snapshot_text(path).splitlines() == ["one", "two", "three"]


class TestConvertSnapshot:
    def test_records_hash_and_provenance(self, tmp_path):
        path = _page(tmp_path)
        converted = convert_snapshot(path)
        assert converted is not None
        assert converted.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
        assert converted.converter == "citefact-html"
        assert converted.page_map is None

    def test_page_without_text_is_unavailable(self, tmp_path):
        assert convert_snapshot(_page(tmp_path, html="<script>x()</script>")) is None

    def test_missing_file_is_unavailable(self, tmp_path):
        assert convert_snapshot(tmp_path / "gone.html") is None


class TestFolderMatching:
    def test_matches_snapshot_by_filename_stem(self, tmp_path):
        page = _page(tmp_path)
        sources = {"doe2026": Source(id="doe2026", title="Other", authors="Doe", year=2026)}
        assert match_pdfs(sources, tmp_path) == {"doe2026": page}

    def test_matches_snapshot_by_title(self, tmp_path):
        page = _page(tmp_path, name="saved-page.htm")
        sources = {"doe2026": Source(id="doe2026", title="Incident report & timeline",
                                     authors="Doe", year=2026)}
        # The year must corroborate a title match; put it in the page text.
        page.write_text(_PAGE.replace("What happened", "What happened in 2026"),
                        encoding="utf-8")
        assert match_pdfs(sources, tmp_path) == {"doe2026": page}


def _run(tmp_path: Path, manuscript: str, page_dir: Path) -> dict:
    m = tmp_path / "m.md"
    m.write_text(manuscript, encoding="utf-8")
    bib = tmp_path / "refs.bib"
    bib.write_text(_BIB, encoding="utf-8")
    return pipeline.run_check(
        m, bib_path=bib, pdf_dir=page_dir, cache_dir=tmp_path / ".citefact",
        levels=["citations", "quotes"], model=None, progress=lambda _m: None,
    )


def _pages_dir(tmp_path: Path) -> Path:
    pages = tmp_path / "sources"
    pages.mkdir()
    _page(pages)
    return pages


class TestPipeline:
    def test_verbatim_quote_from_a_saved_page_is_verified(self, tmp_path, monkeypatch):
        def no_pdf_conversion(*_a, **_k):
            raise AssertionError("a saved page must not go through the PDF converter")

        monkeypatch.setattr("citefact.pipeline.convert_pdf", no_pdf_conversion)
        report = _run(tmp_path, f'The vendor said the attacker "{_QUOTE}" (Doe, 2026).',
                      _pages_dir(tmp_path))
        types = [f["type"] for f in report["findings"]]
        assert "quote_verified" in types
        assert "missing_source" not in types
        source = report["sources"][0]
        assert source["snapshot"].endswith("doe2026.html")
        assert source["pdf"] is None
        assert source["conversion"]["converter"] == "citefact-html"

    def test_altered_quote_from_a_saved_page_is_reported(self, tmp_path):
        altered = _QUOTE.replace("at install time", "at import time")
        report = _run(tmp_path, f'The vendor said the attacker "{altered}" (Doe, 2026).',
                      _pages_dir(tmp_path))
        assert "quote_modified" in [f["type"] for f in report["findings"]]

    def test_pdf_wins_over_a_saved_page(self, tmp_path, monkeypatch):
        from citefact.ingest.convert import Converted

        pages = _pages_dir(tmp_path)
        (pages / "doe2026.pdf").write_bytes(b"%PDF-fake")
        monkeypatch.setattr(
            "citefact.pipeline.convert_pdf",
            lambda p, cache_dir, force=False: Converted(
                text="pdf text only", sha256="h", converter="docling",
                converter_version="0", converted_at="2026-01-01T00:00:00+00:00",
                text_layer=True,
            ),
        )
        report = _run(tmp_path, "Doe (2026) wrote about it.", pages)
        assert report["sources"][0]["conversion"]["converter"] == "docling"


@pytest.fixture
def zotero(monkeypatch):
    from tests.test_zotero import FakeZotero, _author, _coll, _item

    class _Module:
        Zotero = FakeZotero

    monkeypatch.setattr("citefact.ingest.zotero._pyzotero", _Module)
    FakeZotero.collections_data = [_coll("AAA", "Web")]
    FakeZotero.items_data = {"AAA": [_item("I1", "Incident report", "2026", [_author("Doe")])]}
    FakeZotero.children_data = {}
    return FakeZotero


def test_zotero_snapshot_attachment_becomes_the_snapshot_path(tmp_path, zotero):
    from citefact.ingest.zotero import load_zotero_collection

    page = _page(tmp_path)
    zotero.children_data = {"I1": [{
        "data": {"itemType": "attachment", "contentType": "text/html"},
        "links": {"enclosure": {"href": page.as_uri()}},
    }]}
    (source,) = load_zotero_collection("Web").values()
    assert source.snapshot_path == page
    assert source.pdf_path is None


def test_html_report_links_the_saved_page(tmp_path):
    from citefact.report.html_out import write_report_html

    report = _run(tmp_path, f'The vendor said the attacker "{_QUOTE}" (Doe, 2026).',
                  _pages_dir(tmp_path))
    html = write_report_html(report, tmp_path / "out").read_text(encoding="utf-8")
    assert (tmp_path / "sources" / "doe2026.html").resolve().as_uri() in html
