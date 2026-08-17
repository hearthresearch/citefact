"""Tests for the user config file (~/.config/citefact/config.toml)."""

from __future__ import annotations

import os
import stat

import pytest

from citefact.config import apply_config_to_env, config_path, load_config, save_config


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


class TestPathAndRoundtrip:
    def test_path_respects_xdg_config_home(self, config_home):
        assert config_path() == config_home / "citefact" / "config.toml"

    def test_save_and_load_roundtrip(self, config_home):
        save_config({"llm": {"provider": "anthropic", "api_key": "sk-x",
                             "model": "anthropic/claude-sonnet-5"}})
        assert load_config()["llm"]["api_key"] == "sk-x"

    def test_save_sets_owner_only_permissions(self, config_home):
        save_config({"llm": {"api_key": "sk-x"}})
        mode = stat.S_IMODE(os.stat(config_path()).st_mode)
        assert mode == 0o600

    def test_missing_file_loads_empty(self, config_home):
        assert load_config() == {}

    def test_corrupt_file_loads_empty(self, config_home):
        config_path().parent.mkdir(parents=True)
        config_path().write_text("not [valid toml", encoding="utf-8")
        assert load_config() == {}


class TestApplyToEnv:
    def test_sets_provider_key_when_absent(self, config_home, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        save_config({"llm": {"provider": "anthropic", "api_key": "sk-from-config"}})
        apply_config_to_env()
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-from-config"

    def test_never_overrides_existing_env(self, config_home, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
        save_config({"llm": {"provider": "anthropic", "api_key": "sk-from-config"}})
        apply_config_to_env()
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-from-env"

    def test_model_feeds_citefact_model_when_absent(self, config_home, monkeypatch):
        monkeypatch.delenv("CITEFACT_MODEL", raising=False)
        save_config({"llm": {"provider": "anthropic", "api_key": "k",
                             "model": "anthropic/claude-opus-5"}})
        apply_config_to_env()
        assert os.environ["CITEFACT_MODEL"] == "anthropic/claude-opus-5"

    def test_config_model_reaches_resolve_model(self, config_home, monkeypatch):
        from citefact.llm.client import resolve_model

        monkeypatch.delenv("CITEFACT_MODEL", raising=False)
        save_config({"llm": {"provider": "anthropic", "api_key": "k",
                             "model": "anthropic/claude-opus-5"}})
        apply_config_to_env()
        assert resolve_model(None, None) == "anthropic/claude-opus-5"


class TestSecureWrite:
    def test_preexisting_loose_permissions_are_tightened(self, config_home):
        """A file left world-readable (e.g. by an older version or manual
        edit) must come out of save_config owner-only, content replaced."""
        path = config_path()
        path.parent.mkdir(parents=True)
        path.write_text("[llm]\napi_key = \"old\"\n", encoding="utf-8")
        path.chmod(0o644)
        save_config({"llm": {"api_key": "sk-new"}})
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert "sk-new" in path.read_text(encoding="utf-8")

    def test_file_is_created_with_0600_not_chmodded_after(self, config_home, monkeypatch):
        """The credential file must be BORN with 0600 (atomic via os.open),
        never created loose and tightened afterwards: a chmod-after-write
        leaves a race window where the key is world-readable."""
        import citefact.config as cfg

        def forbidden_chmod(self, *a, **k):
            raise AssertionError("chmod-after-write race: file must be created 0600")

        monkeypatch.setattr("pathlib.Path.chmod", forbidden_chmod)
        cfg.save_config({"llm": {"api_key": "sk-x"}})
        assert stat.S_IMODE(os.stat(config_path()).st_mode) == 0o600
