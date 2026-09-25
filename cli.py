"""Operator status for the Proton Pass bulk source. Does not read item values."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Mapping, Optional

if __package__:
    from .protonpass import DEFAULT_TOKEN_ENV
else:
    from protonpass import DEFAULT_TOKEN_ENV

SKILL_NAME = "diagnose"


def hermes_home(env: Optional[Mapping[str, str]] = None) -> Path:
    values = env if env is not None else os.environ
    raw = str(values.get("HERMES_HOME") or "").strip()
    if raw:
        return Path(raw)
    return Path.home() / ".hermes"


def _unquote(raw: str) -> str:
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _as_bool(raw: str) -> bool:
    return _unquote(raw).lower() in {"1", "true", "yes", "on"}


def read_status_config(text: str) -> dict:
    """Read the protonpass block and whether plugins.enabled lists this plugin."""
    plugin_on = False
    in_plugins = False
    in_enabled = False
    plugins_indent = -1
    enabled_indent = -1

    in_secrets = False
    in_protonpass = False
    secrets_indent = -1
    proton_indent = -1
    section: dict[str, str] = {}

    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        key, _, rest = stripped.partition(":")
        key = key.strip()
        rest = rest.strip()

        if indent == 0 and key == "plugins" and rest == "":
            in_plugins = True
            in_secrets = False
            in_protonpass = False
            plugins_indent = indent
            continue
        if indent == 0 and key == "secrets" and rest == "":
            in_secrets = True
            in_plugins = False
            in_enabled = False
            in_protonpass = False
            secrets_indent = indent
            continue
        if indent == 0:
            in_plugins = False
            in_secrets = False
            in_enabled = False
            in_protonpass = False
            continue

        if in_plugins and indent <= plugins_indent:
            in_plugins = False
            in_enabled = False
        if in_plugins and key == "enabled" and rest == "":
            in_enabled = True
            enabled_indent = indent
            continue
        if in_enabled and indent <= enabled_indent:
            in_enabled = False
        if in_enabled and stripped.startswith("- "):
            if _unquote(stripped[2:]) == "protonpass":
                plugin_on = True
            continue

        if in_secrets and indent <= secrets_indent:
            in_secrets = False
            in_protonpass = False
        if in_secrets and key == "protonpass" and rest == "":
            in_protonpass = True
            proton_indent = indent
            continue
        if in_protonpass and indent <= proton_indent:
            in_protonpass = False
        if in_protonpass and rest and key in {
            "enabled",
            "vault",
            "binary_path",
            "personal_access_token_env",
        }:
            section[key] = _unquote(rest)

    token_env = section.get("personal_access_token_env") or DEFAULT_TOKEN_ENV
    return {
        "plugin_enabled": plugin_on,
        "enabled": _as_bool(section.get("enabled", "")),
        "vault": section.get("vault", ""),
        "binary_path": section.get("binary_path", ""),
        "personal_access_token_env": token_env,
    }


def _binary_state(binary_path: str) -> str:
    if binary_path:
        path = Path(binary_path)
        if path.exists() and os.access(path, os.X_OK):
            return str(path)
        return "missing"
    found = shutil.which("pass-cli")
    return found or "missing"


def _scrub(lines: list[str], secret: str) -> list[str]:
    if not secret:
        return lines
    return [line.replace(secret, "[redacted]") for line in lines]


def status_lines(
    home: Path,
    env: Optional[Mapping[str, str]] = None,
) -> list[str]:
    """Presence flags only. Does not run pass-cli and does not read item values."""
    values = env if env is not None else os.environ
    config_path = home / "config.yaml"
    parsed = (
        read_status_config(config_path.read_text())
        if config_path.is_file()
        else {
            "plugin_enabled": False,
            "enabled": False,
            "vault": "",
            "binary_path": "",
            "personal_access_token_env": DEFAULT_TOKEN_ENV,
        }
    )
    token_env = parsed["personal_access_token_env"]
    token = str(values.get(token_env) or "").strip()
    vault = parsed["vault"] or "(unset)"
    lines = [
        "Proton Pass bulk source",
        f"plugin_enabled: {'yes' if parsed['plugin_enabled'] else 'no'}",
        f"source_enabled: {'yes' if parsed['enabled'] else 'no'}",
        f"vault: {vault}",
        f"personal_access_token_env: {token_env}",
        f"token_present: {'yes' if token else 'no'}",
        f"pass-cli: {_binary_state(parsed['binary_path'])}",
        "item values are read on the next Hermes start, not by status",
    ]
    return _scrub(lines, token)


def _yaml_scalar(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _validate_vault(vault: str) -> str:
    name = vault.strip()
    if not name or "\n" in name or "\r" in name:
        raise ValueError("vault name is empty")
    return name


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _ensure_plugin_enabled(lines: list[str]) -> list[str]:
    plugins_at = None
    for index, line in enumerate(lines):
        if line.startswith("plugins:") and line.strip() == "plugins:":
            plugins_at = index
            break
    if plugins_at is None:
        return ["plugins:", "  enabled:", "    - protonpass", ""] + lines

    enabled_at = None
    for index in range(plugins_at + 1, len(lines)):
        if lines[index].strip() and _indent_of(lines[index]) == 0:
            break
        if lines[index].strip() == "enabled:":
            enabled_at = index
            break
    if enabled_at is None:
        lines.insert(plugins_at + 1, "  enabled:")
        lines.insert(plugins_at + 2, "    - protonpass")
        return lines

    child_indent = _indent_of(lines[enabled_at]) + 2
    for index in range(enabled_at + 1, len(lines)):
        stripped = lines[index].strip()
        if not stripped:
            continue
        if _indent_of(lines[index]) <= _indent_of(lines[enabled_at]):
            break
        if stripped.startswith("- ") and stripped[2:].strip("\"'") == "protonpass":
            return lines
    lines.insert(enabled_at + 1, " " * child_indent + "- protonpass")
    return lines


def _upsert_protonpass(lines: list[str], vault: str) -> list[str]:
    scalar = _yaml_scalar(vault)
    secrets_at = None
    for index, line in enumerate(lines):
        if line.startswith("secrets:") and line.strip() == "secrets:":
            secrets_at = index
            break
    block = [
        "  protonpass:",
        "    enabled: true",
        f"    vault: {scalar}",
    ]
    if secrets_at is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(["secrets:", *block])
        return lines

    proton_at = None
    for index in range(secrets_at + 1, len(lines)):
        if lines[index].strip() and _indent_of(lines[index]) == 0:
            break
        if lines[index].strip() == "protonpass:":
            proton_at = index
            break
    if proton_at is None:
        lines[secrets_at + 1 : secrets_at + 1] = block
        return lines

    proton_indent = _indent_of(lines[proton_at])
    end = proton_at + 1
    while end < len(lines):
        stripped = lines[end].strip()
        if stripped and _indent_of(lines[end]) <= proton_indent:
            break
        end += 1
    body = lines[proton_at + 1 : end]
    child = " " * (proton_indent + 2)
    enabled_done = False
    vault_done = False
    rewritten: list[str] = []
    for line in body:
        key = line.strip().split(":", 1)[0]
        if key == "enabled":
            rewritten.append(f"{child}enabled: true")
            enabled_done = True
        elif key == "vault":
            rewritten.append(f"{child}vault: {scalar}")
            vault_done = True
        else:
            rewritten.append(line)
    if not enabled_done:
        rewritten.insert(0, f"{child}enabled: true")
    if not vault_done:
        rewritten.append(f"{child}vault: {scalar}")
    return lines[: proton_at + 1] + rewritten + lines[end:]


def write_setup(text: str, vault: str) -> str:
    """Enable the plugin and set secrets.protonpass.vault. Does not write the token."""
    name = _validate_vault(vault)
    lines = _ensure_plugin_enabled(text.splitlines())
    lines = _upsert_protonpass(lines, name)
    rendered = "\n".join(lines)
    if text.endswith("\n") or not text:
        rendered += "\n"
    return rendered


def setup_config(home: Path, vault: str) -> None:
    path = home / "config.yaml"
    current = path.read_text() if path.is_file() else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(write_setup(current, vault))


def setup_command(args=None, **kwargs) -> None:  # noqa: ARG001
    vault = ""
    if args is not None:
        vault = str(getattr(args, "vault", "") or "")
    if not vault.strip():
        vault = input("Proton Pass vault name: ")
    setup_config(hermes_home(), vault)
    print("Saved secrets.protonpass. Restart Hermes to load passwords.")


def status_command(args=None, **kwargs) -> None:  # noqa: ARG001
    home = hermes_home()
    print("\n".join(status_lines(home)))


def register_proton_pass_cli(subparsers) -> None:
    parser = subparsers.add_parser(
        "protonpass",
        help="Inspect the Proton Pass bulk secret source",
    )
    commands = parser.add_subparsers(dest="proton_pass_action", required=True)
    status = commands.add_parser(
        "status",
        help="Show whether the plugin, vault, token, and pass-cli are set",
    )
    status.set_defaults(func=status_command)
    setup = commands.add_parser(
        "setup",
        help="Write the vault name into secrets.protonpass and enable the plugin",
    )
    setup.add_argument("--vault", default="", help="Vault name or share id")
    setup.set_defaults(func=setup_command)
