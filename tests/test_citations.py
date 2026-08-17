"""Tests for the citations package (parser + catalog matcher).

Parser correctness comes from the extraction tests; matcher correctness
from the resolve tests.
"""

from __future__ import annotations

import pytest

from citefact.citations import pick_parser, resolve_to_catalog
from citefact.citations.apa import ApaParser
from citefact.citations.base import Citation


# --------------------------------------------------------------------------- #
# Parser — preprocess + extraction
# --------------------------------------------------------------------------- #


class TestApaPreprocess:
    def test_strips_zotero_markdown_wrapper_around_year(self):
        text = "As Dratsch et al., [(2023)](https://www.zotero.org/google-docs/?couBQ3) showed…"
        out = ApaParser().preprocess(text)
        assert out == "As Dratsch et al., (2023) showed…"

    def test_strips_zotero_markdown_wrapper_around_full_citation(self):
        text = "See [(Verma et al., 2019)](https://www.zotero.org/google-docs/?oKqWbi) for details."
        out = ApaParser().preprocess(text)
        assert out == "See (Verma et al., 2019) for details."

    def test_leaves_non_zotero_markdown_links_alone(self):
        text = "See [the paper](https://example.com/foo) and (Smith, 2023)."
        out = ApaParser().preprocess(text)
        assert "[the paper](https://example.com/foo)" in out


class TestApaExtractionParenthetical:
    def test_single_parenthetical(self):
        parser = ApaParser()
        text = "Prior work (Smith, 2023) argues…"
        cites = parser.extract(parser.preprocess(text))
        assert len(cites) == 1
        assert cites[0].author_string == "Smith"
        assert cites[0].year == 2023
        assert cites[0].raw == "Smith, 2023"

    def test_parenthetical_with_et_al(self):
        parser = ApaParser()
        text = "Others (Smith et al., 2023) disagree."
        cites = parser.extract(parser.preprocess(text))
        assert len(cites) == 1
        assert cites[0].author_string == "Smith et al."
        assert cites[0].year == 2023

    def test_parenthetical_ampersand(self):
        parser = ApaParser()
        text = "Recent (Smith & Jones, 2023) work."
        cites = parser.extract(parser.preprocess(text))
        assert len(cites) == 1
        assert cites[0].author_string == "Smith & Jones"

    def test_semicolon_separated_group(self):
        parser = ApaParser()
        text = "Related (Waardenburg, 2024; Waardenburg et al., 2018) work."
        cites = parser.extract(parser.preprocess(text))
        assert len(cites) == 2
        assert cites[0].author_string == "Waardenburg"
        assert cites[0].year == 2024
        assert cites[1].author_string == "Waardenburg et al."
        assert cites[1].year == 2018


class TestApaExtractionNarrative:
    def test_single_author_narrative(self):
        parser = ApaParser()
        text = "Falco (2015) argued that…"
        cites = parser.extract(parser.preprocess(text))
        assert len(cites) == 1
        assert cites[0].author_string == "Falco"
        assert cites[0].year == 2015

    def test_et_al_narrative(self):
        parser = ApaParser()
        text = "Verma et al. (2019) demonstrated that…"
        cites = parser.extract(parser.preprocess(text))
        assert len(cites) == 1
        assert cites[0].author_string == "Verma et al."

    def test_two_authors_ampersand_narrative(self):
        parser = ApaParser()
        text = "Elliott & MacCarthaigh (2025) found that…"
        cites = parser.extract(parser.preprocess(text))
        assert len(cites) == 1
        assert cites[0].author_string == "Elliott & MacCarthaigh"

    def test_trailing_comma_between_author_and_year(self):
        # Zotero exports "Author et al., [(2023)](url)" which preprocesses
        # to "Author et al., (2023)" — the narrative regex must accept
        # the stray comma between the author block and the year paren.
        parser = ApaParser()
        text = "Frazer et al., (2024) demonstrate that…"
        cites = parser.extract(parser.preprocess(text))
        assert len(cites) == 1
        assert cites[0].author_string == "Frazer et al."
        assert cites[0].year == 2024


class TestApaExtractionZoteroMarkdown:
    def test_zotero_markdown_year_link_pattern(self):
        parser = ApaParser()
        text = "As Dratsch et al., [(2023)](https://www.zotero.org/google-docs/?couBQ3) showed"
        pre = parser.preprocess(text)
        cites = parser.extract(pre)
        assert len(cites) == 1
        assert cites[0].author_string == "Dratsch et al."
        assert cites[0].year == 2023

    def test_zotero_markdown_full_cite_link_pattern(self):
        parser = ApaParser()
        text = "See [(Verma et al., 2019)](https://www.zotero.org/google-docs/?oKqWbi) for details."
        pre = parser.preprocess(text)
        cites = parser.extract(pre)
        assert len(cites) == 1
        assert cites[0].author_string == "Verma et al."
        assert cites[0].year == 2019

    def test_zotero_markdown_single_author_narrative(self):
        parser = ApaParser()
        text = "Strauß [(2018)](https://www.zotero.org/google-docs/?yzoa40) argues that…"
        pre = parser.preprocess(text)
        cites = parser.extract(pre)
        assert len(cites) == 1
        assert cites[0].author_string == "Strauß"
        assert cites[0].year == 2018


class TestApaDetect:
    def test_detect_scores_citation_dense_text_highly(self):
        parser = ApaParser()
        text = (
            "Smith (2023) found that. Related work (Jones, 2024; Brown et al., 2025) "
            "extends this. Davis & Lee (2024) report."
        )
        score = parser.detect(text)
        assert score > 0.5

    def test_detect_scores_empty_text_zero(self):
        assert ApaParser().detect("") == 0.0

    def test_detect_scores_citation_free_text_low(self):
        parser = ApaParser()
        text = "This paragraph contains no citations at all, just prose about a topic. " * 20
        assert parser.detect(text) < 0.1


class TestPickParser:
    def test_picks_apa_on_apa_text(self):
        text = "Smith (2023) found that (Jones et al., 2024) agree."
        picked = pick_parser(text)
        assert picked.name == "apa"


# --------------------------------------------------------------------------- #
# Matcher — resolve_to_catalog
# --------------------------------------------------------------------------- #


@pytest.fixture
def catalog():
    return {
        "simkuteXAI2022": {
            "authors": "Simkute A., Surana A., Luger E.",
            "year": 2022,
            "title": "XAI for learning",
        },
        "bornmannGrowth2015": {
            "authors": "Bornmann Lutz, Mutz Rüdiger",
            "year": 2015,
            "title": "Growth rates of modern science",
            "_source": "reference",
        },
        "elliottAccountability2025": {
            "authors": "Elliott M.T.J., MacCarthaigh M.",
            "year": 2025,
            "title": "Accountability and AI",
        },
        "floridi2019": {
            "authors": ["Floridi, L.", "Cowls, J."],
            "year": 2019,
            "title": "A Unified Framework of Five Principles for AI in Society",
            "_source": "reference",
        },
    }


class TestResolveToCatalog:
    def test_parenthetical_match_corpus(self, catalog):
        cite = Citation("Simkute et al., 2022", "Simkute et al.", 2022, 0, 19)
        matches = resolve_to_catalog([cite], catalog)
        assert len(matches) == 1
        assert matches[0].paper_id == "simkuteXAI2022"
        assert matches[0].confidence >= 0.8
        assert matches[0].source is None  # corpus entry has no _source key

    def test_reference_source_is_surfaced(self, catalog):
        cite = Citation("Bornmann, 2015", "Bornmann", 2015, 0, 14)
        matches = resolve_to_catalog([cite], catalog)
        assert matches[0].paper_id == "bornmannGrowth2015"
        assert matches[0].source == "reference"

    def test_ampersand_does_not_break_surname_match(self, catalog):
        cite = Citation(
            "Elliott & MacCarthaigh, 2025", "Elliott & MacCarthaigh", 2025, 0, 28
        )
        matches = resolve_to_catalog([cite], catalog)
        assert matches[0].paper_id == "elliottAccountability2025"

    def test_authors_as_list_resolves(self, catalog):
        cite = Citation("Floridi, 2019", "Floridi", 2019, 0, 15)
        matches = resolve_to_catalog([cite], catalog)
        assert matches[0].paper_id == "floridi2019"

    def test_wrong_year_rejects_match(self, catalog):
        cite = Citation("Simkute et al., 1999", "Simkute et al.", 1999, 0, 20)
        matches = resolve_to_catalog([cite], catalog)
        assert matches[0].paper_id is None
        assert matches[0].confidence == 0.0

    def test_unknown_author_rejects_match(self, catalog):
        cite = Citation("Nobody et al., 2022", "Nobody et al.", 2022, 0, 19)
        matches = resolve_to_catalog([cite], catalog)
        assert matches[0].paper_id is None

    def test_empty_author_string_returns_unmatched(self, catalog):
        cite = Citation("", "", 2022, 0, 0)
        matches = resolve_to_catalog([cite], catalog)
        assert matches[0].paper_id is None

    def test_preserves_input_order(self, catalog):
        cites = [
            Citation("Simkute et al., 2022", "Simkute et al.", 2022, 0, 20),
            Citation("Elliott, 2025", "Elliott", 2025, 25, 40),
        ]
        matches = resolve_to_catalog(cites, catalog)
        assert [m.paper_id for m in matches] == [
            "simkuteXAI2022",
            "elliottAccountability2025",
        ]


class TestMultiAuthorNarrative:
    """Three-plus-author narrative citations, observed in a real manuscript:
    the parser used to capture only the LAST surname ("Lifshitz-Assaf
    (2021)"), which the matcher then failed to resolve as a first author."""

    def test_three_authors_oxford_and(self):
        parser = ApaParser()
        text = "Lebovitz, Levina, and Lifshitz-Assaf (2021) show that things happen."
        cites = parser.extract(parser.preprocess(text))
        assert len(cites) == 1
        assert cites[0].author_string == "Lebovitz, Levina, and Lifshitz-Assaf"
        assert cites[0].year == 2021

    def test_three_authors_ampersand(self):
        parser = ApaParser()
        text = "Wolfswinkel, Furtmueller & Wilderom (2013) propose five phases."
        cites = parser.extract(parser.preprocess(text))
        assert len(cites) == 1
        assert cites[0].author_string == "Wolfswinkel, Furtmueller & Wilderom"

    def test_first_surname_resolves_against_catalog(self):
        from citefact.citations import resolve_to_catalog

        parser = ApaParser()
        text = "Urquhart, Lehmann, and Myers (2010) set out five guidelines."
        cites = parser.extract(parser.preprocess(text))
        catalog = {"urquhart2010": {
            "authors": "Urquhart, Cathy and Lehmann, Hans and Myers, Michael",
            "year": 2010, "title": "Putting the theory back into grounded theory",
        }}
        matches = resolve_to_catalog(cites, catalog)
        assert matches[0].paper_id == "urquhart2010"

    def test_prose_before_citation_is_not_swallowed(self):
        parser = ApaParser()
        text = "As previously shown, Smith (2023) argues the point."
        cites = parser.extract(parser.preprocess(text))
        assert len(cites) == 1
        assert cites[0].author_string == "Smith"
