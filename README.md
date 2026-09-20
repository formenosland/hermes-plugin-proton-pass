# hermes-plugin-proton-pass

Hermes Agent secret-source plugin that bulk-injects provider credentials from a [Proton Pass](https://proton.me/pass) vault via `pass-cli`. Item titles that look like env-var names become process environment keys; the password field is the value. Same shape as Hermes Bitwarden (`shape = "bulk"`), not a mapped `env:` / `pass://` catalog.

## Features

- **Bulk vault dump** — list items in one required vault; inject titles matching `^[A-Z][A-Z0-9_]{0,63}$`
- **Password field only** — Netflix-style titles are skipped; empty passwords are never applied (`EMPTY_VALUE`)
- **Bootstrap PAT** — personal access token from the process environment (systemd `EnvironmentFile`, Hermes `.env`, or the shell). `.env` is optional.
- **Non-interactive `pass-cli`** — session check and PAT login on fetch; no TTY prompts
- **Fail-open** — structured `ErrorKind`s; Hermes startup continues; missing secrets are reported, not fatal
- **Protected bootstrap token** — `protected_env_vars` prevents any secret source from overwriting the PAT
- **Orchestrator-owned application** — plugin returns a mapping; Hermes applies precedence, conflicts, provenance, and timeouts

## Requirements

- [Hermes Agent](https://hermes-agent.nousresearch.com/) with secret-source plugin support, including plugin secret re-pull after discovery ([#64177](https://github.com/NousResearch/hermes-agent/issues/64177))
- [Proton Pass CLI](https://protonpass.github.io/pass-cli/) (`pass-cli`) on `PATH`
- A scoped Proton Pass [personal access token](https://protonpass.github.io/pass-cli/commands/personal-access-token/) with access to the configured vault

## How it works

When `secrets.protonpass` is enabled, this plugin reads the PAT from the configured env var, ensures a `pass-cli` session, lists active items in `secrets.protonpass.vault`, and for each title that is a valid env-var name fetches the password field (`pass-cli item view` is an internal helper, not operator config). Resolved values are returned to the Hermes orchestrator, which applies them according to source precedence and `override_existing`. The plugin never writes `os.environ` itself. Product code maps those env names onto Hermes; this plugin does not ship an `env:` map.

Further reading:

- [Hermes secret-source plugin contract](https://hermes-agent.nousresearch.com/docs/developer-guide/secret-source-plugin)
- [Hermes secrets user guide](https://hermes-agent.nousresearch.com/docs/user-guide/secrets/)
- [Proton Pass CLI item commands](https://protonpass.github.io/pass-cli/commands/item/)
- [Proton Pass CLI personal access tokens](https://protonpass.github.io/pass-cli/commands/personal-access-token/)

## Install

1. **Install `pass-cli`** — see the [official installation guide](https://protonpass.github.io/pass-cli/get-started/installation/):

   ```bash
   curl -fsSL https://proton.me/download/pass-cli/install.sh | bash
   ```

   Or via Homebrew: `brew install protonpass/tap/pass-cli`

2. **Create a scoped PAT** — grant the minimum access needed (prefer `viewer` on the Hermes vault):

   ```bash
   pass-cli pat create --name "hermes-agent" --expiration 3m
   pass-cli pat access grant --pat-name "hermes-agent" --vault-name "Personal" --role viewer
   ```

3. **Install the plugin** into your Hermes plugins directory:

   ```bash
   git clone https://github.com/formenosland/hermes-plugin-proton-pass.git ~/.hermes/plugins/proton-pass
   ```

   Or copy the repository contents into `~/.hermes/plugins/proton-pass/`.

4. **Enable the plugin** in `~/.hermes/config.yaml`:

   ```yaml
   plugins:
     enabled:
       - proton-pass
   ```

   Or run: `hermes plugins enable proton-pass`

5. **Provide the PAT** in the process environment. systemd units can use `EnvironmentFile`; Hermes `~/.hermes/.env` is optional:

   ```bash
   PROTON_PASS_PERSONAL_ACCESS_TOKEN=pst_…::…
   ```

   If you use `~/.hermes/.env`, `chmod 600` it.

6. **Name vault items like env vars** — e.g. `OPENAI_API_KEY`, `GITHUB_TOKEN`. Put the secret in the **password** field. Titles such as `Netflix` are skipped.

7. **Configure `secrets.protonpass`** — see [Configuration](#configuration) below.

## Configuration

Config section: `secrets.protonpass` (source name: `protonpass`).

| Key | Description |
| --- | --- |
| `enabled` | Enable this secret source (`false` by default) |
| `vault` | Vault name or share id (required) |
| `personal_access_token_env` | Env var holding the bootstrap PAT (default: `PROTON_PASS_PERSONAL_ACCESS_TOKEN`) |
| `binary_path` | Absolute path to `pass-cli` (optional; pins the binary and skips `PATH` lookup) |
| `override_existing` | Replace env vars already set before secret sources run (default: `true`) |
| `timeout_seconds` | Wall-clock fetch budget enforced by Hermes (default: `120`) |

Example `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - proton-pass

secrets:
  sources:
    - protonpass
  protonpass:
    enabled: true
    vault: Personal
    personal_access_token_env: PROTON_PASS_PERSONAL_ACCESS_TOKEN
    override_existing: true
    timeout_seconds: 120
```

There is no `env:` map and no operator-maintained `pass://` lines.

## Authentication

One PAT in the process environment unlocks the configured vault. Item titles in that vault become env vars. You do not run `pass-cli login` yourself on the Hermes host.

PAT sessions last about two hours. The plugin re-authenticates when the session is gone.

**Headless hosts** (no OS keyring): set filesystem key storage per [Proton Pass CLI configuration](https://protonpass.github.io/pass-cli/get-started/configuration/):

```bash
export PROTON_PASS_KEY_PROVIDER=fs
```

Optional: `PROTON_PASS_SESSION_DIR`, `PROTON_PASS_ENCRYPTION_KEY`, `PROTON_PASS_LINUX_KEYRING` (passed through to `pass-cli`).

## Security

- **Scope the PAT** to the minimum vault Hermes needs; prefer `viewer`; set expiration; rotate on schedule
- **Protect the PAT** — systemd `EnvironmentFile` or `chmod 600 ~/.hermes/.env`; never commit the token
- **Bootstrap token protection** — the PAT env var is listed in `protected_env_vars`; no secret source can overwrite it
- **Minimal child environment** — `pass-cli` is invoked through Hermes `run_secret_cli` with an allowlisted env only
- **No direct env writes** — the plugin returns resolved values; Hermes owns `os.environ` application, precedence, and provenance labels
- **Treat the PAT as a machine credential** — a leak grants vault access within the token's scope and role

## Failure modes

Hermes fail-opens on secret-source errors: startup continues, but affected credentials are not applied. Check startup logs for the source label **Proton Pass** and the `ErrorKind`.

| Symptom | ErrorKind | Likely cause | Fix |
| --- | --- | --- | --- |
| Source skipped; vault or PAT missing | `NOT_CONFIGURED` | `enabled: true` but blank `vault` or unset PAT | Set `secrets.protonpass.vault`; export `PROTON_PASS_PERSONAL_ACCESS_TOKEN` |
| `pass-cli` not found | `BINARY_MISSING` | CLI not installed or not on `PATH` | Install `pass-cli` or set `binary_path` |
| Login or session check failed | `AUTH_FAILED` | Invalid or revoked PAT; session expired; insufficient vault grants; missing keyring on a headless host | Recreate PAT; grant vault access; set `PROTON_PASS_KEY_PROVIDER=fs` if there is no OS keyring |
| Token expired message in stderr | `AUTH_EXPIRED` | PAT past expiration | Create a new PAT; update the environment |
| Warning per item; item skipped | `REF_INVALID` | Title matched but password field missing | Store the secret in the password field |
| Warning per item; value skipped | `EMPTY_VALUE` | Password field empty | Fill the field; empty values are never applied |
| Fetch aborted; timeout message | `TIMEOUT` | Resolution exceeded `timeout_seconds` | Increase timeout; reduce vault size; check network |
| Transport / connectivity errors | `NETWORK` | Proton API unreachable | Check network; retry |
| Unexpected plugin error | `INTERNAL` | Bug or contract violation | Check logs; file an issue |

Non-matching titles are skipped (summary warning). Empty passwords are skipped. Global failures (`NOT_CONFIGURED`, `BINARY_MISSING`, `AUTH_FAILED`, `TIMEOUT`) skip the entire source for that startup.

## Development

```bash
pip install -e ".[dev]"
pytest
```

Unit tests run hermetically: if Hermes Agent is not installed, a minimal stub of `agent.secret_sources.base` under `tests/stubs/` is used.

Conformance tests import `SecretSourceConformance` from the Hermes agent repository (`tests/secret_sources/conformance.py`). If that module is not importable, conformance tests are skipped. Add a Hermes checkout to `PYTHONPATH` (or install Hermes) to run the full suite.

## License

MIT — see [LICENSE](LICENSE).
