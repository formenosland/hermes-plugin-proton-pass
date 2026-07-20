"""Hermes SecretSource conformance checks (requires hermes-agent checkout)."""

from __future__ import annotations

import pytest

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
