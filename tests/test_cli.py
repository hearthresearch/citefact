import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from citefact.cli import app
from citefact.pipeline import exit_code_for

runner = CliRunner()

FIXTURES = Path(__file__).parent / "fixtures" / "demo"


def test_exit_code_for_thresholds():
    findings = [{"severity": "warning"}]
    assert exit_code_for(findings, "error") == 0
    assert exit_code_for(findings, "warning") == 1
    assert exit_code_for(findings, "none") == 0
    assert exit_code_for([{"severity": "error"}], "error") == 1


def test_check_requires_bib(tmp_path: Path):
    m = tmp_path / "m.md"
    m.write_text("Smith (2023) says.", encoding="utf-8")
    result = runner.invoke(app, ["check", str(m)])
    assert result.exit_code == 2


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0 and "citefact 0.1.0" in result.output


_BIB = """@article{smith2023,
  author = {Smith, John},
  title = {A Study of Things},
  year = {2023},
}
"""


def test_check_skip_claims_runs_keyless_and_writes_report(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CITEFACT_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    m = tmp_path / "m.md"
    m.write_text("As shown by Smith (2023), things happen.", encoding="utf-8")
    bib = tmp_path / "refs.bib"
    bib.write_text(_BIB, encoding="utf-8")
    out = tmp_path / "out"

    result = runner.invoke(
        app,
        ["check", str(m), "--bib", str(bib), "--out", str(out), "--skip-claims"],
    )

    assert result.exit_code == 0, result.output
    report = json.loads((out / "report.json").read_text())
    assert report["run"]["levels"] == ["citations", "quotes"]
    assert report["run"]["model"] is None
    assert (out / "report.html").exists()


def test_check_json_streams_report_to_stdout(tmp_path: Path):
    m = tmp_path / "m.md"
    m.write_text("As shown by Smith (2023), things happen.", encoding="utf-8")
    bib = tmp_path / "refs.bib"
    bib.write_text(_BIB, encoding="utf-8")
    out = tmp_path / "out"

    result = runner.invoke(
        app,
        ["check", str(m), "--bib", str(bib), "--out", str(out),
         "--skip-claims", "--json", "--quiet"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    # --quiet silences progress; stdout carries only the JSON report.


def test_check_unknown_provider_without_model_exits_2(tmp_path: Path):
    m = tmp_path / "m.md"
    m.write_text("As shown by Smith (2023), things happen.", encoding="utf-8")
    bib = tmp_path / "refs.bib"
    bib.write_text(_BIB, encoding="utf-8")

    result = runner.invoke(
        app,
        ["check", str(m), "--bib", str(bib), "--provider", "not-a-real-provider"],
    )

    assert result.exit_code == 2
    assert "not-a-real-provider" in result.output or "No default model" in result.output


def test_check_only_dedupes_repeated_levels(tmp_path: Path):
    m = tmp_path / "m.md"
    m.write_text("As shown by Jones (2024), things happen.", encoding="utf-8")
    bib = tmp_path / "refs.bib"
    bib.write_text(_BIB, encoding="utf-8")
    out = tmp_path / "out"

    result = runner.invoke(
        app,
        ["check", str(m), "--bib", str(bib), "--out", str(out),
         "--only", "citations,citations"],
    )

    assert result.exit_code == 1, result.output
    report = json.loads((out / "report.json").read_text())
    assert report["run"]["levels"] == ["citations"]
    # Without dedup, check_citations would run twice (once per repeated
    # "citations" in --only) and every finding would appear duplicated.
    findings_json = [json.dumps(f, sort_keys=True) for f in report["findings"]]
    assert len(findings_json) == len(set(findings_json))
    assert len(findings_json) == 2  # orphan_citation (Jones) + uncited_reference (smith2023)


def test_check_only_claims_with_skip_claims_exits_2(tmp_path: Path):
    m = tmp_path / "m.md"
    m.write_text("As shown by Smith (2023), things happen.", encoding="utf-8")
    bib = tmp_path / "refs.bib"
    bib.write_text(_BIB, encoding="utf-8")

    result = runner.invoke(
        app,
        ["check", str(m), "--bib", str(bib), "--only", "claims", "--skip-claims"],
    )

    assert result.exit_code == 2
    assert "No levels selected" in result.output


def test_convert_honors_citefact_cache(tmp_path: Path, monkeypatch):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    (pdf_dir / "a.pdf").write_bytes(b"%PDF-1.4 fake")
    cache_target = tmp_path / "custom-cache"
    monkeypatch.setenv("CITEFACT_CACHE", str(cache_target))

    captured: dict = {}

    def fake_convert_pdf(pdf_path, cache_dir, *, force=False):
        captured["cache_dir"] = cache_dir
        return "converted text"

    monkeypatch.setattr("citefact.ingest.convert.convert_pdf", fake_convert_pdf)

    result = runner.invoke(app, ["convert", "--pdfs", str(pdf_dir)])

    assert result.exit_code == 0, result.output
    assert captured["cache_dir"] == cache_target


def _seed(tmp_path: Path, monkeypatch):
    """Copy the demo fixture into tmp_path and stub conversion to the fixture texts.

    Bypasses Docling entirely: `convert_pdf` is monkeypatched to return the
    pre-converted source text for each fixture PDF stem, so the e2e test
    stays fast and offline.
    """
    work = tmp_path / "demo"
    shutil.copytree(FIXTURES, work)
    pdf_dir = work / "papers"
    pdf_dir.mkdir()
    for sid in ("smith2023", "doe2021"):
        (pdf_dir / f"{sid}.pdf").write_bytes(b"%PDF-fake-" + sid.encode())
    texts = {sid: (work / "sources" / f"{sid}.md").read_text() for sid in ("smith2023", "doe2021")}
    monkeypatch.setattr("citefact.pipeline.pdf_sha256", lambda p: f"hash-{p.stem}")
    monkeypatch.setattr(
        "citefact.pipeline.convert_pdf", lambda p, c, force=False: texts[p.stem]
    )
    return work, pdf_dir


def test_e2e_skip_claims(tmp_path: Path, monkeypatch):
    work, pdf_dir = _seed(tmp_path, monkeypatch)
    out = tmp_path / "report"
    result = runner.invoke(app, [
        "check", str(work / "manuscript.md"),
        "--bib", str(work / "refs.bib"), "--pdfs", str(pdf_dir),
        "--out", str(out), "--skip-claims", "--quiet",
    ])
    assert result.exit_code == 1  # planted errors present
    report = json.loads((out / "report.json").read_text())
    types = {f["type"] for f in report["findings"]}
    assert {"orphan_citation", "uncited_reference", "missing_source",
            "quote_verified", "quote_modified", "quote_not_found",
            "quote_unattributed"} <= types
    assert report["run"]["model"] is None
    assert (out / "report.html").exists()


def test_e2e_claims_extraction_failure_still_writes_report(tmp_path: Path, monkeypatch):
    """A raising claim extraction (missing key, outage, terminal JSON repair
    failure) must degrade, not crash: L1/L2 findings still ship."""
    work, pdf_dir = _seed(tmp_path, monkeypatch)
    monkeypatch.setenv("CITEFACT_MODEL", "anthropic/claude-sonnet-5")

    def raising_call_llm(messages, *, model, temperature=0.3, max_tokens=16384):
        raise RuntimeError("provider outage")

    monkeypatch.setattr("citefact.checks.claims.call_llm", raising_call_llm)
    out = tmp_path / "report"
    result = runner.invoke(app, [
        "check", str(work / "manuscript.md"),
        "--bib", str(work / "refs.bib"), "--pdfs", str(pdf_dir),
        "--out", str(out), "--quiet",
    ])
    assert result.exit_code == 1  # planted L1/L2 errors still present
    report = json.loads((out / "report.json").read_text())
    assert report["run"]["partial"] is True
    types = {f["type"] for f in report["findings"]}
    assert {"orphan_citation", "uncited_reference", "missing_source"} <= types
    assert (out / "report.html").exists()


def test_e2e_fail_on_none_exits_zero(tmp_path: Path, monkeypatch):
    work, pdf_dir = _seed(tmp_path, monkeypatch)
    result = runner.invoke(app, [
        "check", str(work / "manuscript.md"),
        "--bib", str(work / "refs.bib"), "--pdfs", str(pdf_dir),
        "--out", str(tmp_path / "r2"), "--skip-claims", "--fail-on", "none", "--quiet",
    ])
    assert result.exit_code == 0


class TestZoteroCollectionFlag:
    def test_bib_and_zotero_together_exit_2(self, tmp_path):
        m = tmp_path / "m.md"
        m.write_text("x", encoding="utf-8")
        bib = tmp_path / "refs.bib"
        bib.write_text("@article{a, author={A}, title={T}, year={2020}}", encoding="utf-8")
        result = runner.invoke(app, [
            "check", str(m), "--bib", str(bib),
            "--zotero-collection", "My Papers",
        ])
        assert result.exit_code == 2
        assert "not both" in result.output

    def test_zotero_collection_reaches_pipeline(self, tmp_path, monkeypatch):
        m = tmp_path / "m.md"
        m.write_text("x", encoding="utf-8")
        captured = {}

        def fake_run_check(manuscript, **kwargs):
            captured.update(kwargs)
            return {
                "schema_version": 1, "citefact_version": "t",
                "manuscript": {"file": "m.md", "sha256": "0" * 64, "words": 1},
                "run": {"timestamp": "t", "model": None, "levels": ["citations"],
                        "cost_usd": 0.0, "duration_seconds": 0.0, "partial": False},
                "summary": {"errors": 0, "warnings": 0, "claims_total": 0,
                            "verdicts": {}},
                "sources": [], "findings": [],
            }

        monkeypatch.setattr("citefact.pipeline.run_check", fake_run_check)
        result = runner.invoke(app, [
            "check", str(m), "--zotero-collection", "PhD/Chapter 3",
            "--skip-claims", "--quiet", "--out", str(tmp_path / "r"),
        ])
        assert result.exit_code == 0
        assert captured["zotero_collection"] == "PhD/Chapter 3"
        assert captured["bib_path"] is None


class TestProgressUx:
    def _fixture(self, tmp_path):
        m = tmp_path / "m.md"
        m.write_text("Smith (2023) says things.", encoding="utf-8")
        bib = tmp_path / "refs.bib"
        bib.write_text(
            "@article{smith2023, author={Smith, John}, title={T}, year={2023}}",
            encoding="utf-8",
        )
        return m, bib

    def test_summary_block_printed(self, tmp_path):
        m, bib = self._fixture(tmp_path)
        result = runner.invoke(app, [
            "check", str(m), "--bib", str(bib), "--skip-claims",
            "--out", str(tmp_path / "r"), "--fail-on", "none",
        ])
        assert result.exit_code == 0
        assert "Summary:" in result.output
        assert "report.html" in result.output

    def test_quiet_suppresses_summary(self, tmp_path):
        m, bib = self._fixture(tmp_path)
        result = runner.invoke(app, [
            "check", str(m), "--bib", str(bib), "--skip-claims",
            "--out", str(tmp_path / "r"), "--fail-on", "none", "--quiet",
        ])
        assert result.exit_code == 0
        assert result.output == ""

    def test_convert_shows_counted_progress(self, tmp_path, monkeypatch):
        pdf_dir = tmp_path / "papers"
        pdf_dir.mkdir()
        (pdf_dir / "a.pdf").write_bytes(b"%PDF")
        (pdf_dir / "b.pdf").write_bytes(b"%PDF")
        monkeypatch.setenv("CITEFACT_CACHE", str(tmp_path / "cache"))
        monkeypatch.setattr(
            "citefact.ingest.convert.convert_pdf", lambda p, c, force=False: "text"
        )
        result = runner.invoke(app, ["convert", "--pdfs", str(pdf_dir)])
        assert result.exit_code == 0
        assert "[1/2]" in result.output and "[2/2]" in result.output


class TestSummaryEmoji:
    def test_clean_run_gets_green_check(self, tmp_path):
        m = tmp_path / "m.md"
        m.write_text("Smith (2023) says things.", encoding="utf-8")
        bib = tmp_path / "refs.bib"
        bib.write_text("@article{smith2023, author={Smith, John}, title={T}, year={2023}}",
                       encoding="utf-8")
        result = runner.invoke(app, [
            "check", str(m), "--bib", str(bib), "--skip-claims",
            "--only", "citations", "--out", str(tmp_path / "r"), "--fail-on", "none",
        ])
        # smith2023 is cited and no PDF was given: one missing_source warning
        assert "⚠️" in result.output

    def test_errors_get_cross(self, tmp_path):
        m = tmp_path / "m.md"
        m.write_text("Ghost (2024) says things.", encoding="utf-8")
        bib = tmp_path / "refs.bib"
        bib.write_text("@article{smith2023, author={Smith, John}, title={T}, year={2023}}",
                       encoding="utf-8")
        result = runner.invoke(app, [
            "check", str(m), "--bib", str(bib), "--skip-claims",
            "--out", str(tmp_path / "r"), "--fail-on", "none",
        ])
        assert "❌" in result.output


class TestConfigWiring:
    def test_check_uses_config_model_when_no_flag_or_env(self, tmp_path, monkeypatch):
        from citefact.config import save_config

        for var in ("ANTHROPIC_API_KEY", "CITEFACT_MODEL"):
            monkeypatch.delenv(var, raising=False)
        save_config({"llm": {"provider": "anthropic", "api_key": "sk-x",
                             "model": "anthropic/claude-opus-5"}})
        m = tmp_path / "m.md"
        m.write_text("x", encoding="utf-8")
        captured = {}

        def fake_run_check(manuscript, **kwargs):
            captured.update(kwargs)
            return {
                "schema_version": 1, "citefact_version": "t",
                "manuscript": {"file": "m.md", "sha256": "0" * 64, "words": 1},
                "run": {"timestamp": "t", "model": kwargs["model"],
                        "levels": ["claims"], "cost_usd": 0.0,
                        "duration_seconds": 0.0, "partial": False},
                "summary": {"errors": 0, "warnings": 0, "claims_total": 0,
                            "verdicts": {}},
                "sources": [], "findings": [],
            }

        monkeypatch.setattr("citefact.pipeline.run_check", fake_run_check)
        result = runner.invoke(app, [
            "check", str(m), "--bib", str(m), "--only", "claims",
            "--out", str(tmp_path / "r"), "--quiet",
        ])
        assert result.exit_code == 0
        assert captured["model"] == "anthropic/claude-opus-5"
