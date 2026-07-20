"""Unit tests for the Proton Pass secret source."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent.secret_sources.base import ErrorKind

from protonpass import DEFAULT_TOKEN_ENV, ProtonPassSource


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


class TestMalformedConfig:
    def test_fetch_never_raises_on_degenerate_config(
        self, source: ProtonPassSource, home_path: Path
    ):
        for cfg in (
            {},
            {"enabled": True},
            {"enabled": True, "env": "not-a-dict"},
            {"enabled": True, "timeout_seconds": "bogus"},
            None,
        ):
            result = source.fetch(cfg if isinstance(cfg, dict) else {}, home_path)
            assert result.error_kind is not None or result.ok


class TestNotConfigured:
    def test_missing_token(self, source, home_path, monkeypatch):
        monkeypatch.delenv(DEFAULT_TOKEN_ENV, raising=False)
        cfg = {
            "enabled": True,
            "env": {"API_KEY": "pass://Vault/Item/password"},
        }
        result = source.fetch(cfg, home_path)
        assert result.error_kind == ErrorKind.NOT_CONFIGURED
        assert DEFAULT_TOKEN_ENV in (result.error or "")

    def test_empty_env_map(self, source, home_path, monkeypatch):
        monkeypatch.setenv(DEFAULT_TOKEN_ENV, "pst_test_token")
        result = source.fetch({"enabled": True, "env": {}}, home_path)
        assert result.error_kind == ErrorKind.NOT_CONFIGURED


class TestBinaryResolution:
    def test_missing_binary(self, source, home_path, monkeypatch):
        monkeypatch.setenv(DEFAULT_TOKEN_ENV, "pst_test_token")
        monkeypatch.setattr("protonpass.shutil.which", lambda _name: None)
        cfg = {
            "enabled": True,
            "env": {"API_KEY": "pass://Vault/Item/password"},
        }
        result = source.fetch(cfg, home_path)
        assert result.error_kind == ErrorKind.BINARY_MISSING

    def test_pinned_binary_missing_no_path_fallback(
        self, source, home_path, monkeypatch, tmp_path
    ):
        monkeypatch.setenv(DEFAULT_TOKEN_ENV, "pst_test_token")
        missing = tmp_path / "missing-pass-cli"
        cfg = {
            "enabled": True,
            "binary_path": str(missing),
            "env": {"API_KEY": "pass://Vault/Item/password"},
        }
        result = source.fetch(cfg, home_path)
        assert result.error_kind == ErrorKind.BINARY_MISSING


class TestHappyPath:
    def test_test_ok_and_item_view_returns_values(
        self, source, home_path, monkeypatch, tmp_path
    ):
        binary = tmp_path / "pass-cli"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        monkeypatch.setenv(DEFAULT_TOKEN_ENV, "pst_test_token")

        calls: list[list[str]] = []

        def fake_run(argv, *, allow_env=(), extra_env=None, timeout=30):
            calls.append(list(argv))
            if argv[1:] == ["test"]:
                return _completed(returncode=0)
            if argv[1:4] == ["item", "view", "--"]:
                ref = argv[4]
                if ref == "pass://Vault/Item/password":
                    return _completed(stdout="secret-value\n")
                if ref == "pass://Vault/Item/api_key":
                    return _completed(stdout="api-secret\n")
            return _completed(returncode=1, stderr="unexpected command")

        monkeypatch.setattr("protonpass.run_secret_cli", fake_run)

        cfg = {
            "enabled": True,
            "binary_path": str(binary),
            "env": {
                "API_KEY": "pass://Vault/Item/api_key",
                "DB_PASSWORD": "pass://Vault/Item/password",
            },
        }
        result = source.fetch(cfg, home_path)

        assert result.ok
        assert result.secrets == {
            "API_KEY": "api-secret",
            "DB_PASSWORD": "secret-value",
        }
        assert result.binary_path == binary
        assert calls[0][1:] == ["test"]
        assert any(call[1:4] == ["item", "view", "--"] for call in calls)


class TestLoginPath:
    def test_test_fails_login_ok_then_view(
        self, source, home_path, monkeypatch, tmp_path
    ):
        binary = tmp_path / "pass-cli"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        monkeypatch.setenv(DEFAULT_TOKEN_ENV, "pst_test_token")

        def fake_run(argv, *, allow_env=(), extra_env=None, timeout=30):
            if argv[1:] == ["test"]:
                return _completed(returncode=1, stderr="not logged in")
            if argv[1:] == ["login"]:
                return _completed(returncode=0)
            if argv[1:4] == ["item", "view", "--"]:
                return _completed(stdout="from-login\n")
            return _completed(returncode=1, stderr="unexpected")

        monkeypatch.setattr("protonpass.run_secret_cli", fake_run)

        cfg = {
            "enabled": True,
            "binary_path": str(binary),
            "env": {"TOKEN": "pass://Vault/Item/password"},
        }
        result = source.fetch(cfg, home_path)

        assert result.ok
        assert result.secrets == {"TOKEN": "from-login"}


class TestPerReferenceHandling:
    def test_empty_field_value_skipped(self, source, home_path, monkeypatch, tmp_path):
        binary = tmp_path / "pass-cli"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        monkeypatch.setenv(DEFAULT_TOKEN_ENV, "pst_test_token")

        def fake_run(argv, *, allow_env=(), extra_env=None, timeout=30):
            if argv[1:] == ["test"]:
                return _completed(returncode=0)
            if argv[1:4] == ["item", "view", "--"]:
                return _completed(stdout="   \n")
            return _completed(returncode=1)

        monkeypatch.setattr("protonpass.run_secret_cli", fake_run)

        cfg = {
            "enabled": True,
            "binary_path": str(binary),
            "env": {"EMPTY": "pass://Vault/Item/password"},
        }
        result = source.fetch(cfg, home_path)

        assert result.ok
        assert result.secrets == {}
        assert any("empty value" in warning.lower() for warning in result.warnings)

    def test_invalid_refs_warned_and_skipped(self, source, home_path, monkeypatch):
        monkeypatch.setenv(DEFAULT_TOKEN_ENV, "pst_test_token")
        monkeypatch.setattr(
            "protonpass._find_binary",
            lambda _path: Path("/usr/bin/pass-cli"),
        )
        monkeypatch.setattr(
            "protonpass._ensure_session",
            lambda *_args, **_kwargs: True,
        )

        def fake_run(argv, *, allow_env=(), extra_env=None, timeout=30):
            if argv[1:4] == ["item", "view", "--"]:
                return _completed(stdout="good-value\n")
            return _completed(returncode=1)

        monkeypatch.setattr("protonpass.run_secret_cli", fake_run)

        cfg = {
            "enabled": True,
            "env": {
                "GOOD": "pass://Vault/Item/password",
                "NOT_A_REF": "https://example.com",
                "2INVALID": "pass://Vault/Item/password",
            },
        }
        result = source.fetch(cfg, home_path)

        assert result.secrets == {"GOOD": "good-value"}
        assert any("not a valid env-var name" in w for w in result.warnings)
        assert any("not a valid pass://" in w for w in result.warnings)


class TestHooks:
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
        binary = tmp_path / "pass-cli"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        monkeypatch.setenv("MY_PASS_TOKEN", "pst_custom")
        monkeypatch.delenv(DEFAULT_TOKEN_ENV, raising=False)

        seen_extra: list[dict] = []

        def fake_run(argv, *, allow_env=(), extra_env=None, timeout=30):
            seen_extra.append(dict(extra_env or {}))
            if argv[1:] == ["test"]:
                return _completed(returncode=0)
            if argv[1:4] == ["item", "view", "--"]:
                return _completed(stdout="ok\n")
            return _completed(returncode=1, stderr="unexpected")

        monkeypatch.setattr("protonpass.run_secret_cli", fake_run)

        cfg = {
            "enabled": True,
            "binary_path": str(binary),
            "personal_access_token_env": "MY_PASS_TOKEN",
            "env": {"API_KEY": "pass://Vault/Item/password"},
        }
        result = source.fetch(cfg, home_path)

        assert result.ok
        assert result.secrets == {"API_KEY": "ok"}
        assert seen_extra
        assert all(
            extra.get(DEFAULT_TOKEN_ENV) == "pst_custom" for extra in seen_extra
        )
