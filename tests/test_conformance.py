"""Hermes SecretSource conformance checks (requires hermes-agent checkout)."""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path

import pytest

_hermes = os.environ.get("HERMES_AGENT_CHECKOUT", "").strip()
if _hermes and (Path(_hermes) / "tests" / "secret_sources" / "conformance.py").is_file():
    sys.path.insert(0, _hermes)

try:
    from tests.secret_sources.conformance import SecretSourceConformance
except ImportError:
    pytest.skip(
        "hermes-agent is not installed; conformance tests require "
        "tests.secret_sources.conformance",
        allow_module_level=True,
    )

_plugin_cli = Path(__file__).resolve().parent.parent / "cli.py"
_spec = importlib.util.spec_from_file_location("proton_pass_plugin_cli", _plugin_cli)
_plugin_cli_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_plugin_cli_mod)
register_proton_pass_cli = _plugin_cli_mod.register_proton_pass_cli

from protonpass import ProtonPassSource


class TestProtonPassConformance(SecretSourceConformance):
    @pytest.fixture
    def source(self):
        return ProtonPassSource()


def test_hermes_attach_keeps_protonpass_setup_and_status():
    from hermes_cli.main import _attach_plugin_cli_command

    root = argparse.ArgumentParser(prog="hermes")
    subparsers = root.add_subparsers(dest="command")
    _attach_plugin_cli_command(
        subparsers,
        {
            "name": "protonpass",
            "help": "Inspect the Proton Pass bulk secret source",
            "setup_fn": register_proton_pass_cli,
        },
    )
    setup = root.parse_args(["protonpass", "setup", "--vault", "Istandil"])
    assert setup.vault == "Istandil"
    assert root.parse_args(["protonpass", "status"]).proton_pass_action == "status"
