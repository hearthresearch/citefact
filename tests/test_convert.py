"""Tests for the pagebound-backed converter.

pagebound is exercised through its own public API with a fake converter,
so no Docling runs here; the fixtures pin the recipe to skip
`docling --version` too.
"""

from __future__ import annotations

import json
from pathlib import Path

import pagebound
import pytest
from pagebound import DoclingUnavailableError, Store
from pagebound.recipe import DEFAULT_OPTIONS, Recipe

from citefact.ingest import convert as convert_module
from citefact.ingest.convert import Converted, convert_pdf, pdf_sha256

RECIPE = Recipe(converter="docling", converter_version="9.9.9", options=dict(DEFAULT_OPTIONS))


def _docling_json(text: str) -> dict:
    """The smallest DoclingDocument pagebound's normaliser accepts."""
    return {
        "schema_name": "DoclingDocument",
        "version": "1.9.0",
        "name": "doc",
        "pages": {"1": {"page_no": 1, "size": {"width": 612.0, "height": 792.0}}},
        "body": {"self_ref": "#/body", "children": [{"$ref": "#/texts/0"}]},
        "texts": [{
            "self_ref": "#/texts/0", "label": "paragraph", "text": text,
            "content_layer": "body",
            "prov": [{"page_no": 1, "charspan": [0, len(text)],
                      "bbox": {"l": 1.0, "t": 2.0, "r": 3.0, "b": 1.0,
                               "coord_origin": "BOTTOMLEFT"}}],
        }],
        "tables": [], "pictures": [], "groups": [], "key_value_items": [],
    }


def _fake_converter(text: str, calls: list):
    def convert(pdf_path, output_dir, options, **kwargs):
        calls.append(Path(pdf_path))
        produced = Path(output_dir) / f"{Path(pdf_path).stem}.json"
        produced.write_text(json.dumps(_docling_json(text)), encoding="utf-8")
        return produced
    return convert


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-fake")
    return path


@pytest.fixture
def shared_cache(tmp_path: Path, monkeypatch) -> Path:
    """A user-level pagebound cache, isolated from the developer's real one."""
    root = tmp_path / "shared"
    monkeypatch.setenv("PAGEBOUND_CACHE", str(root))
    return root


@pytest.fixture
def fake_docling(monkeypatch):
    """Route conversions through a fake converter with a pinned recipe."""
    calls: list = []
    monkeypatch.setattr(convert_module, "_RECIPE", RECIPE)
    monkeypatch.setattr(convert_module, "_CONVERTER", _fake_converter("Webster & Watson", calls))
    monkeypatch.setattr(convert_module, "_PROBER", lambda p: {"has_text": True, "pages": 1,
                                                              "probe_pages": 1, "probe_chars": 10})
    return calls


class TestConvertPdf:
    def test_converts_into_project_store_only(self, pdf, tmp_path, shared_cache, fake_docling):
        result = convert_pdf(pdf, tmp_path / ".citefact")
        assert isinstance(result, Converted)
        assert result.text.strip() == "Webster & Watson"  # never &amp;
        assert result.sha256 == pdf_sha256(pdf)
        assert result.converter == "docling"
        assert result.converter_version == "9.9.9"
        assert result.text_layer is True
        assert fake_docling == [pdf]
        assert Store(tmp_path / ".citefact/pagebound").has(result.sha256, RECIPE.key)
        assert not shared_cache.exists()  # a fallback is never written

    def test_second_call_reads_cache_without_converting(self, pdf, tmp_path, shared_cache, fake_docling):
        convert_pdf(pdf, tmp_path / ".citefact")
        convert_pdf(pdf, tmp_path / ".citefact")
        assert len(fake_docling) == 1

    def test_shared_cache_hit_is_adopted_not_reconverted(self, pdf, tmp_path, shared_cache, fake_docling):
        # Populate the shared cache as another pagebound consumer would.
        pagebound.get_document(pdf, store=Store(shared_cache), recipe=RECIPE,
                               converter=convert_module._CONVERTER,
                               prober=convert_module._PROBER)
        assert len(fake_docling) == 1

        result = convert_pdf(pdf, tmp_path / ".citefact")
        assert result is not None
        assert len(fake_docling) == 1  # read through the fallback
        assert Store(tmp_path / ".citefact/pagebound").has(result.sha256, RECIPE.key)

    def test_force_reconverts(self, pdf, tmp_path, shared_cache, fake_docling):
        convert_pdf(pdf, tmp_path / ".citefact")
        convert_pdf(pdf, tmp_path / ".citefact", force=True)
        assert len(fake_docling) == 2

    def test_scan_is_converted_with_ocr(self, pdf, tmp_path, shared_cache, fake_docling, monkeypatch):
        monkeypatch.setattr(convert_module, "_PROBER", lambda p: {"has_text": False, "pages": 1,
                                                                  "probe_pages": 1, "probe_chars": 0})
        result = convert_pdf(pdf, tmp_path / ".citefact")
        assert result is not None
        assert result.text_layer is False
        assert len(fake_docling) == 1

    def test_conversion_failure_degrades_to_none(self, pdf, tmp_path, shared_cache, fake_docling, monkeypatch):
        from pagebound import ConversionFailedError

        def broken(*a, **k):
            raise ConversionFailedError("docling failed")

        monkeypatch.setattr(convert_module, "_CONVERTER", broken)
        assert convert_pdf(pdf, tmp_path / ".citefact") is None

    def test_docling_unavailable_degrades_to_none(self, pdf, tmp_path, shared_cache, fake_docling, monkeypatch):
        def missing(*a, **k):
            raise DoclingUnavailableError("uv was not found on PATH")

        monkeypatch.setattr(convert_module, "_CONVERTER", missing)
        assert convert_pdf(pdf, tmp_path / ".citefact") is None

    def test_unreadable_pdf_degrades_to_none(self, tmp_path, shared_cache, fake_docling):
        assert convert_pdf(tmp_path / "missing.pdf", tmp_path / ".citefact") is None

    def test_absent_shared_cache_is_not_created(self, pdf, tmp_path, shared_cache, fake_docling):
        convert_pdf(pdf, tmp_path / ".citefact")
        assert not shared_cache.exists()
