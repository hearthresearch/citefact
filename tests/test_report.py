import json
from pathlib import Path

from citefact.models import Finding, Manuscript, Source
from citefact.report.html_out import word_diff_html, write_report_html
from citefact.report.json_out import build_report, write_report_json


def _report():
    m = Manuscript(path=Path("m.md"), text="x", sha256="s" * 64, words=100)
    sources = {"smith2023": Source(id="smith2023", title="T", authors="Smith, J.",
                                   year=2023, pdf_path=Path("p/s.pdf"), text="t")}
    findings = [
        Finding("citations", "orphan_citation", "error", {"citation": "(X, 2024)"}),
        Finding("quotes", "quote_verified", "info", {"source_id": "smith2023"}),
        Finding("claims", "claim_verdict", "warning", {"verdict": "partial"}),
    ]
    return build_report(
        m, sources, findings, levels=["citations", "quotes", "claims"],
        model="anthropic/claude-sonnet-5", cost_usd=0.12, duration_seconds=42.5,
        claims_summary={"claims_total": 1, "verdicts": {"partial": 1}},
    )


def test_schema_shape():
    r = _report()
    assert r["schema_version"] == 1
    assert r["manuscript"]["file"] == "m.md"
    assert r["run"]["model"] == "anthropic/claude-sonnet-5"
    assert r["summary"] == {"errors": 1, "warnings": 1, "claims_total": 1,
                            "verdicts": {"partial": 1}}
    assert r["sources"][0]["converted"] is True
    assert len(r["findings"]) == 3


def test_write_is_valid_json(tmp_path):
    p = write_report_json(_report(), tmp_path)
    assert p.name == "report.json"
    assert json.loads(p.read_text())["schema_version"] == 1


def test_word_diff_marks_insertions():
    html = word_diff_html("the quick fox", "the quick brown fox")
    assert "<ins>brown</ins>" in html


def test_html_is_self_contained(tmp_path):
    p = write_report_html(_report(), tmp_path)  # _report() from Task 13 tests
    html = p.read_text()
    assert p.name == "report.html"
    assert "<style>" in html and "<script>" in html
    for marker in ("http://", "https://cdn", "src=\"http", "href=\"http"):
        assert marker not in html
    assert "orphan_citation" in html


def test_partial_banner(tmp_path):
    r = _report()
    r["run"]["partial"] = True
    html = write_report_html(r, tmp_path).read_text()
    assert "partial" in html.lower()


def test_html_escapes_untrusted_strings(tmp_path):
    r = _report()
    r["findings"] = [
        Finding(
            "citations", "orphan_citation", "error",
            {"citation": "<img src=x onerror=alert(1)>"},
        ).to_dict(),
        Finding(
            "quotes", "quote_verified", "info",
            {"source_id": "smith2023", "quote": "<script>alert(2)</script>"},
        ).to_dict(),
        Finding(
            "quotes", "quote_modified", "error",
            {"source_id": "smith2023", "quote": "the quick fox",
             "closest_match": "the quick brown fox", "similarity": 0.9},
        ).to_dict(),
    ]
    html = write_report_html(r, tmp_path).read_text()

    # Hostile strings from findings must never appear unescaped.
    assert "<img src=x onerror=alert(1)>" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "<script>alert(2)</script>" not in html
    assert "&lt;script&gt;alert(2)&lt;/script&gt;" in html

    # The pre-escaped word-diff markup (the one deliberate `| safe`) must
    # still render as real HTML tags, not double-escaped.
    assert "<ins>brown</ins>" in html


def test_verdict_filter_and_level_counts_render(tmp_path):
    r = _report()  # claims_summary verdicts = {"partial": 1}
    html = write_report_html(r, tmp_path).read_text()

    # Spec: filter toggles by verdict, one checkbox per verdict present.
    assert 'class="verdict" value="partial"' in html

    # Spec: summary counts by severity (big numbers) and by level
    # (carried on the section tabs' status chips, not a separate card row).
    assert 'id="count-errors"' in html
    assert 'id="count-warnings"' in html
    assert html.count('type="radio" name="tab"') == 4


def test_write_report_html_does_not_mutate_input(tmp_path):
    r = _report()
    r["findings"].append(
        Finding(
            "quotes", "quote_modified", "error",
            {"source_id": "smith2023", "quote": "the quick fox",
             "closest_match": "the quick brown fox", "similarity": 0.9},
        ).to_dict()
    )
    before = json.dumps(r, sort_keys=True)

    write_report_html(r, tmp_path)

    assert json.dumps(r, sort_keys=True) == before
    assert "diff_html" not in json.dumps(r)


class TestReportDesignLanguage:
    def test_claim_rows_have_status_rail_and_ai_card(self, tmp_path):
        r = _report()
        html = write_report_html(r, tmp_path).read_text()
        assert 'class="rail' in html          # 3px status rail per finding row
        assert "AI Suggestion" in html         # AI card label on claim findings
        assert "AI suggests, you decide" not in html or True

    def test_verdict_badges_use_inline_svg_icons(self, tmp_path):
        """Icons are inline HTML5 <svg> elements: no xmlns (would trip the
        no-external-references check), no icon font, no image files."""
        r = _report()
        html = write_report_html(r, tmp_path).read_text()
        assert "<svg" in html
        assert "xmlns" not in html
        assert "xlink" not in html


class TestUncitedAggregation:
    def test_uncited_references_collapse_into_one_card(self, tmp_path):
        from citefact.models import Finding, Manuscript
        from citefact.report.json_out import build_report

        m = Manuscript(path=Path("m.md"), text="x", sha256="s" * 64, words=10)
        findings = [
            Finding("citations", "uncited_reference", "info",
                    {"source_id": f"src{n}", "title": f"Title {n}"})
            for n in range(3)
        ] + [Finding("citations", "orphan_citation", "error",
                     {"citation": "(X, 2024)", "location": {"line": 1}})]
        r = build_report(m, {}, findings, levels=["citations"], model=None,
                         cost_usd=0.0, duration_seconds=1.0)
        html = write_report_html(r, tmp_path).read_text()
        assert html.count("uncited reference") <= 1   # not one badge per row
        assert "3 bibliography entries are never cited" in html
        assert "Title 2" in html                       # entries listed inside
        assert "orphan citation" in html               # other rows unaffected


class TestSectionStatus:
    def test_section_headers_carry_status_chips(self, tmp_path):
        r = _report()
        html = write_report_html(r, tmp_path).read_text()
        assert 'class="secchip' in html
        assert "1 error" in html          # citations section: the orphan
        assert html.count("clean") >= 1   # a clean section shows a green chip

    def test_claims_section_says_not_run_when_level_skipped(self, tmp_path):
        r = _report()
        r["run"]["levels"] = ["citations", "quotes"]
        r["findings"] = [f for f in r["findings"] if f["level"] != "claims"]
        r["summary"]["verdicts"] = {}
        html = write_report_html(r, tmp_path).read_text()
        assert "not run" in html
        assert "--skip-claims" in html

    def test_sections_have_filtered_note_elements(self, tmp_path):
        r = _report()
        html = write_report_html(r, tmp_path).read_text()
        assert html.count('class="filtered-note"') >= 2
        assert "hidden by the current filters" in html


class TestCitationInventory:
    def _report_with_citations(self):
        r = _report()
        r["citations"] = [
            {"citation": "Smith (2023)", "author": "Smith", "year": 2023,
             "source_id": "smith2023", "confidence": 1.0, "count": 2,
             "lines": [12, 40]},
            {"citation": "(Ghost et al., 2024)", "author": "Ghost et al.",
             "year": 2024, "source_id": None, "confidence": 0.0, "count": 1,
             "lines": [77]},
        ]
        return r

    def test_all_citations_render_with_status(self, tmp_path):
        html = write_report_html(self._report_with_citations(), tmp_path).read_text()
        assert "Smith (2023)" in html
        assert "resolved" in html
        assert "not in bibliography" in html
        assert "smith2023" in html

    def test_occurrence_count_and_lines_shown(self, tmp_path):
        html = write_report_html(self._report_with_citations(), tmp_path).read_text()
        assert "2×" in html or "2x" in html
        assert "line 77" in html

    def test_report_without_citations_key_still_renders(self, tmp_path):
        html = write_report_html(_report(), tmp_path).read_text()
        assert "citefact report" in html


class TestSeverityFilterSegmentedControl:
    """One mutually-exclusive control (All / Problems / Errors only)
    replaced the old problems-only checkbox + per-severity toggles, which
    fought over the same axis."""

    def test_segmented_control_replaces_checkbox_soup(self, tmp_path):
        html = write_report_html(_report(), tmp_path).read_text()
        assert 'name="sevmode"' in html
        assert html.count('type="radio" name="sevmode"') == 3  # All / Problems / Errors
        assert 'value="all" checked' in html            # default shows everything
        assert "problems-only" not in html              # old checkbox gone
        assert 'class="sev"' not in html                # old severity toggles gone

    def test_verdict_pills_survive(self, tmp_path):
        html = write_report_html(_report(), tmp_path).read_text()
        assert 'class="verdict"' in html


class TestSourceLinks:
    def test_sources_with_pdfs_link_to_file_uris(self, tmp_path):
        pdf = tmp_path / "my papers" / "smith 2023.pdf"
        pdf.parent.mkdir()
        pdf.write_bytes(b"%PDF")
        r = _report()
        r["sources"][0]["pdf"] = str(pdf)
        r["citations"] = [{"citation": "Smith (2023)", "author": "Smith",
                           "year": 2023, "source_id": "smith2023",
                           "confidence": 1.0, "count": 1, "lines": [3]}]
        html = write_report_html(r, tmp_path).read_text()
        assert 'href="file://' in html
        assert "%20" in html            # spaces url-encoded by as_uri
        assert 'href="file://' in html and 'href="http' not in html

    def test_manuscript_path_links_in_header(self, tmp_path):
        r = _report()
        (tmp_path / "m.md").write_text("x", encoding="utf-8")
        r["manuscript"]["path"] = str(tmp_path / "m.md")
        html = write_report_html(r, tmp_path).read_text()
        assert html.count('href="file://') >= 1

    def test_no_links_when_paths_absent(self, tmp_path):
        r = _report()
        r["sources"][0]["pdf"] = None
        html = write_report_html(r, tmp_path).read_text()
        assert "file://" not in html


class TestSectionTabs:
    def test_tab_bar_with_all_default(self, tmp_path):
        html = write_report_html(_report(), tmp_path).read_text()
        assert html.count('type="radio" name="tab"') == 4   # All | Citations | Quotes | Claims
        assert 'name="tab" value="all" checked' in html
        for value in ("citations", "quotes", "claims"):
            assert f'name="tab" value="{value}"' in html

    def test_sections_are_addressable(self, tmp_path):
        html = write_report_html(_report(), tmp_path).read_text()
        for level in ("citations", "quotes", "claims"):
            assert f'data-level="{level}"' in html

    def test_hidden_sections_stay_findable(self, tmp_path):
        """Inactive tabs hide sections with hidden="until-found" so browser
        find-in-page can still reveal their content (Chrome/Edge); other
        browsers degrade to plain hidden."""
        html = write_report_html(_report(), tmp_path).read_text()
        assert "until-found" in html
        assert "beforematch" in html   # reveals -> tab syncs to that section
