"""Tests for the Zotero local-API bibliography source (pyzotero mocked)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import pytest

from citefact.ingest.zotero import load_zotero_collection

PAGE_SIZE = 25  # what the local API returns when pagination is ignored


class _FirstPage(list):
    """Simulates pyzotero's first-page-only return; `everything` unwraps it."""

    def __init__(self, full: list):
        super().__init__(full[:PAGE_SIZE])
        self.full = full


class FakeZotero:
    """Minimal stand-in for pyzotero.zotero.Zotero against the local API."""

    def __init__(self, library_id, library_type, local=False, **kwargs):
        assert local is True, "must use the local Zotero API"
        self._collections = FakeZotero.collections_data
        self._items = FakeZotero.items_data
        self._children = FakeZotero.children_data

    collections_data: list = []
    items_data: dict = {}
    children_data: dict = {}

    def collections(self):
        return _FirstPage(self._collections)

    def collection_items_top(self, key):
        return _FirstPage(self._items.get(key, []))

    def children(self, item_key):
        return _FirstPage(self._children.get(item_key, []))

    def everything(self, page):
        return page.full if isinstance(page, _FirstPage) else page


def _coll(key, name, parent=False):
    return {"key": key, "data": {"name": name, "parentCollection": parent}}


def _item(key, title, date, creators):
    return {
        "key": key,
        "data": {"itemType": "journalArticle", "title": title, "date": date,
                 "creators": creators},
    }


def _author(last, first=""):
    return {"creatorType": "author", "lastName": last, "firstName": first}


def _pdf_child(href):
    return {
        "data": {"itemType": "attachment", "contentType": "application/pdf"},
        "links": {"enclosure": {"href": href}},
    }


@pytest.fixture
def fake(monkeypatch):
    class _Module:
        Zotero = FakeZotero

    monkeypatch.setattr("citefact.ingest.zotero._pyzotero", _Module)
    FakeZotero.collections_data = []
    FakeZotero.items_data = {}
    FakeZotero.children_data = {}
    return FakeZotero


class TestCollectionResolution:
    def test_finds_collection_by_name(self, fake):
        fake.collections_data = [_coll("AAA", "My Papers")]
        fake.items_data = {"AAA": [_item("I1", "A Study", "2023", [_author("Smith", "Jo")])]}
        sources = load_zotero_collection("My Papers")
        assert len(sources) == 1

    def test_nested_path_resolves_child_collection(self, fake):
        fake.collections_data = [
            _coll("AAA", "PhD"),
            _coll("BBB", "Chapter 3", parent="AAA"),
            _coll("CCC", "Chapter 3", parent="ZZZ"),  # same name, other parent
            _coll("ZZZ", "Other"),
        ]
        fake.items_data = {"BBB": [_item("I1", "T", "2020", [_author("Doe")])]}
        sources = load_zotero_collection("PhD/Chapter 3")
        assert len(sources) == 1

    def test_unknown_collection_raises_with_names(self, fake):
        fake.collections_data = [_coll("AAA", "My Papers")]
        with pytest.raises(ValueError, match="My Papers"):
            load_zotero_collection("Nope")

    def test_ambiguous_name_raises(self, fake):
        fake.collections_data = [_coll("AAA", "Dup"), _coll("BBB", "Dup")]
        with pytest.raises(ValueError, match="[Aa]mbiguous"):
            load_zotero_collection("Dup")


class TestPagination:
    def test_reads_beyond_first_page(self, fake):
        """The pyzotero pitfall: listing calls return ~25 items unless
        wrapped in zot.everything(). 30 items must yield 30 sources."""
        items = [
            _item(f"I{n}", f"Title {n}", "2020", [_author(f"Auth{n}")])
            for n in range(30)
        ]
        fake.collections_data = [_coll("AAA", "Big")]
        fake.items_data = {"AAA": items}
        assert len(load_zotero_collection("Big")) == 30


class TestItemMapping:
    def test_source_fields_and_key(self, fake):
        fake.collections_data = [_coll("AAA", "C")]
        fake.items_data = {"AAA": [_item(
            "I1", "A Grand Study", "06/2023",
            [_author("Smith", "Jo"), _author("Jones", "Kim")],
        )]}
        sources = load_zotero_collection("C")
        s = sources["smith2023"]
        assert s.title == "A Grand Study"
        assert s.authors == "Smith, Jo and Jones, Kim"
        assert s.year == 2023
        assert s.pdf_path is None

    def test_duplicate_keys_get_suffix(self, fake):
        fake.collections_data = [_coll("AAA", "C")]
        fake.items_data = {"AAA": [
            _item("I1", "First", "2023", [_author("Smith")]),
            _item("I2", "Second", "2023", [_author("Smith")]),
        ]}
        sources = load_zotero_collection("C")
        assert set(sources) == {"smith2023", "smith2023a"}

    def test_skips_items_without_title_or_creators(self, fake):
        fake.collections_data = [_coll("AAA", "C")]
        fake.items_data = {"AAA": [
            _item("I1", "", "2023", [_author("Smith")]),
            _item("I2", "Ok", "2023", []),
            _item("I3", "Kept", "2023", [_author("Doe")]),
        ]}
        assert set(load_zotero_collection("C")) == {"doe2023"}


class TestAttachments:
    def test_pdf_path_from_enclosure_href(self, fake, tmp_path):
        pdf = tmp_path / "my paper.pdf"
        pdf.write_bytes(b"%PDF")
        fake.collections_data = [_coll("AAA", "C")]
        fake.items_data = {"AAA": [_item("I1", "T", "2021", [_author("Doe")])]}
        fake.children_data = {"I1": [_pdf_child(f"file://{quote(str(pdf))}")]}
        sources = load_zotero_collection("C")
        assert sources["doe2021"].pdf_path == pdf

    def test_missing_file_leaves_pdf_path_none(self, fake):
        fake.collections_data = [_coll("AAA", "C")]
        fake.items_data = {"AAA": [_item("I1", "T", "2021", [_author("Doe")])]}
        fake.children_data = {"I1": [_pdf_child("file:///nonexistent/x.pdf")]}
        assert load_zotero_collection("C")["doe2021"].pdf_path is None

    def test_non_pdf_children_ignored(self, fake):
        fake.collections_data = [_coll("AAA", "C")]
        fake.items_data = {"AAA": [_item("I1", "T", "2021", [_author("Doe")])]}
        fake.children_data = {"I1": [{
            "data": {"itemType": "attachment", "contentType": "text/html"},
            "links": {"enclosure": {"href": "file:///whatever.html"}},
        }]}
        assert load_zotero_collection("C")["doe2021"].pdf_path is None


class TestConnectionFailure:
    def test_unreachable_api_raises_runtime_error(self, fake, monkeypatch):
        class Boom:
            def __init__(self, *a, **k):
                pass

            def collections(self):
                raise OSError("connection refused")

            def everything(self, x):
                return x

        class _Module:
            Zotero = Boom

        monkeypatch.setattr("citefact.ingest.zotero._pyzotero", _Module)
        with pytest.raises(RuntimeError, match="[Ii]s Zotero.*running"):
            load_zotero_collection("Any")
