# hermes-plugin-proton-pass

A Hermes secret source for [Proton Pass](https://proton.me/pass). Hermes already loads credentials from several places before the agent starts. This plugin is one of those places: a single Proton Pass vault, read through the official `pass-cli`.

Hermes decides the order when more than one source can supply the same variable, and Hermes is what writes the environment. This plugin only resolves the values for the vault you point it at. If Proton Pass cannot be reached, Hermes still starts; those keys are simply left unset.

In that vault, name an item after the variable you want (`XAI_API_KEY`, `GITHUB_TOKEN`) and store the secret in the password field. A title such as `Netflix` is left alone. A password you edit in Proton Pass is what the next Hermes start receives.

## Install

Install [`pass-cli`](https://protonpass.github.io/pass-cli/get-started/installation/) and create a token that can only read the vault you intend to use:

```bash
brew install protonpass/tap/pass-cli
pass-cli pat create --name "hermes-agent" --expiration 3m
pass-cli pat access grant --pat-name "hermes-agent" --vault-name "Hermes" --role viewer
```

Install the plugin. Hermes enables it and asks for `PROTON_PASS_PERSONAL_ACCESS_TOKEN`:

```bash
hermes plugins install formenosland/hermes-plugin-proton-pass --enable
```

[Install in Hermes Desktop](hermes://plugin/install?repo=formenosland/hermes-plugin-proton-pass&enable=1)

`--ref` accepts a full 40-character commit when you want a fixed revision. Tags and branch names are not accepted.

You do not run `pass-cli login` on the Hermes machine. The token from install is the session. When it expires, create another and grant the same vault.

## Configure

Create the items in Proton Pass first, then name the vault:

```bash
hermes protonpass setup
```

`setup` asks for the vault name (`--vault Hermes` skips the prompt) and records it under `secrets.protonpass`. It does not ask for the token again. Start Hermes after it finishes.

On a host with no keychain, tell Proton to keep its keys on disk. See [Proton Pass CLI configuration](https://protonpass.github.io/pass-cli/get-started/configuration/):

```bash
export PROTON_PASS_KEY_PROVIDER=fs
```

## Commands

```bash
hermes protonpass setup
hermes protonpass status
```

`status` reports whether this source is enabled, whether the vault and token are set, and whether `pass-cli` is installed. It does not print the token or any password.

## When a credential is missing

| What you see | What to do |
| --- | --- |
| This source or its vault is off | `hermes protonpass setup`, then start Hermes again |
| The token is unset | Install the plugin again, or set `PROTON_PASS_PERSONAL_ACCESS_TOKEN` where Hermes starts |
| `pass-cli` is missing | Install it, or set `secrets.protonpass.binary_path` to the executable |
| Login failed, or the token expired | Issue a new viewer token for the same vault. On a headless host, set `PROTON_PASS_KEY_PROVIDER=fs` |
| One variable is still empty | The item title must be that name, and the password field must be filled |
| Hermes still has the previous password | Start Hermes again |

## Security

Prefer `viewer` on the one vault this source should see, with an expiration. Keep the token out of git. Anyone who has it can read that vault until you revoke it.

## License

MIT — see [LICENSE](LICENSE).
