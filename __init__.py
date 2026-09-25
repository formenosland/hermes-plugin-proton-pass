from pathlib import Path

if __package__:
    from .cli import SKILL_NAME, register_proton_pass_cli
    from .protonpass import ProtonPassSource
else:
    # pytest imports this file without a parent package (repo-root __init__.py).
    from cli import SKILL_NAME, register_proton_pass_cli
    from protonpass import ProtonPassSource

_SKILL_FILE = Path(__file__).resolve().parent / "skills" / "diagnose" / "SKILL.md"


def register(ctx):
    ctx.register_secret_source(ProtonPassSource())
    ctx.register_cli_command(
        name="protonpass",
        help="Inspect the Proton Pass bulk secret source",
        setup_fn=register_proton_pass_cli,
    )
    if _SKILL_FILE.is_file():
        ctx.register_skill(SKILL_NAME, _SKILL_FILE)
