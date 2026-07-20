# hermes-plugin-proton-pass

Hermes Agent secret-source plugin that resolves provider credentials from [Proton Pass](https://proton.me/pass) via `pass-cli` and `pass://` references.

## Features

- **Mapped `pass://` references** — bind Hermes env vars to `pass://vault/item/field` (names or IDs; optional `?totp=code|uri`)
- **Bootstrap PAT model** — one scoped personal access token in `~/.hermes/.env`, same pattern as Bitwarden and 1Password
- **`pass-cli` subprocess via `run_secret_cli`** — minimal allowlisted child env; argv-only; stdin closed; no shell
- **Non-interactive session ensure** — `pass-cli test` then `pass-cli login` when needed; never prompts on the startup path
- **Fail-open `ErrorKind`s** — structured errors (`NOT_CONFIGURED`, `AUTH_FAILED`, etc.); Hermes startup continues; missing secrets are reported, not fatal
- **Protected bootstrap token** — `protected_env_vars` prevents any secret source from overwriting the PAT
- **Orchestrator-owned application** — plugin returns a mapping; Hermes applies precedence, conflicts, provenance, and timeouts

## Requirements

- [Hermes Agent](https://hermes-agent.nousresearch.com/) with secret-source plugin support
- [Proton Pass CLI](https://protonpass.github.io/pass-cli/) (`pass-cli`) on `PATH`
- A scoped Proton Pass [personal access token](https://protonpass.github.io/pass-cli/commands/personal-access-token/) with access to the vaults or items you reference

## How it works

Hermes loads `~/.hermes/.env` first (bootstrap credentials). When `secrets.protonpass` is enabled, this plugin reads your PAT from the configured env var, ensures a `pass-cli` session, and resolves each entry in `secrets.protonpass.env` by calling `pass-cli item view` with the `pass://` reference. Resolved values are returned to the Hermes orchestrator, which applies them to the process environment according to source precedence and `override_existing`. The plugin never writes `os.environ` itself.

Further reading:

- [Hermes secret-source plugin contract](https://hermes-agent.nousresearch.com/docs/developer-guide/secret-source-plugin)
- [Hermes secrets user guide](https://hermes-agent.nousresearch.com/docs/user-guide/secrets/)
- [Proton Pass CLI secret references](https://protonpass.github.io/pass-cli/commands/contents/secret-references/)
- [Proton Pass CLI personal access tokens](https://protonpass.github.io/pass-cli/commands/personal-access-token/)

## Install

1. **Install `pass-cli`** — see the [official installation guide](https://protonpass.github.io/pass-cli/get-started/installation/):

   ```bash
   curl -fsSL https://proton.me/download/pass-cli/install.sh | bash
   ```

   Or via Homebrew: `brew install protonpass/tap/pass-cli`

2. **Create a scoped PAT** — grant the minimum access needed (prefer `viewer` on specific vaults or items):

   ```bash
   pass-cli pat create --name "hermes-agent" --expiration 3m
   pass-cli pat access grant --pat-name "hermes-agent" --vault-name "My Vault" --role viewer
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

5. **Store the PAT** in `~/.hermes/.env`:

   ```bash
   PROTON_PASS_PERSONAL_ACCESS_TOKEN=pst_…::…
   chmod 600 ~/.hermes/.env
   ```

6. **Configure `secrets.protonpass`** — see [Configuration](#configuration) below.

## Configuration

Config section: `secrets.protonpass` (source name: `protonpass`).

| Key | Description |
| --- | --- |
| `enabled` | Enable this secret source (`false` by default) |
| `env` | Map of env var name → `pass://` reference |
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
    personal_access_token_env: PROTON_PASS_PERSONAL_ACCESS_TOKEN
    override_existing: true
    timeout_seconds: 120
    env:
      OPENAI_API_KEY: pass://API Keys/OpenAI/api_key
      ANTHROPIC_API_KEY: pass://API Keys/Anthropic/secret_key
      GITHUB_TOKEN: pass://Work/GitHub/password
```

### `pass://` reference format

```
pass://<vault>/<item>/<field>[?totp=code|uri]
```

- **vault** — Share ID or vault name (e.g. `Work`, `AbCdEf123456`)
- **item** — Item ID or title (e.g. `GitHub`, `XyZ789`)
- **field** — Field name (e.g. `password`, `username`, `api_key`, `totp`)
- **totp** (optional) — `?totp=code` (default) returns the current TOTP code; `?totp=uri` returns the raw `otpauth://` URI

Names with spaces are supported: `pass://My Vault/My Item/password`. For unambiguous targeting, use Share ID and Item ID. See the [secret references documentation](https://protonpass.github.io/pass-cli/commands/contents/secret-references/) for section-qualified fields and TOTP behavior.

## Authentication

This plugin follows the same bootstrap-token model as Hermes's bundled Bitwarden and 1Password sources: one machine-local PAT in `~/.hermes/.env` unlocks the vault; everything else is resolved from `pass://` maps in config.

On fetch, the plugin checks the session with `pass-cli test`. If the session is missing or invalid, it runs `pass-cli login` non-interactively using the PAT from the allowlisted child environment. It never prompts — startup runs in non-TTY contexts (gateway, cron, Docker).

**Headless environments:** when the OS keyring is unavailable (containers, headless servers), set filesystem key storage per [Proton Pass CLI configuration](https://protonpass.github.io/pass-cli/get-started/configuration/):

```bash
export PROTON_PASS_KEY_PROVIDER=fs
```

Optional overrides: `PROTON_PASS_SESSION_DIR`, `PROTON_PASS_ENCRYPTION_KEY`, `PROTON_PASS_LINUX_KEYRING`.

## Security

- **Scope the PAT** to the minimum vaults or items Hermes needs; prefer `viewer`; set expiration; rotate on schedule
- **Protect `~/.hermes/.env`** — `chmod 600`; never commit the PAT to version control
- **Bootstrap token protection** — the PAT env var is listed in `protected_env_vars`; no secret source (including this one) can overwrite it via a mapped ref
- **Minimal child environment** — `pass-cli` is invoked through Hermes `run_secret_cli` with an allowlisted env only; the plugin never passes the full process environment
- **No direct env writes** — the plugin returns resolved values; Hermes owns `os.environ` application, precedence, and provenance labels
- **Treat the PAT as a machine credential** — a leak grants vault access within the token's scope and role; protect it like any other long-lived automation secret

## Failure modes

Hermes fail-opens on secret-source errors: startup continues, but affected credentials are not applied. Check startup logs for the source label **Proton Pass** and the `ErrorKind`.

| Symptom | ErrorKind | Likely cause | Fix |
| --- | --- | --- | --- |
| Source skipped; "not set" / no `env` map | `NOT_CONFIGURED` | `enabled: true` but missing PAT, empty `env` map, or invalid config | Set `PROTON_PASS_PERSONAL_ACCESS_TOKEN` in `~/.hermes/.env`; add `env` entries |
| `pass-cli` not found | `BINARY_MISSING` | CLI not installed or not on `PATH` | Install `pass-cli` or set `binary_path` |
| Login or session check failed | `AUTH_FAILED` | Invalid or revoked PAT; insufficient vault grants | Recreate PAT; `pass-cli pat access grant` for referenced vaults/items |
| Token expired message in stderr | `AUTH_EXPIRED` | PAT past expiration | Create a new PAT; update `~/.hermes/.env` |
| Warning per ref; ref skipped | `REF_INVALID` | Malformed `pass://` or unknown vault/item/field | Fix reference format; verify with `pass-cli item view` |
| Warning per ref; value skipped | `EMPTY_VALUE` | Field exists but returned empty | Check field in Proton Pass; do not map empty secrets |
| Fetch aborted; timeout message | `TIMEOUT` | Resolution exceeded `timeout_seconds` | Increase timeout; reduce map size; check network |
| Transport / connectivity errors | `NETWORK` | Proton API unreachable | Check network; retry |
| Unexpected plugin error | `INTERNAL` | Bug or contract violation | Check logs; file an issue |

Per-reference failures are recorded as warnings; other mapped secrets may still resolve. Global failures (`NOT_CONFIGURED`, `BINARY_MISSING`, `AUTH_FAILED`, `TIMEOUT`) skip the entire source for that startup.

## Plugin timing note

Per the [Hermes secret-source plugin docs](https://hermes-agent.nousresearch.com/docs/developer-guide/secret-source-plugin), plugin discovery runs after the first `load_hermes_dotenv()` in a process. The discovering process may not apply plugin secret sources on that first load. Every subsequently spawned Hermes process (gateway children, cron sessions, subagents) does consult enabled plugin sources. Bundled sources (Bitwarden, 1Password) cover first-process bootstrap; plan plugin-only setups accordingly.

## Development

```bash
pip install -e ".[dev]"
pytest
```

Unit tests run hermetically: if Hermes Agent is not installed, a minimal stub of `agent.secret_sources.base` under `tests/stubs/` is used.

Conformance tests import `SecretSourceConformance` from the Hermes agent repository (`tests/secret_sources/conformance.py`). If that module is not importable, conformance tests are skipped. Add a Hermes checkout to `PYTHONPATH` (or install Hermes) to run the full suite.

## License

MIT — see [LICENSE](LICENSE).
