"""Hermetic stand-in for ``agent.secret_sources.base`` used by unit tests.

Mirrors the public surface this plugin imports. When a real Hermes Agent
install is available, conftest prefers that instead of this stub.
"""

from __future__ import annotations

import os
import re
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Sequence

SECRET_SOURCE_API_VERSION = 1
DEFAULT_FETCH_TIMEOUT_SECONDS = 120.0
DEFAULT_CLI_TIMEOUT_SECONDS = 30.0

_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ANSI_RE = re.compile(
    r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?)"
)


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
    api_version: int = SECRET_SOURCE_API_VERSION
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

    def protected_env_vars(self, cfg: dict) -> FrozenSet[str]:
        return frozenset()

    def fetch_timeout_seconds(self, cfg: dict) -> float:
        try:
            val = float((cfg or {}).get("timeout_seconds", DEFAULT_FETCH_TIMEOUT_SECONDS))
        except (TypeError, ValueError):
            return DEFAULT_FETCH_TIMEOUT_SECONDS
        return val if val > 0 else DEFAULT_FETCH_TIMEOUT_SECONDS

    def config_schema(self) -> dict:
        return {}


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
    base_keep = (
        "PATH",
        "HOME",
        "USERPROFILE",
        "SYSTEMROOT",
        "TMPDIR",
        "TEMP",
        "LANG",
        "LC_ALL",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
    )
    env: Dict[str, str] = {}
    for key in (*base_keep, *allow_env):
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    if extra_env:
        env.update(extra_env)
    env.setdefault("NO_COLOR", "1")

    try:
        proc = subprocess.run(  # noqa: S603
            list(argv),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"{Path(str(argv[0])).name} timed out after {timeout:.0f}s"
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"failed to invoke {Path(str(argv[0])).name}: {exc}"
        ) from exc

    proc.stdout = proc.stdout or ""
    proc.stderr = scrub_ansi(proc.stderr or "")
    return proc
