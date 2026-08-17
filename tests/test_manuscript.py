from pathlib import Path

import pytest

from citefact.ingest.manuscript import load_manuscript


def test_loads_markdown(tmp_path: Path):
    p = tmp_path / "m.md"
    p.write_text("# Title\n\nSmith (2023) found things.\n", encoding="utf-8")
    m = load_manuscript(p)
    assert m.path == p
    assert "Smith (2023)" in m.text
    assert len(m.sha256) == 64
    assert m.words == 6


def test_rejects_docx(tmp_path: Path):
    p = tmp_path / "m.docx"
    p.write_bytes(b"whatever")
    with pytest.raises(ValueError, match="v0.1 supports Markdown"):
        load_manuscript(p)
