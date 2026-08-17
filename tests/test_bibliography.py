from pathlib import Path

from citefact.ingest.bibliography import load_bibtex, to_catalog

BIB = """
@article{smith2023,
  author = {Smith, John and Jones, Kate},
  title = {A Study of Things},
  year = {2023},
}
@book{doe2021,
  author = {Doe, Jane},
  title = {Another Work},
  year = {2021},
}
"""


def test_load_bibtex(tmp_path: Path):
    p = tmp_path / "refs.bib"
    p.write_text(BIB, encoding="utf-8")
    sources = load_bibtex(p)
    assert set(sources) == {"smith2023", "doe2021"}
    s = sources["smith2023"]
    assert s.title == "A Study of Things"
    assert s.authors == "Smith, John and Jones, Kate"
    assert s.year == 2023
    assert s.pdf_path is None


def test_to_catalog_shape(tmp_path: Path):
    p = tmp_path / "refs.bib"
    p.write_text(BIB, encoding="utf-8")
    cat = to_catalog(load_bibtex(p))
    assert cat["doe2021"] == {
        "authors": "Doe, Jane", "year": 2021, "title": "Another Work"
    }


def test_matcher_resolves_bibtex_catalog(tmp_path: Path):
    from citefact.citations.base import Citation
    from citefact.citations import resolve_to_catalog

    p = tmp_path / "refs.bib"
    p.write_text(BIB, encoding="utf-8")
    cat = to_catalog(load_bibtex(p))
    m = resolve_to_catalog([Citation("Smith & Jones, 2023", "Smith & Jones", 2023, 0, 19)], cat)
    assert m[0].paper_id == "smith2023"


def test_strips_all_braces_from_title(tmp_path: Path):
    """Regression: bibtexparser leaves inner protective braces; strip them all.

    BibTeX pattern: title = {{BERT}: Pre-training of Deep Bidirectional Transformers}
    bibtexparser parsed value: {BERT}: Pre-training...

    Old code used .strip("{}") which only removes edge chars, leaving stray },
    leaving {BERT}: Pre-training... corrupted. Fix removes all braces.
    """
    bib_with_braces = """
    @article{devlin2019,
      author = {Devlin, Jacob and others},
      title = {{BERT}: Pre-training of Deep Bidirectional Transformers},
      year = {2019},
    }
    """
    p = tmp_path / "refs.bib"
    p.write_text(bib_with_braces, encoding="utf-8")
    sources = load_bibtex(p)
    assert sources["devlin2019"].title == "BERT: Pre-training of Deep Bidirectional Transformers"
