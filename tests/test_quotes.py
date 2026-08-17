from citefact.checks.quotes import check_quotes, extract_quotes, normalize
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
