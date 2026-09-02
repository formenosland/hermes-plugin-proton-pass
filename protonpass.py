"""Proton Pass (`pass-cli`) bulk secret source for Hermes Agent."""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from agent.secret_sources.base import (
    DEFAULT_CLI_TIMEOUT_SECONDS,
    DEFAULT_FETCH_TIMEOUT_SECONDS,
    ErrorKind,
    FetchResult,
    SecretSource,
    run_secret_cli,
    scrub_ansi,
)

DEFAULT_TOKEN_ENV = "PROTON_PASS_PERSONAL_ACCESS_TOKEN"

# Per-invocation CLI budget. The orchestrator wall-clock budget is separate
# (secrets.protonpass.timeout_seconds → fetch_timeout_seconds, default 120s).
_CLI_RUN_TIMEOUT = DEFAULT_CLI_TIMEOUT_SECONDS

_PASS_SCHEME = "pass://"
_ENV_TITLE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SHARE_ID_RE = re.compile(r"^[A-Za-z0-9]{16,}$")

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


def _vault_name(cfg: dict) -> str:
    return str(cfg.get("vault") or "").strip()


def _allow_env(token_env: str) -> Tuple[str, ...]:
    # Always allowlist the name pass-cli reads, plus the configured name (in
    # case they differ and the operator also exported the official name).
    names = {token_env, DEFAULT_TOKEN_ENV}
    return tuple(sorted(names)) + _STATIC_ALLOW_ENV


def _child_extra_env(token: str) -> Dict[str, str]:
    """Always export the PAT under the name pass-cli expects."""
    return {DEFAULT_TOKEN_ENV: token}


def _is_share_id(vault: str) -> bool:
    return bool(_SHARE_ID_RE.fullmatch(vault))


def _password_ref(vault: str, title: str) -> str:
    return f"{_PASS_SCHEME}{vault}/{title}/password"


def _extract_title(item: object) -> Optional[str]:
    if isinstance(item, str):
        cleaned = item.strip()
        return cleaned or None
    if not isinstance(item, dict):
        return None
    for key in ("title", "name", "itemTitle"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    nested = item.get("item")
    if isinstance(nested, dict):
        return _extract_title(nested)
    return None


def _iter_list_payload(payload: object) -> Iterable[object]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "data", "result"):
            inner = payload.get(key)
            if isinstance(inner, list):
                return inner
        return [payload]
    return ()


def parse_item_titles(raw: str) -> List[str]:
    """Extract unique item titles from `pass-cli item list --output json`."""
    text = (raw or "").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []

    titles: List[str] = []
    seen: set[str] = set()
    for item in _iter_list_payload(payload):
        title = _extract_title(item)
        if not title or title in seen:
            continue
        seen.add(title)
        titles.append(title)
    return titles


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


def _list_argv(binary: Path, vault: str) -> List[str]:
    argv = [
        str(binary),
        "item",
        "list",
        "--output",
        "json",
        "--filter-state",
        "active",
    ]
    if _is_share_id(vault):
        argv.extend(["--share-id", vault])
    else:
        argv.extend(["--vault-name", vault])
    return argv


def _view_password(
    binary: Path,
    vault: str,
    title: str,
    *,
    allow_env: Sequence[str],
    token: str,
):
    ref = _password_ref(vault, title)
    return _run_cli(
        [str(binary), "item", "view", "--", ref],
        allow_env=allow_env,
        token=token,
    ), ref


class ProtonPassSource(SecretSource):
    """Bulk-inject env vars from Proton Pass item titles via pass-cli."""

    name = "protonpass"
    label = "Proton Pass"
    shape = "bulk"
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
            "vault": {
                "description": "Vault name or share id to dump (required)",
                "default": "",
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
        vault = _vault_name(cfg)
        token_env = _token_env_name(cfg)
        token = os.environ.get(token_env, "").strip()

        if not vault or not token:
            missing = []
            if not vault:
                missing.append("secrets.protonpass.vault")
            if not token:
                missing.append(token_env)
            result.error = (
                "secrets.protonpass.enabled is true but "
                + " and ".join(f"{name} is not set" for name in missing)
                + "."
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

        try:
            list_proc = _run_cli(
                _list_argv(binary, vault),
                allow_env=allow_env,
                token=token,
            )
        except RuntimeError as exc:
            result.error = str(exc)
            result.error_kind = _classify_stderr(str(exc))
            return result

        if list_proc.returncode != 0:
            stderr = list_proc.stderr or ""
            result.error = _format_cli_error("item list", list_proc.returncode, stderr)
            result.error_kind = _classify_stderr(stderr)
            return result

        titles = parse_item_titles(list_proc.stdout or "")
        matching = [title for title in titles if _ENV_TITLE_RE.fullmatch(title)]
        skipped = len(titles) - len(matching)
        if skipped:
            result.warnings.append(
                f"Skipped {skipped} Proton Pass item(s) whose titles are not "
                "env-var names (^[A-Z][A-Z0-9_]{0,63}$)."
            )
        if not titles:
            result.warnings.append(
                f"Proton Pass vault {vault!r} listed no items."
            )

        secrets: Dict[str, str] = {}
        for title in matching:
            try:
                proc, ref = _view_password(
                    binary,
                    vault,
                    title,
                    allow_env=allow_env,
                    token=token,
                )
            except RuntimeError as exc:
                message = str(exc)
                result.warnings.append(f"Skipping {title!r}: {message}")
                if _stderr_looks_auth_related(message):
                    result.error = message
                    result.error_kind = _classify_stderr(message)
                    break
                continue

            if proc.returncode != 0:
                stderr = proc.stderr or ""
                message = _format_cli_error("item view", proc.returncode, stderr)
                result.warnings.append(f"Skipping {title!r}: {message}")
                if _stderr_looks_auth_related(stderr):
                    result.error = message
                    result.error_kind = _classify_stderr(stderr)
                    break
                continue

            value = (proc.stdout or "").rstrip("\r\n")
            if not value.strip():
                result.warnings.append(
                    f"Skipping {title!r}: pass-cli returned an empty value "
                    f"for {ref!r} ({ErrorKind.EMPTY_VALUE.value})"
                )
                continue

            secrets[title] = value

        result.secrets = secrets
        return result
