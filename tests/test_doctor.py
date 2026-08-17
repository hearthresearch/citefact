"""Tests for environment diagnostics (doctor) and guided setup."""

from __future__ import annotations

import subprocess

from typer.testing import CliRunner

from citefact.cli import app
from citefact.doctor import (
    check_docling,
    check_llm_env,
    check_uv,
    check_zotero,
    run_doctor,
)

runner = CliRunner()


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


class TestChecks:
    def test_uv_missing(self, monkeypatch):
        monkeypatch.setattr("citefact.doctor.shutil.which", lambda n: None)
        c = check_uv()
        assert c.status == "missing"
        assert "astral" in c.hint or "uv" in c.hint

    def test_uv_present(self, monkeypatch):
        monkeypatch.setattr("citefact.doctor.shutil.which", lambda n: "/usr/bin/uvx")
        assert check_uv().status == "ok"

    def test_docling_ready(self, monkeypatch):
        monkeypatch.setattr("citefact.doctor.shutil.which", lambda n: "/usr/bin/uvx")
        monkeypatch.setattr(
            "citefact.doctor.subprocess.run",
            lambda *a, **k: _completed(stdout="Docling version: 2.97.0\n"),
        )
        c = check_docling()
        assert c.status == "ok"
        assert "2.97.0" in c.detail

    def test_docling_without_uv_is_missing(self, monkeypatch):
        monkeypatch.setattr("citefact.doctor.shutil.which", lambda n: None)
        assert check_docling().status == "missing"

    def test_docling_timeout_means_not_warmed(self, monkeypatch):
        monkeypatch.setattr("citefact.doctor.shutil.which", lambda n: "/usr/bin/uvx")

        def boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="uvx", timeout=1)

        monkeypatch.setattr("citefact.doctor.subprocess.run", boom)
        c = check_docling()
        assert c.status == "warn"
        assert "setup" in c.hint

    def test_llm_env_with_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
        c = check_llm_env()
        assert c.status == "ok"
        assert "ANTHROPIC_API_KEY" in c.detail
        assert "sk-secret" not in c.detail  # never leak values

    def test_llm_env_without_keys_warns(self, monkeypatch):
        for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CITEFACT_MODEL"):
            monkeypatch.delenv(var, raising=False)
        c = check_llm_env()
        assert c.status == "warn"
        assert "--skip-claims" in c.hint

    def test_zotero_reachable(self, monkeypatch):
        monkeypatch.setattr("citefact.doctor._http_ok", lambda url, timeout=2.0: True)
        assert check_zotero().status == "ok"

    def test_zotero_unreachable_is_warn_not_missing(self, monkeypatch):
        monkeypatch.setattr("citefact.doctor._http_ok", lambda url, timeout=2.0: False)
        c = check_zotero()
        assert c.status == "warn"  # optional feature, never an error

    def test_run_doctor_returns_all_checks(self, monkeypatch):
        monkeypatch.setattr("citefact.doctor.shutil.which", lambda n: None)
        monkeypatch.setattr("citefact.doctor._http_ok", lambda url, timeout=2.0: False)
        names = [c.name for c in run_doctor()]
        assert "python" in names and "uv" in names and "docling" in names
        assert "llm" in names and "zotero" in names


class TestDoctorCommand:
    def test_doctor_prints_all_checks_and_exits_0(self, monkeypatch):
        monkeypatch.setattr("citefact.doctor.shutil.which", lambda n: None)
        monkeypatch.setattr("citefact.doctor._http_ok", lambda url, timeout=2.0: False)
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        for label in ("python", "uv", "docling", "llm", "zotero"):
            assert label in result.output.lower()


class TestSetupCommand:
    def test_setup_skips_warm_when_docling_ready(self, monkeypatch):
        monkeypatch.setattr("citefact.doctor.shutil.which", lambda n: "/usr/bin/uvx")
        monkeypatch.setattr(
            "citefact.doctor.subprocess.run",
            lambda *a, **k: _completed(stdout="Docling version: 2.97.0\n"),
        )
        monkeypatch.setattr("citefact.doctor._http_ok", lambda url, timeout=2.0: True)
        warmed = []
        monkeypatch.setattr("citefact.cli._warm_docling", lambda: warmed.append(1))
        result = runner.invoke(app, ["setup"])
        assert result.exit_code == 0
        assert warmed == []
        assert "ANTHROPIC_API_KEY" in result.output  # export suggestion

    def test_setup_warms_docling_when_not_ready(self, monkeypatch):
        monkeypatch.setattr("citefact.doctor.shutil.which", lambda n: "/usr/bin/uvx")

        def timeout(*a, **k):
            raise subprocess.TimeoutExpired(cmd="uvx", timeout=1)

        monkeypatch.setattr("citefact.doctor.subprocess.run", timeout)
        monkeypatch.setattr("citefact.doctor._http_ok", lambda url, timeout=2.0: False)
        warmed = []
        monkeypatch.setattr("citefact.cli._warm_docling", lambda: warmed.append(1))
        result = runner.invoke(app, ["setup"])
        assert result.exit_code == 0
        assert warmed == [1]


class TestLlmCheckOrigin:
    def test_config_file_key_reports_config_origin(self, monkeypatch):
        from citefact.config import save_config

        for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CITEFACT_MODEL"):
            monkeypatch.delenv(var, raising=False)
        save_config({"llm": {"provider": "anthropic", "api_key": "sk-x"}})
        c = check_llm_env()
        assert c.status == "ok"
        assert "config file" in c.detail
        assert "sk-x" not in c.detail


class TestInteractiveSetup:
    def _base_env(self, monkeypatch):
        monkeypatch.setattr("citefact.doctor.shutil.which", lambda n: "/usr/bin/uvx")
        monkeypatch.setattr(
            "citefact.doctor.subprocess.run",
            lambda *a, **k: subprocess.CompletedProcess(
                args=[], returncode=0, stdout="Docling version: 2.97.0\n", stderr=""),
        )
        monkeypatch.setattr("citefact.doctor._http_ok", lambda url, timeout=2.0: True)
        for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CITEFACT_MODEL"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setattr("citefact.cli._stdin_isatty", lambda: True)

    def test_saves_validated_key_to_config(self, monkeypatch):
        from citefact.config import load_config

        self._base_env(monkeypatch)
        monkeypatch.setattr(
            "citefact.cli._validate_key",
            lambda provider, key: (True, "anthropic/claude-sonnet-5"),
        )
        result = runner.invoke(app, ["setup"], input="y\nanthropic\nsk-test-123\n")
        assert result.exit_code == 0
        assert load_config()["llm"]["api_key"] == "sk-test-123"
        assert "Saved" in result.output
        assert "sk-test-123" not in result.output  # never echo the key

    def test_declining_saves_nothing(self, monkeypatch):
        from citefact.config import load_config

        self._base_env(monkeypatch)
        result = runner.invoke(app, ["setup"], input="n\n")
        assert result.exit_code == 0
        assert load_config() == {}

    def test_failed_validation_offers_save_anyway(self, monkeypatch):
        from citefact.config import load_config

        self._base_env(monkeypatch)
        monkeypatch.setattr(
            "citefact.cli._validate_key", lambda provider, key: (False, "auth error"),
        )
        result = runner.invoke(app, ["setup"], input="y\nanthropic\nsk-bad\nn\n")
        assert result.exit_code == 0
        assert load_config() == {}
        assert "auth error" in result.output

    def test_non_interactive_prints_instructions(self, monkeypatch):
        self._base_env(monkeypatch)
        monkeypatch.setattr("citefact.cli._stdin_isatty", lambda: False)
        result = runner.invoke(app, ["setup"])
        assert result.exit_code == 0
        assert "export ANTHROPIC_API_KEY" in result.output
