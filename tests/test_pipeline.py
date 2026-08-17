from pathlib import Path

import pytest

from citefact import pipeline

_BIB = """@article{smith2023,
  author = {Smith, John},
  title = {A Study of Things},
  year = {2023},
}
"""


def test_run_check_claims_without_model_raises_value_error(tmp_path: Path):
    """`assert model is not None` used to guard this; asserts vanish under
    `python -O`, so a missing model would silently proceed with model=None
    instead of failing loudly. Must be an explicit, non-strippable raise."""
    m = tmp_path / "m.md"
    m.write_text("Smith (2023) says things.", encoding="utf-8")
    bib = tmp_path / "refs.bib"
    bib.write_text(_BIB, encoding="utf-8")

    with pytest.raises(ValueError, match="claims level requires a model"):
        pipeline.run_check(
            m, bib_path=bib, pdf_dir=None, cache_dir=tmp_path / ".citefact",
            levels=["claims"], model=None, progress=lambda _m: None,
        )


def test_run_check_requires_a_bibliography_source(tmp_path: Path):
    m = tmp_path / "m.md"
    m.write_text("Smith (2023) says things.", encoding="utf-8")
    with pytest.raises(ValueError, match="bibliography source"):
        pipeline.run_check(
            m, bib_path=None, pdf_dir=None, cache_dir=tmp_path / ".citefact",
            levels=["citations"], model=None, progress=lambda _m: None,
        )


def test_zotero_sources_with_pdf_paths_convert_without_pdf_dir(
    tmp_path: Path, monkeypatch
):
    """A Zotero collection already carries attachment paths; the pipeline
    must convert those PDFs even when --pdfs was never given."""
    from citefact.models import Source

    m = tmp_path / "m.md"
    m.write_text('Smith (2023) said "a sufficiently long quoted passage here" (Smith, 2023).',
                 encoding="utf-8")
    pdf = tmp_path / "smith.pdf"
    pdf.write_bytes(b"%PDF-fake")

    monkeypatch.setattr(
        "citefact.pipeline.load_zotero_collection",
        lambda name: {"smith2023": Source(
            id="smith2023", title="A Study", authors="Smith, John",
            year=2023, pdf_path=pdf,
        )},
    )
    converted = []

    def fake_convert(p, cache_dir, force=False):
        converted.append(p)
        return "full text with a sufficiently long quoted passage here inside"

    monkeypatch.setattr("citefact.pipeline.convert_pdf", fake_convert)
    monkeypatch.setattr("citefact.pipeline.pdf_sha256", lambda p: "hash")

    report = pipeline.run_check(
        m, bib_path=None, zotero_collection="My Papers", pdf_dir=None,
        cache_dir=tmp_path / ".citefact", levels=["citations", "quotes"],
        model=None, progress=lambda _m: None,
    )
    assert converted == [pdf]
    types = {f["type"] for f in report["findings"]}
    assert "missing_source" not in types
    assert "quote_verified" in types


def test_match_pdfs_only_fills_sources_without_paths(tmp_path: Path, monkeypatch):
    """--pdfs may supplement a Zotero collection: matching must not
    overwrite attachment paths that are already set."""
    from citefact.models import Source

    m = tmp_path / "m.md"
    m.write_text("Smith (2023) and Doe (2021) say things.", encoding="utf-8")
    zotero_pdf = tmp_path / "from_zotero.pdf"
    zotero_pdf.write_bytes(b"%PDF-z")
    pdf_dir = tmp_path / "papers"
    pdf_dir.mkdir()
    (pdf_dir / "smith2023.pdf").write_bytes(b"%PDF-dir")  # decoy for smith
    (pdf_dir / "doe2021.pdf").write_bytes(b"%PDF-dir")

    monkeypatch.setattr(
        "citefact.pipeline.load_zotero_collection",
        lambda name: {
            "smith2023": Source(id="smith2023", title="A", authors="Smith, J",
                                year=2023, pdf_path=zotero_pdf),
            "doe2021": Source(id="doe2021", title="B", authors="Doe, J", year=2021),
        },
    )
    seen = {}

    def fake_convert(p, cache_dir, force=False):
        seen[p.name] = True
        return "text"

    monkeypatch.setattr("citefact.pipeline.convert_pdf", fake_convert)
    monkeypatch.setattr("citefact.pipeline.pdf_sha256", lambda p: f"h-{p.name}")
    monkeypatch.setattr("citefact.pipeline._pdf_probe", lambda p: ("", ""),
                        raising=False)

    pipeline.run_check(
        m, bib_path=None, zotero_collection="C", pdf_dir=pdf_dir,
        cache_dir=tmp_path / ".citefact", levels=["citations"],
        model=None, progress=lambda _m: None,
    )
    assert set(seen) == {"from_zotero.pdf", "doe2021.pdf"}


def test_convert_phase_emits_counted_events(tmp_path: Path, monkeypatch):
    from citefact.models import Source
    from citefact.progress import ProgressEvent

    m = tmp_path / "m.md"
    m.write_text("Smith (2023) and Doe (2021).", encoding="utf-8")
    pdf_a = tmp_path / "a.pdf"; pdf_a.write_bytes(b"%PDF")
    pdf_b = tmp_path / "b.pdf"; pdf_b.write_bytes(b"%PDF")
    monkeypatch.setattr(
        "citefact.pipeline.load_zotero_collection",
        lambda name: {
            "smith2023": Source(id="smith2023", title="A", authors="Smith, J",
                                year=2023, pdf_path=pdf_a),
            "doe2021": Source(id="doe2021", title="B", authors="Doe, J",
                              year=2021, pdf_path=pdf_b),
        },
    )
    monkeypatch.setattr("citefact.pipeline.convert_pdf", lambda p, c, force=False: "text")
    monkeypatch.setattr("citefact.pipeline.pdf_sha256", lambda p: "h")

    events: list[ProgressEvent] = []
    pipeline.run_check(
        m, bib_path=None, zotero_collection="C", pdf_dir=None,
        cache_dir=tmp_path / ".c", levels=["citations"],
        model=None, progress=events.append,
    )
    convert = [e for e in events if e.phase == "convert" and e.total is not None]
    assert [(e.current, e.total) for e in convert] == [(1, 2), (2, 2)]
    assert all(isinstance(e, ProgressEvent) for e in events)


def test_completed_facts_are_checkmarked(tmp_path: Path):
    from citefact.progress import ProgressEvent

    m = tmp_path / "m.md"
    m.write_text("Smith (2023) says things.", encoding="utf-8")
    bib = tmp_path / "refs.bib"
    bib.write_text("@article{smith2023, author={Smith, J}, title={T}, year={2023}}",
                   encoding="utf-8")
    events: list[ProgressEvent] = []
    pipeline.run_check(
        m, bib_path=bib, pdf_dir=None, cache_dir=tmp_path / ".c",
        levels=["citations"], model=None, progress=events.append,
    )
    messages = [e.message for e in events if e.phase == "load"]
    assert any(msg.startswith("✓ Loaded") for msg in messages)
    assert any(msg.startswith("✓ Found") for msg in messages)


def test_report_carries_grouped_citation_inventory(tmp_path: Path):
    m = tmp_path / "m.md"
    m.write_text(
        "Smith (2023) says things.\nLater, Smith (2023) repeats.\n"
        "And Ghost (2024) was invented.\n", encoding="utf-8")
    bib = tmp_path / "refs.bib"
    bib.write_text("@article{smith2023, author={Smith, J}, title={T}, year={2023}}",
                   encoding="utf-8")
    report = pipeline.run_check(
        m, bib_path=bib, pdf_dir=None, cache_dir=tmp_path / ".c",
        levels=["citations"], model=None, progress=lambda _e: None,
    )
    cites = report["citations"]
    smith = next(c for c in cites if c["source_id"] == "smith2023")
    assert smith["count"] == 2
    assert smith["lines"] == [1, 2]
    ghost = next(c for c in cites if c["source_id"] is None)
    assert ghost["count"] == 1 and ghost["lines"] == [3]
