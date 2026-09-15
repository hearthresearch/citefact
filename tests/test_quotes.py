from citefact.checks.quotes import check_quotes, extract_quotes, normalize
from citefact.citations.apa import ApaParser
from citefact.checks.quotes import normalize_with_map
from citefact.ingest.pages import PageMap
from citefact.citations.base import Citation, MatchedCitation
from citefact.models import Source

SOURCE_TEXT = (
    "Introduction. The eval-uation revealed that participants overwhelmingly "
    "preferred the assisted work‐flow in every measured dimension. Methods follow."
)


def _sources():
    return {"smith2023": Source(id="smith2023", title="T", authors="Smith", year=2023, text=SOURCE_TEXT)}


def _cite(start):
    raw = "(Smith, 2023)"
    return MatchedCitation(Citation(raw, "Smith", 2023, start, start + len(raw)), "smith2023", 0.95)


class TestNormalize:
    def test_hyphenation_and_unicode_hyphen(self):
        assert "evaluation" in normalize("eval-\nuation")
        assert "workflow" in normalize("work‐flow")

    def test_ligatures_quotes_case_whitespace(self):
        assert normalize("The ﬁnal  “Word”") == 'the final "word"'


class TestExtractQuotes:
    def test_curly_and_straight_quotes(self):
        text = 'A “first long enough quotation here” and "second long enough quotation".'
        spans = [q.text for q in extract_quotes(text)]
        assert spans == ["first long enough quotation here", "second long enough quotation"]

    def test_short_scare_quotes_skipped(self):
        assert extract_quotes('The "so-called" effect.') == []


class TestCheckQuotes:
    def test_verbatim_quote_passes(self):
        text = 'They "preferred the assisted workflow in every measured dimension" (Smith, 2023).'
        matched = [_cite(text.index("(Smith"))]
        findings = check_quotes(text, matched, _sources())
        assert [f.type for f in findings] == ["quote_verified"]

    def test_altered_quote_flags_modified_with_similarity(self):
        # Observed partial_ratio_alignment score for this fixture: 89.4 (within 80..100).
        text = 'They "preferred the assisted workflow in every single measured dimension" (Smith, 2023).'
        matched = [_cite(text.index("(Smith"))]
        findings = check_quotes(text, matched, _sources())
        assert findings[0].type == "quote_modified"
        assert findings[0].severity == "error"
        assert 80 <= findings[0].details["similarity"] < 100
        assert "closest_match" in findings[0].details

    def test_fabricated_quote_not_found(self):
        text = 'They "reported a completely different outcome altogether" (Smith, 2023).'
        matched = [_cite(text.index("(Smith"))]
        findings = check_quotes(text, matched, _sources())
        assert findings[0].type == "quote_not_found"

    def test_quote_without_citation_is_unattributed(self):
        text = 'Someone wrote "a rather long quotation with no citation nearby at all".'
        findings = check_quotes(text, [], _sources())
        assert findings[0].type == "quote_unattributed"
        assert findings[0].severity == "warning"

    def test_quote_near_unresolved_citation_yields_no_finding(self):
        # paper_id=None means the citation itself is an orphan; L1 already
        # reports it as orphan_citation, so check_quotes must stay silent.
        text = 'They "reported a completely different outcome altogether" (Nobody, 2024).'
        raw = "(Nobody, 2024)"
        start = text.index(raw)
        matched = [MatchedCitation(Citation(raw, "Nobody", 2024, start, start + len(raw)), None, 0.0)]
        findings = check_quotes(text, matched, _sources())
        assert findings == []

    def test_quote_near_citation_with_missing_source_text_yields_no_finding(self):
        # Resolved paper_id but source.text is None (PDF never converted); L1
        # already reports it as missing_source, so check_quotes must stay silent.
        text = 'They "reported a completely different outcome altogether" (Empty, 2020).'
        raw = "(Empty, 2020)"
        start = text.index(raw)
        matched = [MatchedCitation(Citation(raw, "Empty", 2020, start, start + len(raw)), "empty2020", 0.9)]
        sources = {**_sources(), "empty2020": Source(id="empty2020", title="E", authors="Empty", year=2020, text=None)}
        findings = check_quotes(text, matched, sources)
        assert findings == []

    def test_citation_beyond_adjacency_window_is_unattributed(self):
        # Gap between quote end and citation start is 313 chars, past
        # ADJACENCY_WINDOW (300), so the citation must be treated as absent.
        text = 'They "preferred the assisted workflow in every measured dimension" ' + ("x" * 310) + " (Smith, 2023)."
        matched = [_cite(text.index("(Smith"))]
        findings = check_quotes(text, matched, _sources())
        assert findings[0].type == "quote_unattributed"

    def test_nearest_of_two_in_range_citations_wins(self):
        # Smith sits right after the quote (gap 2); Doe is farther away but
        # still within the window (gap 216). The nearer one, Smith, must win.
        text = (
            'They "preferred the assisted workflow in every measured dimension" (Smith, 2023) '
            + ("x " * 100)
            + "(Doe, 2021)."
        )
        smith_start = text.index("(Smith")
        doe_raw = "(Doe, 2021)"
        doe_start = text.index(doe_raw)
        matched = [
            _cite(smith_start),
            MatchedCitation(Citation(doe_raw, "Doe", 2021, doe_start, doe_start + len(doe_raw)), "doe2021", 0.9),
        ]
        sources = {**_sources(), "doe2021": Source(id="doe2021", title="U", authors="Doe", year=2021, text="unrelated text")}
        findings = check_quotes(text, matched, sources)
        assert findings[0].details["source_id"] == "smith2023"


class TestLigatureFragments:
    """PDF extraction sometimes splits ligatures with spaces mid-word
    ("de fi nitely", "of fi ve"). Bare fi/fl/ff/ffi/ffl tokens never occur
    in real English, so joining them to their neighbours is safe."""

    def test_space_split_fi_is_joined(self):
        assert normalize("it de fi nitely picks") == "it definitely picks"

    def test_space_split_fl_is_joined(self):
        assert normalize("brie fl y noted") == "briefly noted"

    def test_real_words_ending_in_fi_are_untouched(self):
        assert normalize("the sci-fi genre") == "the scifi genre"

    def test_quote_with_split_ligature_in_source_verifies(self):
        source = {"s1": Source(id="s1", title="T", authors="A", year=2022,
                               text="once in a while, it de fi nitely picks up things")}
        text = 'They said "once in a while, it definitely picks up things" (Lebovitz, 2022).'
        cite = MatchedCitation(
            Citation("(Lebovitz, 2022)", "Lebovitz", 2022, text.index("(Leb"),
                     text.index("(Leb") + 16), "s1", 0.95)
        findings = check_quotes(text, [cite], source)
        assert [f.type for f in findings] == ["quote_verified"]


def _parsed_cites(text, paper_id="smith2023"):
    """Citations as the real APA parser emits them (span covers the
    author-year item only, not the parentheses or a page locator)."""
    return [MatchedCitation(c, paper_id, 0.95) for c in ApaParser().extract(text)]


class TestBlockQuotes:
    """APA sets quotations of 40+ words as indented blocks without
    quotation marks. In Markdown those are `>` block quotes."""

    def test_block_quote_is_extracted_with_block_form(self):
        text = "Smith argued:\n\n> The participants overwhelmingly preferred the assisted\n> workflow in every measured dimension.\n\nNext.\n"
        spans = extract_quotes(text)
        assert len(spans) == 1
        assert spans[0].form == "block"
        assert spans[0].text == "The participants overwhelmingly preferred the assisted workflow in every measured dimension."
        assert text[spans[0].start:].startswith("> The participants")

    def test_inline_quotes_default_to_inline_form(self):
        spans = extract_quotes('A "first long enough quotation here" done.')
        assert spans[0].form == "inline"

    def test_quotation_marks_inside_block_quote_are_not_extracted_twice(self):
        text = '> He said "this inner quotation is long enough" and left.\n'
        spans = extract_quotes(text)
        assert [s.form for s in spans] == ["block"]

    def test_quotation_marks_inside_fence_and_table_are_skipped(self):
        text = (
            '```\nx = "a string literal that is long enough"\n```\n\n'
            '| h |\n|---|\n| "a table cell that is long enough" |\n'
        )
        assert extract_quotes(text) == []

    def test_verbatim_block_quote_with_trailing_citation_verifies(self):
        # APA puts the citation after the block's final punctuation, inside
        # the block; it must be cut from the text compared with the source.
        text = (
            "Smith found:\n\n> Participants overwhelmingly preferred the assisted "
            "workflow in every\n> measured dimension. (Smith, 2023, p. 4)\n\nNext.\n"
        )
        matched = _parsed_cites(text)
        findings = check_quotes(text, matched, _sources())
        assert [f.type for f in findings] == ["quote_verified"]
        assert findings[0].details["form"] == "block"
        assert findings[0].details["quote"] == (
            "Participants overwhelmingly preferred the assisted workflow in every measured dimension."
        )
        assert findings[0].details["location"] == {"line": 3}

    def test_inline_findings_carry_inline_form(self):
        text = 'They "preferred the assisted workflow in every measured dimension" (Smith, 2023).'
        findings = check_quotes(text, [_cite(text.index("(Smith"))], _sources())
        assert findings[0].details["form"] == "inline"

    def test_altered_block_quote_is_flagged(self):
        text = (
            "> Participants overwhelmingly preferred the assisted workflow in every\n"
            "> single measured dimension. (Smith, 2023)\n"
        )
        matched = _parsed_cites(text)
        findings = check_quotes(text, matched, _sources())
        assert findings[0].type == "quote_modified"

    def test_citation_inside_block_but_not_at_its_end_is_kept_in_text(self):
        # A citation the quoted author wrote is part of the quotation; only a
        # trailing attribution is cut.
        text = "> As Smith (2023) wrote, the assisted workflow won in every dimension.\n"
        findings = check_quotes(text, _parsed_cites(text), _sources())
        assert "Smith (2023)" in findings[0].details["quote"]

    def test_attribution_with_two_works_is_cut_whole(self):
        text = "> Participants overwhelmingly preferred the assisted workflow in every measured dimension. (Doe, 2021; Smith, 2023)\n"
        cites = ApaParser().extract(text)
        matched = [MatchedCitation(cites[0], "doe2021", 0.9), MatchedCitation(cites[1], "smith2023", 0.95)]
        sources = {**_sources(), "doe2021": Source(id="doe2021", title="U", authors="Doe", year=2021, text="unrelated")}
        findings = check_quotes(text, matched, sources)
        assert findings[0].details["quote"].endswith("measured dimension.")


class TestNormalizeWithMap:
    """`normalize` rewrites the text; the map gives, for every character of
    the normalized text, the offset in the original it came from, so a
    match position can be turned back into a PDF page."""

    def test_normalized_text_equals_normalize(self):
        raw = "The ﬁnal  “Word” eval-\nuation de fi nitely"
        norm, _ = normalize_with_map(raw)
        assert norm == normalize(raw)

    def test_map_points_into_original(self):
        raw = "Intro.   The eval-\nuation ﬁnished."
        norm, m = normalize_with_map(raw)
        assert len(m) == len(norm)
        i = norm.index("evaluation")
        assert raw[m[i]:m[i] + 4] == "eval"
        j = norm.index("finished")
        assert raw[m[j]] == "ﬁ"
        k = norm.index("the")
        assert raw[m[k]:m[k] + 3] == "The"


def _paged_source(text, spans, offset):
    return {"smith2023": Source(id="smith2023", title="T", authors="Smith", year=2023, text=text,
                                page_map=PageMap(spans=spans, page_count=3, offset=offset))}


class TestQuotePages:
    # Source markdown: page 1 = chars [0, 60), page 2 = [60, len). Printed
    # numbering starts at 225, so PDF page 2 is printed page 226.
    TEXT = SOURCE_TEXT
    SPANS = [(0, 60, 1), (60, len(SOURCE_TEXT), 2)]

    def test_verified_quote_reports_pdf_and_printed_page(self):
        text = 'They "preferred the assisted workflow in every measured dimension" (Smith, 2023, p. 226).'
        findings = check_quotes(text, _parsed_cites(text), _paged_source(self.TEXT, self.SPANS, 224))
        f = findings[0]
        assert f.type == "quote_verified"
        assert f.details["pdf_page"] == 2
        assert f.details["page_found"] == 226
        assert f.details["page_claimed"] == [226]

    def test_wrong_page_is_a_mismatch_warning(self):
        text = 'They "preferred the assisted workflow in every measured dimension" (Smith, 2023, p. 225).'
        findings = check_quotes(text, _parsed_cites(text), _paged_source(self.TEXT, self.SPANS, 224))
        f = findings[0]
        assert f.type == "quote_page_mismatch"
        assert f.severity == "warning"
        assert f.details["page_found"] == 226
        assert f.details["page_claimed"] == [225]

    def test_unknown_printed_numbering_never_mismatches(self):
        text = 'They "preferred the assisted workflow in every measured dimension" (Smith, 2023, p. 1).'
        findings = check_quotes(text, _parsed_cites(text), _paged_source(self.TEXT, self.SPANS, None))
        f = findings[0]
        assert f.type == "quote_verified"
        assert f.details["pdf_page"] == 2
        assert f.details["page_found"] is None
        assert f.details["page_claimed"] == [1]

    def test_no_locator_verifies_with_page_found_only(self):
        text = 'They "preferred the assisted workflow in every measured dimension" (Smith, 2023).'
        findings = check_quotes(text, _parsed_cites(text), _paged_source(self.TEXT, self.SPANS, 224))
        f = findings[0]
        assert f.type == "quote_verified"
        assert f.details["page_found"] == 226
        assert "page_claimed" not in f.details

    def test_source_without_page_map_has_no_page_fields(self):
        text = 'They "preferred the assisted workflow in every measured dimension" (Smith, 2023, p. 3).'
        findings = check_quotes(text, _parsed_cites(text), _sources())
        assert findings[0].type == "quote_verified"
        assert findings[0].details["page_claimed"] == [3]
        assert "pdf_page" not in findings[0].details

    def test_modified_quote_reports_page_of_closest_match(self):
        text = 'They "preferred the assisted workflow in every single measured dimension" (Smith, 2023).'
        findings = check_quotes(text, _parsed_cites(text), _paged_source(self.TEXT, self.SPANS, 224))
        assert findings[0].type == "quote_modified"
        assert findings[0].details["pdf_page"] == 2


class TestCitationInsideInlineQuote:
    def test_citation_inside_the_marks_is_the_quoted_authors_not_the_attribution(self):
        # The quoted sentence itself cites Galal (2001); the manuscript's
        # attribution is the parenthetical after the closing mark.
        text = ('They wrote "preferred the assisted workflow in every measured dimension, '
                'as Galal (2001) had" (Smith, 2023).')
        cites = ApaParser().extract(text)
        assert [c.raw for c in cites] == ["Galal (2001)", "Smith, 2023"]
        matched = [MatchedCitation(cites[0], "galal2001", 0.9), MatchedCitation(cites[1], "smith2023", 0.95)]
        sources = {**_sources(), "galal2001": Source(id="galal2001", title="G", authors="Galal", year=2001, text="nothing")}
        findings = check_quotes(text, matched, sources)
        assert findings[0].details["source_id"] == "smith2023"

    def test_inline_quote_whose_only_citation_is_inside_is_unattributed(self):
        text = 'They wrote "preferred the assisted workflow, as Galal (2001) had" and moved on.'
        cites = ApaParser().extract(text)
        matched = [MatchedCitation(cites[0], "galal2001", 0.9)]
        findings = check_quotes(text, matched, _sources())
        assert findings[0].type == "quote_unattributed"
