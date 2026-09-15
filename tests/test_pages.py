from citefact.ingest.pages import PageMap, offset_from_bib_pages, offset_from_furniture


def _block(page, text, layer="furniture", md_start=None, md_end=None):
    return {"page": page, "text": text, "layer": layer, "md_start": md_start, "md_end": md_end}


class TestOffsetFromFurniture:
    def test_consistent_bare_numbers_give_the_offset(self):
        blocks = [_block(2, "225"), _block(3, "226"), _block(4, "227"), _block(5, "Journal header")]
        assert offset_from_furniture(blocks) == 223

    def test_numbers_inside_prose_are_ignored(self):
        blocks = [_block(1, "Neuropsychopharmacology (2015) 40, 1979"), _block(2, "1"), _block(3, "1"), _block(4, "1")]
        assert offset_from_furniture(blocks) is None

    def test_body_blocks_are_ignored(self):
        blocks = [_block(1, "225", layer="body"), _block(2, "226", layer="body"), _block(3, "227", layer="body")]
        assert offset_from_furniture(blocks) is None

    def test_fewer_than_three_agreeing_pages_is_unknown(self):
        assert offset_from_furniture([_block(1, "10"), _block(2, "11")]) is None

    def test_one_outlier_does_not_break_a_clear_majority(self):
        blocks = [_block(1, "1"), _block(2, "2"), _block(3, "3"), _block(4, "4"), _block(5, "2019")]
        assert offset_from_furniture(blocks) == 0


class TestOffsetFromBibPages:
    def test_range_matching_page_count(self):
        assert offset_from_bib_pages("233--250", page_count=18) == 232

    def test_range_not_matching_page_count_is_unknown(self):
        assert offset_from_bib_pages("233--250", page_count=12) is None

    def test_missing_or_malformed(self):
        assert offset_from_bib_pages(None, page_count=5) is None
        assert offset_from_bib_pages("e12345", page_count=5) is None
        assert offset_from_bib_pages("233", page_count=5) is None


class TestPageMap:
    def test_pdf_page_at_offset_and_printed_label(self):
        pm = PageMap(spans=[(0, 100, 1), (100, 250, 2), (260, 300, 3)], page_count=3, offset=224)
        assert pm.pdf_page_at(50) == 1
        assert pm.pdf_page_at(120) == 2
        assert pm.pdf_page_at(255) == 2  # whitespace between blocks: page of the block before
        assert PageMap(spans=[], page_count=1).pdf_page_at(0) is None
        assert pm.printed(2) == 226

    def test_printed_is_none_without_offset(self):
        pm = PageMap(spans=[(0, 10, 1)], page_count=1, offset=None)
        assert pm.printed(1) is None

    def test_with_bib_pages_fills_a_missing_offset_only(self):
        pm = PageMap(spans=[], page_count=3, offset=None).with_bib_pages("10--12")
        assert pm.offset == 9
        kept = PageMap(spans=[], page_count=3, offset=5).with_bib_pages("10--12")
        assert kept.offset == 5
