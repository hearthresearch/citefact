from citefact.checks.citations import check_citations
from citefact.citations.base import Citation, MatchedCitation
from citefact.models import Source


def _m(raw, author, year, start, pid, conf=0.9):
    return MatchedCitation(Citation(raw, author, year, start, start + len(raw)), pid, conf)


def _sources():
    return {
        "smith2023": Source(id="smith2023", title="T", authors="Smith, J.", year=2023, text="full text"),
        "doe2021": Source(id="doe2021", title="U", authors="Doe, J.", year=2021, text="full text"),
        "kim2019": Source(id="kim2019", title="V", authors="Kim, H.", year=2019, text=None),
    }


TEXT = "line one\nSmith (2023) says X. Nobody et al. (2024) say Y.\nKim (2019) too."


def test_orphan_citation_error_with_line():
    matched = [
        _m("Smith (2023)", "Smith", 2023, 9, "smith2023"),
        _m("Nobody et al. (2024)", "Nobody et al.", 2024, 31, None, 0.0),
    ]
    findings = check_citations(TEXT, matched, _sources())
    orphans = [f for f in findings if f.type == "orphan_citation"]
    assert len(orphans) == 1
    assert orphans[0].severity == "error"
    assert orphans[0].details["location"]["line"] == 2


def test_orphans_deduped_by_author_year():
    matched = [
        _m("Nobody, 2024", "Nobody", 2024, 0, None, 0.0),
        _m("Nobody, 2024", "Nobody", 2024, 40, None, 0.0),
    ]
    findings = check_citations(TEXT, matched, _sources())
    assert len([f for f in findings if f.type == "orphan_citation"]) == 1


def test_uncited_reference_warning():
    matched = [_m("Smith (2023)", "Smith", 2023, 9, "smith2023")]
    findings = check_citations(TEXT, matched, _sources())
    uncited = {f.details["source_id"] for f in findings if f.type == "uncited_reference"}
    assert uncited == {"doe2021", "kim2019"}


def test_missing_source_only_when_cited():
    matched = [
        _m("Smith (2023)", "Smith", 2023, 9, "smith2023"),
        _m("Kim (2019)", "Kim", 2019, 58, "kim2019"),
    ]
    findings = check_citations(TEXT, matched, _sources())
    missing = [f for f in findings if f.type == "missing_source"]
    assert len(missing) == 1 and missing[0].details["source_id"] == "kim2019"
    assert missing[0].severity == "warning"


class TestUncitedSeverityBySource:
    def test_bib_source_keeps_warning(self):
        matched = [_m("Smith (2023)", "Smith", 2023, 9, "smith2023")]
        findings = check_citations(TEXT, matched, _sources())
        uncited = [f for f in findings if f.type == "uncited_reference"]
        assert all(f.severity == "warning" for f in uncited)

    def test_zotero_collection_source_demotes_to_info(self):
        """A Zotero collection is a reading library, not the paper's
        reference list: entries not (yet) cited are normal, not a warning."""
        matched = [_m("Smith (2023)", "Smith", 2023, 9, "smith2023")]
        findings = check_citations(TEXT, matched, _sources(),
                                   bibliography_is_library=True)
        uncited = [f for f in findings if f.type == "uncited_reference"]
        assert uncited and all(f.severity == "info" for f in uncited)
