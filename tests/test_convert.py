import subprocess
from pathlib import Path

from citefact.ingest.convert import convert_pdf, pdf_sha256


def _completed(returncode: int, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr=stderr)


def _fake_run(effects):
    """effects: list of returncodes to emit per call; writes out.md on rc 0."""
    calls = []

    def run(cmd, **kwargs):
        rc = effects[len(calls)]
        calls.append(cmd)
        if rc == 0:
            out_dir = Path(cmd[cmd.index("--output") + 1])
            (out_dir / (Path(cmd[cmd.index("docling") + 1]).stem + ".md")).write_text("converted text")
        return _completed(rc)

    return run, calls


def test_convert_success_writes_cache(tmp_path, monkeypatch):
    pdf = tmp_path / "a.pdf"; pdf.write_bytes(b"%PDF-fake")
    run, calls = _fake_run([0])
    monkeypatch.setattr("citefact.ingest.convert.subprocess.run", run)
    text = convert_pdf(pdf, tmp_path / ".citefact")
    assert text == "converted text"
    assert (tmp_path / ".citefact/cache/sources" / f"{pdf_sha256(pdf)}.md").exists()
    assert len(calls) == 1 and "--no-ocr" not in calls[0]


def test_cache_hit_skips_subprocess(tmp_path, monkeypatch):
    pdf = tmp_path / "a.pdf"; pdf.write_bytes(b"%PDF-fake")
    cache = tmp_path / ".citefact/cache/sources"; cache.mkdir(parents=True)
    (cache / f"{pdf_sha256(pdf)}.md").write_text("cached")
    monkeypatch.setattr(
        "citefact.ingest.convert.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    assert convert_pdf(pdf, tmp_path / ".citefact") == "cached"


def test_crash_exit_retries_with_no_ocr(tmp_path, monkeypatch):
    pdf = tmp_path / "a.pdf"; pdf.write_bytes(b"%PDF-fake")
    run, calls = _fake_run([139, 0])
    monkeypatch.setattr("citefact.ingest.convert.subprocess.run", run)
    assert convert_pdf(pdf, tmp_path / ".citefact") == "converted text"
    assert len(calls) == 2 and "--no-ocr" in calls[1]


def test_plain_failure_does_not_retry(tmp_path, monkeypatch):
    pdf = tmp_path / "a.pdf"; pdf.write_bytes(b"%PDF-fake")
    run, calls = _fake_run([1])
    monkeypatch.setattr("citefact.ingest.convert.subprocess.run", run)
    assert convert_pdf(pdf, tmp_path / ".citefact") is None
    assert len(calls) == 1


def test_pdf_sha256_failure_degrades_to_none(tmp_path, monkeypatch):
    pdf = tmp_path / "a.pdf"; pdf.write_bytes(b"%PDF-fake")
    monkeypatch.setattr(
        "citefact.ingest.convert.pdf_sha256",
        lambda p: (_ for _ in ()).throw(PermissionError("no access")),
    )
    assert convert_pdf(pdf, tmp_path / ".citefact") is None
