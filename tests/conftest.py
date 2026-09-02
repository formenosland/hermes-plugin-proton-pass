"""Pytest configuration — make the plugin root importable."""

from __future__ import annotations

import re
import subprocess
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_agent_stubs() -> None:
    """Provide minimal agent.secret_sources.base stubs when hermes is absent."""
    if "agent" in sys.modules:
        return

    class ErrorKind(str, Enum):
        NOT_CONFIGURED = "not_configured"
        BINARY_MISSING = "binary_missing"
        AUTH_FAILED = "auth_failed"
        AUTH_EXPIRED = "auth_expired"
        REF_INVALID = "ref_invalid"
        NETWORK = "network"
        EMPTY_VALUE = "empty_value"
        TIMEOUT = "timeout"
        INTERNAL = "internal"

    DEFAULT_CLI_TIMEOUT_SECONDS = 30.0
    DEFAULT_FETCH_TIMEOUT_SECONDS = 120.0

    @dataclass
    class FetchResult:
        secrets: Dict[str, str] = field(default_factory=dict)
        applied: List[str] = field(default_factory=list)
        skipped: List[str] = field(default_factory=list)
        warnings: List[str] = field(default_factory=list)
        error: Optional[str] = None
        error_kind: Optional[ErrorKind] = None
        binary_path: Optional[Path] = None

        @property
        def ok(self) -> bool:
            return self.error is None

    class SecretSource(ABC):
        api_version: int = 1
        name: str = ""
        label: str = ""
        shape: str = "mapped"
        scheme: Optional[str] = None

        @abstractmethod
        def fetch(self, cfg: dict, home_path: Path) -> FetchResult:
            raise NotImplementedError

        def is_enabled(self, cfg: dict) -> bool:
            return bool(isinstance(cfg, dict) and cfg.get("enabled"))

        def override_existing(self, cfg: dict) -> bool:
            return bool(isinstance(cfg, dict) and cfg.get("override_existing", False))

        def protected_env_vars(self, cfg: dict) -> frozenset[str]:
            return frozenset()

        def fetch_timeout_seconds(self, cfg: dict) -> float:
            try:
                val = float(
                    (cfg or {}).get("timeout_seconds", DEFAULT_FETCH_TIMEOUT_SECONDS)
                )
            except (TypeError, ValueError):
                return DEFAULT_FETCH_TIMEOUT_SECONDS
            return val if val > 0 else DEFAULT_FETCH_TIMEOUT_SECONDS

        def config_schema(self) -> dict:
            return {}

    _ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    _ANSI_RE = re.compile(
        r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?)"
    )

    def is_valid_env_name(name: str) -> bool:
        return bool(name) and bool(_ENV_NAME_RE.match(name))

    def scrub_ansi(text: str) -> str:
        return _ANSI_RE.sub("", text or "")

    def run_secret_cli(
        argv: Sequence[str],
        *,
        allow_env: Sequence[str] = (),
        extra_env: Optional[Dict[str, str]] = None,
        timeout: float = DEFAULT_CLI_TIMEOUT_SECONDS,
    ) -> subprocess.CompletedProcess:
        raise NotImplementedError("run_secret_cli must be mocked in tests")

    import types

    agent = types.ModuleType("agent")
    secret_sources = types.ModuleType("agent.secret_sources")
    base = types.ModuleType("agent.secret_sources.base")

    base.ErrorKind = ErrorKind
    base.FetchResult = FetchResult
    base.SecretSource = SecretSource
    base.DEFAULT_CLI_TIMEOUT_SECONDS = DEFAULT_CLI_TIMEOUT_SECONDS
    base.DEFAULT_FETCH_TIMEOUT_SECONDS = DEFAULT_FETCH_TIMEOUT_SECONDS
    base.is_valid_env_name = is_valid_env_name
    base.scrub_ansi = scrub_ansi
    base.run_secret_cli = run_secret_cli

    secret_sources.base = base
    agent.secret_sources = secret_sources

    sys.modules["agent"] = agent
    sys.modules["agent.secret_sources"] = secret_sources
    sys.modules["agent.secret_sources.base"] = base


try:
    import agent.secret_sources.base  # noqa: F401
except ImportError:
    _install_agent_stubs()
