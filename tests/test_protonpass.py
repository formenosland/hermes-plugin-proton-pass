"""Unit tests for the Proton Pass bulk secret source."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent.secret_sources.base import ErrorKind

from protonpass import DEFAULT_TOKEN_ENV, ProtonPassSource, parse_item_titles


@pytest.fixture
def source() -> ProtonPassSource:
    return ProtonPassSource()


@pytest.fixture
def home_path(tmp_path: Path) -> Path:
    return tmp_path


def _completed(
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["pass-cli"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _enabled_cfg(binary: Path, **extra) -> dict:
    cfg = {
        "enabled": True,
        "vault": "Personal",
        "binary_path": str(binary),
    }
    cfg.update(extra)
    return cfg


def _fake_binary(tmp_path: Path) -> Path:
    binary = tmp_path / "pass-cli"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    return binary


LIST_FIXTURE = json.dumps(
    [
        {"title": "OPENAI_API_KEY"},
        {"title": "GITHUB_TOKEN"},
        {"title": "Netflix"},
        {"title": "GitHub Account"},
        {"title": "openai_api_key"},
        {"item": {"title": "ANTHROPIC_API_KEY"}},
    ]
)


class TestMalformedConfig:
    def test_fetch_never_raises_on_degenerate_config(
        self, source: ProtonPassSource, home_path: Path
    ):
        for cfg in (
            {},
            {"enabled": True},
            {"enabled": True, "vault": None},
            {"enabled": True, "timeout_seconds": "bogus"},
            None,
        ):
            result = source.fetch(cfg if isinstance(cfg, dict) else {}, home_path)
            assert result.error_kind is not None or result.ok


class TestNotConfigured:
    def test_missing_token(self, source, home_path, monkeypatch):
        monkeypatch.delenv(DEFAULT_TOKEN_ENV, raising=False)
        result = source.fetch({"enabled": True, "vault": "Personal"}, home_path)
        assert result.error_kind == ErrorKind.NOT_CONFIGURED
        assert DEFAULT_TOKEN_ENV in (result.error or "")

    def test_missing_vault(self, source, home_path, monkeypatch):
        monkeypatch.setenv(DEFAULT_TOKEN_ENV, "pst_test_token")
        result = source.fetch({"enabled": True}, home_path)
        assert result.error_kind == ErrorKind.NOT_CONFIGURED
        assert "vault" in (result.error or "")

    def test_blank_vault(self, source, home_path, monkeypatch):
        monkeypatch.setenv(DEFAULT_TOKEN_ENV, "pst_test_token")
        result = source.fetch({"enabled": True, "vault": "  "}, home_path)
        assert result.error_kind == ErrorKind.NOT_CONFIGURED


class TestBulkParse:
    def test_parse_item_titles_from_list_and_nested(self):
        titles = parse_item_titles(LIST_FIXTURE)
        assert titles == [
            "OPENAI_API_KEY",
            "GITHUB_TOKEN",
            "Netflix",
            "GitHub Account",
            "openai_api_key",
            "ANTHROPIC_API_KEY",
        ]

    def test_parse_wrapped_items_key(self):
        raw = json.dumps({"items": [{"title": "OPENAI_API_KEY"}, {"title": "Netflix"}]})
        assert parse_item_titles(raw) == ["OPENAI_API_KEY", "Netflix"]

    def test_parse_empty_and_invalid_json(self):
        assert parse_item_titles("") == []
        assert parse_item_titles("not-json") == []


class TestBinaryResolution:
    def test_missing_binary(self, source, home_path, monkeypatch):
        monkeypatch.setenv(DEFAULT_TOKEN_ENV, "pst_test_token")
        monkeypatch.setattr("protonpass.shutil.which", lambda _name: None)
        result = source.fetch({"enabled": True, "vault": "Personal"}, home_path)
        assert result.error_kind == ErrorKind.BINARY_MISSING

    def test_pinned_binary_missing_no_path_fallback(
        self, source, home_path, monkeypatch, tmp_path
    ):
        monkeypatch.setenv(DEFAULT_TOKEN_ENV, "pst_test_token")
        missing = tmp_path / "missing-pass-cli"
        result = source.fetch(
            {
                "enabled": True,
                "vault": "Personal",
                "binary_path": str(missing),
            },
            home_path,
        )
        assert result.error_kind == ErrorKind.BINARY_MISSING


class TestHappyPath:
    def test_lists_vault_and_injects_env_titles_only(
        self, source, home_path, monkeypatch, tmp_path
    ):
        binary = _fake_binary(tmp_path)
        monkeypatch.setenv(DEFAULT_TOKEN_ENV, "pst_test_token")
        calls: list[list[str]] = []

        def fake_run(argv, *, allow_env=(), extra_env=None, timeout=30):
            calls.append(list(argv))
            if argv[1:] == ["test"]:
                return _completed(returncode=0)
            if argv[1:3] == ["item", "list"]:
                return _completed(stdout=LIST_FIXTURE)
            if argv[1:4] == ["item", "view", "--"]:
                ref = argv[4]
                values = {
                    "pass://Personal/OPENAI_API_KEY/password": "sk-openai",
                    "pass://Personal/GITHUB_TOKEN/password": "ghp_token",
                    "pass://Personal/ANTHROPIC_API_KEY/password": "sk-ant",
                }
                return _completed(stdout=values.get(ref, "") + "\n")
            return _completed(returncode=1, stderr="unexpected command")

        monkeypatch.setattr("protonpass.run_secret_cli", fake_run)

        result = source.fetch(_enabled_cfg(binary), home_path)

        assert result.ok
        assert result.secrets == {
            "OPENAI_API_KEY": "sk-openai",
            "GITHUB_TOKEN": "ghp_token",
            "ANTHROPIC_API_KEY": "sk-ant",
        }
        assert "Netflix" not in result.secrets
        assert "GitHub Account" not in result.secrets
        assert "openai_api_key" not in result.secrets
        assert any("Skipped" in w and "env-var" in w for w in result.warnings)
        list_call = next(c for c in calls if c[1:3] == ["item", "list"])
        assert "--output" in list_call and "json" in list_call
        assert "--vault-name" in list_call and "Personal" in list_call
        assert "--filter-state" in list_call and "active" in list_call
        assert any(c[1:4] == ["item", "view", "--"] for c in calls)

    def test_empty_vault_listing_is_ok(self, source, home_path, monkeypatch, tmp_path):
        binary = _fake_binary(tmp_path)
        monkeypatch.setenv(DEFAULT_TOKEN_ENV, "pst_test_token")

        def fake_run(argv, *, allow_env=(), extra_env=None, timeout=30):
            if argv[1:] == ["test"]:
                return _completed(returncode=0)
            if argv[1:3] == ["item", "list"]:
                return _completed(stdout="[]")
            return _completed(returncode=1, stderr="unexpected")

        monkeypatch.setattr("protonpass.run_secret_cli", fake_run)
        result = source.fetch(_enabled_cfg(binary), home_path)
        assert result.ok
        assert result.secrets == {}
        assert any("no items" in w.lower() for w in result.warnings)

    def test_share_id_uses_share_id_flag(
        self, source, home_path, monkeypatch, tmp_path
    ):
        binary = _fake_binary(tmp_path)
        monkeypatch.setenv(DEFAULT_TOKEN_ENV, "pst_test_token")
        share_id = "AbCdEfGhIjKlMnOpQrSt"
        calls: list[list[str]] = []

        def fake_run(argv, *, allow_env=(), extra_env=None, timeout=30):
            calls.append(list(argv))
            if argv[1:] == ["test"]:
                return _completed(returncode=0)
            if argv[1:3] == ["item", "list"]:
                return _completed(stdout='[{"title": "OPENAI_API_KEY"}]')
            if argv[1:4] == ["item", "view", "--"]:
                return _completed(stdout="secret\n")
            return _completed(returncode=1)

        monkeypatch.setattr("protonpass.run_secret_cli", fake_run)
        result = source.fetch(_enabled_cfg(binary, vault=share_id), home_path)
        assert result.ok
        assert result.secrets == {"OPENAI_API_KEY": "secret"}
        list_call = next(c for c in calls if c[1:3] == ["item", "list"])
        assert "--share-id" in list_call
        assert share_id in list_call
        assert "--vault-name" not in list_call


class TestLoginPath:
    def test_test_fails_login_ok_then_list(
        self, source, home_path, monkeypatch, tmp_path
    ):
        binary = _fake_binary(tmp_path)
        monkeypatch.setenv(DEFAULT_TOKEN_ENV, "pst_test_token")

        def fake_run(argv, *, allow_env=(), extra_env=None, timeout=30):
            if argv[1:] == ["test"]:
                return _completed(returncode=1, stderr="not logged in")
            if argv[1:] == ["login"]:
                return _completed(returncode=0)
            if argv[1:3] == ["item", "list"]:
                return _completed(stdout='[{"title": "GITHUB_TOKEN"}]')
            if argv[1:4] == ["item", "view", "--"]:
                return _completed(stdout="from-login\n")
            return _completed(returncode=1, stderr="unexpected")

        monkeypatch.setattr("protonpass.run_secret_cli", fake_run)
        result = source.fetch(_enabled_cfg(binary), home_path)
        assert result.ok
        assert result.secrets == {"GITHUB_TOKEN": "from-login"}


class TestEmptyValue:
    def test_empty_password_skipped(self, source, home_path, monkeypatch, tmp_path):
        binary = _fake_binary(tmp_path)
        monkeypatch.setenv(DEFAULT_TOKEN_ENV, "pst_test_token")

        def fake_run(argv, *, allow_env=(), extra_env=None, timeout=30):
            if argv[1:] == ["test"]:
                return _completed(returncode=0)
            if argv[1:3] == ["item", "list"]:
                return _completed(
                    stdout=json.dumps(
                        [{"title": "OPENAI_API_KEY"}, {"title": "GITHUB_TOKEN"}]
                    )
                )
            if argv[1:4] == ["item", "view", "--"]:
                if argv[4].endswith("OPENAI_API_KEY/password"):
                    return _completed(stdout="   \n")
                return _completed(stdout="kept\n")
            return _completed(returncode=1)

        monkeypatch.setattr("protonpass.run_secret_cli", fake_run)
        result = source.fetch(_enabled_cfg(binary), home_path)
        assert result.ok
        assert result.secrets == {"GITHUB_TOKEN": "kept"}
        assert "OPENAI_API_KEY" not in result.secrets
        assert any("empty value" in warning.lower() for warning in result.warnings)


class TestHooks:
    def test_shape_is_bulk(self, source):
        assert source.shape == "bulk"
        assert "env" not in source.config_schema()
        assert "vault" in source.config_schema()

    def test_protected_env_vars_returns_token_env(self, source):
        assert source.protected_env_vars({}) == frozenset({DEFAULT_TOKEN_ENV})
        assert source.protected_env_vars(
            {"personal_access_token_env": "MY_PASS_TOKEN"}
        ) == frozenset({"MY_PASS_TOKEN"})

    def test_override_existing_defaults_true(self, source):
        assert source.override_existing({}) is True
        assert source.override_existing({"override_existing": True}) is True
        assert source.override_existing({"override_existing": False}) is False

    def test_disabled_by_default(self, source):
        assert source.is_enabled({}) is False
        assert source.is_enabled({"enabled": False}) is False
        assert source.is_enabled({"enabled": True}) is True

    def test_fetch_timeout_uses_orchestrator_default(self, source):
        assert source.fetch_timeout_seconds({}) == 120.0
        assert source.fetch_timeout_seconds({"timeout_seconds": 60}) == 60.0


class TestTokenRemap:
    def test_custom_token_env_exported_as_official_name(
        self, source, home_path, monkeypatch, tmp_path
    ):
        binary = _fake_binary(tmp_path)
        monkeypatch.setenv("MY_PASS_TOKEN", "pst_custom")
        monkeypatch.delenv(DEFAULT_TOKEN_ENV, raising=False)
        seen_extra: list[dict] = []

        def fake_run(argv, *, allow_env=(), extra_env=None, timeout=30):
            seen_extra.append(dict(extra_env or {}))
            if argv[1:] == ["test"]:
                return _completed(returncode=0)
            if argv[1:3] == ["item", "list"]:
                return _completed(stdout='[{"title": "OPENAI_API_KEY"}]')
            if argv[1:4] == ["item", "view", "--"]:
                return _completed(stdout="ok\n")
            return _completed(returncode=1, stderr="unexpected")

        monkeypatch.setattr("protonpass.run_secret_cli", fake_run)
        result = source.fetch(
            _enabled_cfg(binary, personal_access_token_env="MY_PASS_TOKEN"),
            home_path,
        )
        assert result.ok
        assert result.secrets == {"OPENAI_API_KEY": "ok"}
        assert seen_extra
        assert all(
            extra.get(DEFAULT_TOKEN_ENV) == "pst_custom" for extra in seen_extra
        )
        for extra in seen_extra:
            assert set(extra) <= {DEFAULT_TOKEN_ENV}
