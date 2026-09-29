"""Manifest, status, and skill wiring. No Proton account and no secret values."""

from __future__ import annotations

import argparse
from pathlib import Path

import cli
from cli import read_status_config, register_proton_pass_cli, status_lines

ROOT = Path(__file__).resolve().parent.parent


def test_plugin_yaml_declares_standalone_and_pat():
    text = (ROOT / "plugin.yaml").read_text()
    assert "kind: standalone" in text
    assert "PROTON_PASS_PERSONAL_ACCESS_TOKEN" in text
    assert "requires_env:" in text


def test_status_reports_config_without_token_value(tmp_path: Path):
    token = "pst_super_secret_value"
    binary = tmp_path / "pass-cli"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text(
        "\n".join(
            [
                "plugins:",
                "  enabled:",
                "    - protonpass",
                "secrets:",
                "  protonpass:",
                "    enabled: true",
                "    vault: Hermes",
                f"    binary_path: {binary}",
                "",
            ]
        )
    )
    lines = status_lines(
        home,
        {"PROTON_PASS_PERSONAL_ACCESS_TOKEN": token},
    )
    text = "\n".join(lines)
    assert "plugin_enabled: yes" in text
    assert "source_enabled: yes" in text
    assert "vault: Hermes" in text
    assert "token_present: yes" in text
    assert str(binary) in text
    assert token not in text
    assert "next Hermes start" in text


def test_status_scrubs_token_if_it_appears_in_config(tmp_path: Path):
    token = "pst_super_secret_value"
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text(
        "secrets:\n  protonpass:\n    enabled: true\n"
        f"    vault: {token}\n"
    )
    text = "\n".join(
        status_lines(home, {"PROTON_PASS_PERSONAL_ACCESS_TOKEN": token})
    )
    assert token not in text
    assert "vault: [redacted]" in text


def test_status_missing_token_and_binary(tmp_path: Path):
    home = tmp_path / "hermes"
    home.mkdir()
    missing = tmp_path / "no-such-pass-cli"
    (home / "config.yaml").write_text(
        "secrets:\n  protonpass:\n    enabled: false\n    vault: Work\n"
        f"    binary_path: {missing}\n"
    )
    text = "\n".join(status_lines(home, {}))
    assert "plugin_enabled: no" in text
    assert "source_enabled: no" in text
    assert "vault: Work" in text
    assert "token_present: no" in text
    assert "pass-cli: missing" in text


def test_read_status_config_ignores_other_sections():
    parsed = read_status_config(
        "\n".join(
            [
                "plugins:",
                "  enabled:",
                "    - other",
                "secrets:",
                "  bitwarden:",
                "    enabled: true",
                "    vault: nope",
                "  protonpass:",
                "    enabled: true",
                "    vault: Ops",
                "    personal_access_token_env: MY_PAT",
            ]
        )
    )
    assert parsed["plugin_enabled"] is False
    assert parsed["vault"] == "Ops"
    assert parsed["personal_access_token_env"] == "MY_PAT"


def test_skill_file_uses_our_name():
    text = (ROOT / "skills" / "diagnose" / "SKILL.md").read_text()
    assert "name: diagnose" in text
    assert "protonpass:diagnose" in text
    assert "hermes protonpass setup" in text
    assert "vault-access" not in text
    assert cli.SKILL_NAME == "diagnose"


def test_setup_writes_vault_without_token(tmp_path: Path):
    from cli import setup_config, write_setup

    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text(
        "plugins:\n  enabled:\n    - other\nsecrets:\n  bitwarden:\n    enabled: true\n"
    )
    setup_config(home, "Hermes")
    text = (home / "config.yaml").read_text()
    assert "protonpass" in text
    assert 'vault: "Hermes"' in text
    assert "pst_" not in text
    parsed = read_status_config(text)
    assert parsed["plugin_enabled"] is True
    assert parsed["enabled"] is True
    assert parsed["vault"] == "Hermes"
    again = write_setup(text, "Work")
    assert again.count("protonpass:") == 1
    assert read_status_config(again)["vault"] == "Work"


def test_setup_preserves_comments_sibling_and_spaced_vault():
    from cli import write_setup

    original = "\n".join(
        [
            "model: anthropic",
            "# provider keys live in Proton Pass",
            "secrets:",
            "  bitwarden:",
            "    enabled: true",
            "  protonpass:",
            "    # operator note",
            "    enabled: false",
            '    vault: "Old"',
            "",
        ]
    )
    updated = write_setup(original, "Personal Ops")
    assert "model: anthropic" in updated
    assert "# provider keys live in Proton Pass" in updated
    assert "bitwarden:" in updated
    assert "    # operator note" in updated
    assert 'vault: "Personal Ops"' in updated
    assert "pst_" not in updated
    parsed = read_status_config(updated)
    assert parsed["vault"] == "Personal Ops"
    assert parsed["enabled"] is True


def test_cli_setup_accepts_vault_on_the_parser_hermes_passes():
    """Hermes creates the protonpass parser and passes that parser to setup_fn."""
    root = argparse.ArgumentParser(prog="hermes")
    subparsers = root.add_subparsers(dest="command")
    parser = subparsers.add_parser("protonpass")
    register_proton_pass_cli(parser)

    setup = root.parse_args(["protonpass", "setup", "--vault", "Istandil"])
    assert setup.proton_pass_action == "setup"
    assert setup.vault == "Istandil"
    status = root.parse_args(["protonpass", "status"])
    assert status.proton_pass_action == "status"
