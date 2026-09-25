"""Hermes SecretSource conformance checks (requires hermes-agent checkout)."""

from __future__ import annotations

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

from protonpass import ProtonPassSource


class TestProtonPassConformance(SecretSourceConformance):
    @pytest.fixture
    def source(self):
        return ProtonPassSource()
