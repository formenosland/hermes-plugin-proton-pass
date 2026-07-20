"""Proton Pass (`pass-cli`) secret source for Hermes Agent."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from agent.secret_sources.base import (
    DEFAULT_CLI_TIMEOUT_SECONDS,
    DEFAULT_FETCH_TIMEOUT_SECONDS,
    ErrorKind,
    FetchResult,
    SecretSource,
    is_valid_env_name,
    run_secret_cli,
    scrub_ansi,
)

DEFAULT_TOKEN_ENV = "PROTON_PASS_PERSONAL_ACCESS_TOKEN"

# Per-invocation CLI budget. The orchestrator wall-clock budget is separate
# (secrets.protonpass.timeout_seconds → fetch_timeout_seconds, default 120s).
_CLI_RUN_TIMEOUT = DEFAULT_CLI_TIMEOUT_SECONDS

_PASS_SCHEME = "pass://"
_TOTP_QUERY_RE = re.compile(r"^\?totp=(?:code|uri)$")

_STATIC_ALLOW_ENV = (
    "PROTON_PASS_SESSION_DIR",
    "PROTON_PASS_KEY_PROVIDER",
    "PROTON_PASS_ENCRYPTION_KEY",
    "PROTON_PASS_LINUX_KEYRING",
    "PASS_LOG_LEVEL",
)


def _coerce_cfg(cfg: object) -> dict:
    return cfg if isinstance(cfg, dict) else {}


def _token_env_name(cfg: dict) -> str:
    return str(cfg.get("personal_access_token_env") or DEFAULT_TOKEN_ENV)


def _allow_env(token_env: str) -> Tuple[str, ...]:
    # Always allowlist the name pass-cli reads, plus the configured name (in
    # case they differ and the operator also exported the official name).
    names = {token_env, DEFAULT_TOKEN_ENV}
    return tuple(sorted(names)) + _STATIC_ALLOW_ENV


def _child_extra_env(token: str) -> Dict[str, str]:
    """Always export the PAT under the name pass-cli expects."""
    return {DEFAULT_TOKEN_ENV: token}


def _is_valid_pass_ref(ref: str) -> bool:
    if not ref.startswith(_PASS_SCHEME):
        return False

    rest = ref[len(_PASS_SCHEME) :]
    path_part, sep, query = rest.partition("?")
    if sep and not _TOTP_QUERY_RE.match(f"?{query}"):
        return False

    parts = [segment for segment in path_part.split("/") if segment]
    return len(parts) >= 3


def _validate_references(
    references: Optional[Dict[str, object]],
) -> Tuple[Dict[str, str], List[str]]:
    valid: Dict[str, str] = {}
    warnings: List[str] = []

    if not isinstance(references, dict):
        return valid, warnings

    for name, ref in references.items():
        if not is_valid_env_name(name):
            warnings.append(f"Skipping {name!r}: not a valid env-var name")
            continue
        if not isinstance(ref, str):
            warnings.append(f"Skipping {name!r}: reference is not a string")
            continue

        cleaned = ref.strip()
        if not _is_valid_pass_ref(cleaned):
            warnings.append(
                f"Skipping {name!r}: {ref!r} is not a valid "
                "pass://vault/item/field reference"
            )
            continue
        valid[name] = cleaned

    return valid, warnings


def _find_binary(binary_path: str) -> Optional[Path]:
    if binary_path:
        pinned = Path(binary_path)
        if pinned.exists() and os.access(pinned, os.X_OK):
            return pinned
        return None

    found = shutil.which("pass-cli")
    return Path(found) if found else None


def _format_cli_error(command: str, returncode: int, stderr: str) -> str:
    detail = scrub_ansi(stderr).strip()[:200]
    if detail:
        return f"pass-cli {command} exited {returncode}: {detail}"
    return f"pass-cli {command} exited {returncode}"


def _classify_stderr(stderr: str) -> ErrorKind:
    lowered = scrub_ansi(stderr).lower()
    if "expired" in lowered or "expir" in lowered:
        return ErrorKind.AUTH_EXPIRED
    if any(
        tok in lowered
        for tok in (
            "unauthorized",
            "not signed in",
            "authentication",
            "auth failed",
            "invalid token",
            "login required",
            "requires an authenticated",
            "401",
            "403",
        )
    ):
        return ErrorKind.AUTH_FAILED
    if any(
        tok in lowered
        for tok in (
            "invalid reference",
            "field not found",
            "field does not exist",
            "secret reference",
            "not found",
        )
    ):
        return ErrorKind.REF_INVALID
    if "timed out" in lowered or "timeout" in lowered:
        return ErrorKind.TIMEOUT
    if any(tok in lowered for tok in ("network", "connection", "resolve host", "dns")):
        return ErrorKind.NETWORK
    if "failed to invoke" in lowered:
        return ErrorKind.BINARY_MISSING
    return ErrorKind.INTERNAL


def _stderr_looks_auth_related(stderr: str) -> bool:
    kind = _classify_stderr(stderr)
    return kind in (ErrorKind.AUTH_FAILED, ErrorKind.AUTH_EXPIRED)


def _run_cli(
    argv: Sequence[str],
    *,
    allow_env: Sequence[str],
    token: str,
    timeout: float = _CLI_RUN_TIMEOUT,
):
    return run_secret_cli(
        list(argv),
        allow_env=allow_env,
        extra_env=_child_extra_env(token),
        timeout=timeout,
    )


def _ensure_session(
    binary: Path,
    allow_env: Sequence[str],
    token: str,
    result: FetchResult,
) -> bool:
    try:
        test_proc = _run_cli(
            [str(binary), "test"],
            allow_env=allow_env,
            token=token,
        )
    except RuntimeError as exc:
        result.error = str(exc)
        result.error_kind = _classify_stderr(str(exc))
        return False

    if test_proc.returncode == 0:
        return True

    try:
        login_proc = _run_cli(
            [str(binary), "login"],
            allow_env=allow_env,
            token=token,
        )
    except RuntimeError as exc:
        result.error = str(exc)
        result.error_kind = _classify_stderr(str(exc))
        return False

    if login_proc.returncode != 0:
        stderr = login_proc.stderr or ""
        result.error = _format_cli_error("login", login_proc.returncode, stderr)
        result.error_kind = _classify_stderr(stderr)
        if result.error_kind not in (
            ErrorKind.AUTH_FAILED,
            ErrorKind.AUTH_EXPIRED,
            ErrorKind.NETWORK,
            ErrorKind.TIMEOUT,
            ErrorKind.BINARY_MISSING,
        ):
            result.error_kind = ErrorKind.AUTH_FAILED
        return False

    return True


class ProtonPassSource(SecretSource):
    """Resolve mapped env vars from Proton Pass pass:// references via pass-cli."""

    name = "protonpass"
    label = "Proton Pass"
    shape = "mapped"
    scheme = "pass"

    def override_existing(self, cfg: dict) -> bool:
        cfg = _coerce_cfg(cfg)
        if "override_existing" not in cfg:
            return True
        return bool(cfg.get("override_existing"))

    def protected_env_vars(self, cfg: dict) -> frozenset[str]:
        return frozenset({_token_env_name(_coerce_cfg(cfg))})

    def config_schema(self) -> dict:
        return {
            "enabled": {"description": "Master switch", "default": False},
            "env": {
                "description": "Map of ENV_VAR -> pass://vault/item/field reference",
                "default": {},
            },
            "personal_access_token_env": {
                "description": "Env var holding the Proton Pass personal access token",
                "default": DEFAULT_TOKEN_ENV,
            },
            "binary_path": {
                "description": "Pin the pass-cli binary (empty = resolve via PATH)",
                "default": "",
            },
            "override_existing": {
                "description": "Resolved values overwrite .env/shell values",
                "default": True,
            },
            "timeout_seconds": {
                "description": "Wall-clock fetch budget enforced by Hermes",
                "default": DEFAULT_FETCH_TIMEOUT_SECONDS,
            },
        }

    def fetch(self, cfg: dict, home_path: Path) -> FetchResult:  # noqa: ARG002
        result = FetchResult()
        try:
            return self._fetch_impl(_coerce_cfg(cfg), result)
        except Exception as exc:  # noqa: BLE001 — contract: never raise
            result.error = str(exc)
            result.error_kind = ErrorKind.INTERNAL
            return result

    def _fetch_impl(self, cfg: dict, result: FetchResult) -> FetchResult:
        env_map = cfg.get("env")
        valid, warnings = _validate_references(
            env_map if isinstance(env_map, dict) else None
        )
        result.warnings.extend(warnings)

        if not valid:
            if not warnings:
                result.error = (
                    "secrets.protonpass.enabled is true but the env: map is empty. "
                    "Add ENV_VAR: pass://vault/item/field entries."
                )
            else:
                result.error = (
                    "secrets.protonpass.enabled is true but no valid pass:// "
                    "references were found in the env: map."
                )
            result.error_kind = ErrorKind.NOT_CONFIGURED
            return result

        token_env = _token_env_name(cfg)
        token = os.environ.get(token_env, "").strip()
        if not token:
            result.error = (
                f"secrets.protonpass.enabled is true but {token_env} is not set."
            )
            result.error_kind = ErrorKind.NOT_CONFIGURED
            return result

        binary_path = str(cfg.get("binary_path") or "")
        binary = _find_binary(binary_path)
        result.binary_path = binary
        if binary is None:
            if binary_path:
                result.error = (
                    f"secrets.protonpass.binary_path ({binary_path!r}) is not an "
                    "executable pass-cli binary."
                )
            else:
                result.error = (
                    "secrets.protonpass.enabled is true but pass-cli was not found "
                    "on PATH. Install it or set secrets.protonpass.binary_path."
                )
            result.error_kind = ErrorKind.BINARY_MISSING
            return result

        allow_env = _allow_env(token_env)

        if not _ensure_session(binary, allow_env, token, result):
            return result

        secrets: Dict[str, str] = {}
        for name in sorted(valid):
            ref = valid[name]
            try:
                proc = _run_cli(
                    [str(binary), "item", "view", "--", ref],
                    allow_env=allow_env,
                    token=token,
                )
            except RuntimeError as exc:
                message = str(exc)
                result.warnings.append(f"Skipping {name!r}: {message}")
                if _stderr_looks_auth_related(message):
                    result.error = message
                    result.error_kind = _classify_stderr(message)
                    break
                continue

            if proc.returncode != 0:
                stderr = proc.stderr or ""
                message = _format_cli_error("item view", proc.returncode, stderr)
                result.warnings.append(f"Skipping {name!r}: {message}")
                if _stderr_looks_auth_related(stderr):
                    result.error = message
                    result.error_kind = _classify_stderr(stderr)
                    break
                continue

            value = (proc.stdout or "").rstrip("\r\n")
            if not value.strip():
                result.warnings.append(
                    f"Skipping {name!r}: pass-cli returned an empty value "
                    f"for {ref!r} ({ErrorKind.EMPTY_VALUE.value})"
                )
                continue

            secrets[name] = value

        result.secrets = secrets
        return result
