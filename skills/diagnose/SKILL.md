---
name: diagnose
description: Diagnose a Hermes credential that Proton Pass should have injected at startup, and point the operator at protonpass setup.
---

# Proton Pass diagnose

Hermes loads this as `protonpass:diagnose`. Passwords are injected at process start. This skill does not fetch them and does not edit `config.yaml`.

Use it when an operator says a provider key is missing, wrong, or still old after a vault edit.

1. Run `hermes protonpass status`. The command checks the config file and whether the token env var is non-empty. It does not run `pass-cli`.
2. If `plugin_enabled` or `source_enabled` is `no`, or `vault` is `(unset)`, the operator runs `hermes protonpass setup` in their own terminal. That command asks for the vault name and writes `secrets.protonpass`. It does not ask for the token. Plugin install already prompted for `PROTON_PASS_PERSONAL_ACCESS_TOKEN`.
3. `token_present: no` means that env var is empty. `pass-cli: missing` means the CLI is not on `PATH` or `binary_path` does not point at an executable.
4. If status is healthy and one env var is still missing, the vault item title must be that name, such as `OPENAI_API_KEY`, and the secret must be in the password field. Other titles are skipped. An empty password is not applied.
5. A password changed in Proton Pass is loaded on the next Hermes start. The current process keeps the value from the start that loaded it.
