from citefact.models import Finding, Source, line_of


def test_finding_to_dict_merges_details():
    f = Finding(
        level="citations", type="orphan_citation", severity="error",
        details={"citation": "(Smith, 2024)", "location": {"line": 12}},
    )
    d = f.to_dict()
    assert d["level"] == "citations"
    assert d["citation"] == "(Smith, 2024)"
    assert d["location"] == {"line": 12}


def test_source_converted_is_none_check_not_truthiness():
    s = Source(id="x", title="T", authors="A", year=2020, text="")
    assert s.converted is True  # empty string is a valid converted text


def test_line_of():
    assert line_of("a\nb\nc", 0) == 1
    assert line_of("a\nb\nc", 2) == 2
    assert line_of("a\nb\nc", 4) == 3
