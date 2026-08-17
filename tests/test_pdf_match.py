from pathlib import Path

from citefact.ingest.pdfs import match_pdfs
from citefact.models import Source


def _sources():
    return {
        "smith2023": Source(id="smith2023", title="A Study of Things", authors="Smith, John", year=2023),
        "doe2021": Source(id="doe2021", title="Another Work Entirely", authors="Doe, Jane", year=2021),
    }


def test_filename_stem_match(tmp_path: Path, monkeypatch):
    (tmp_path / "SMITH2023.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr("citefact.ingest.pdfs._pdf_probe", lambda p: ("", ""))
    assert match_pdfs(_sources(), tmp_path) == {"smith2023": tmp_path / "SMITH2023.pdf"}


def test_fuzzy_title_match(tmp_path: Path, monkeypatch):
    pdf = tmp_path / "paper_final_v3.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setattr(
        "citefact.ingest.pdfs._pdf_probe",
        lambda p: ("A Study of Things", "A Study of Things\nJohn Smith\n2023\nAbstract..."),
    )
    assert match_pdfs(_sources(), tmp_path)["smith2023"] == pdf


def test_unmatchable_pdf_is_ignored(tmp_path: Path, monkeypatch):
    (tmp_path / "random_scan.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr("citefact.ingest.pdfs._pdf_probe", lambda p: ("", "grocery list"))
    assert match_pdfs(_sources(), tmp_path) == {}
