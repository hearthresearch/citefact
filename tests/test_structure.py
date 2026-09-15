from citefact.ingest.structure import block_quotes, excluded_spans


class TestBlockQuotes:
    def test_single_paragraph_block_quote(self):
        text = "Intro.\n\n> The first line of the quote\n> continues on the second line.\n\nAfter.\n"
        spans = block_quotes(text)
        assert len(spans) == 1
        assert spans[0].text == "The first line of the quote continues on the second line."
        assert text[spans[0].start:spans[0].end].startswith("> The first")
        assert text[spans[0].start:spans[0].end].rstrip().endswith("second line.")

    def test_lazy_continuation_lines_belong_to_the_block(self):
        text = "> Quoted text starts here\nand lazily continues here.\n"
        assert block_quotes(text)[0].text == "Quoted text starts here and lazily continues here."

    def test_multi_paragraph_block_quote_keeps_paragraph_break_as_space(self):
        text = "> First paragraph.\n>\n> Second paragraph.\n"
        assert block_quotes(text)[0].text == "First paragraph. Second paragraph."

    def test_nested_markers_are_stripped(self):
        text = "> > Doubly quoted.\n"
        assert block_quotes(text)[0].text == "Doubly quoted."

    def test_no_block_quote(self):
        assert block_quotes("Plain paragraph.\n\nAnother one.\n") == []


class TestExcludedSpans:
    def test_fence_and_table_are_excluded(self):
        text = (
            "Prose.\n\n```\ncode \"with a quotation that is long enough\"\n```\n\n"
            "| a | b |\n|---|---|\n| \"quoted cell text long enough\" | x |\n\nMore prose.\n"
        )
        spans = excluded_spans(text)
        assert len(spans) == 2
        fence, table = spans
        assert text[fence[0]:fence[1]].startswith("```")
        assert text[table[0]:table[1]].startswith("| a |")

    def test_indented_code_block_is_excluded(self):
        text = "Prose.\n\n    indented \"code block quotation long enough\"\n\nMore.\n"
        spans = excluded_spans(text)
        assert len(spans) == 1
        assert text[spans[0][0]:spans[0][1]].lstrip().startswith("indented")

    def test_prose_only_has_no_exclusions(self):
        assert excluded_spans("Just \"a long enough quotation in prose\" here.\n") == []
