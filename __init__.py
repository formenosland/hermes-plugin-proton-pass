if __package__:
    from .protonpass import ProtonPassSource
else:
    # pytest imports this file without a parent package (repo-root __init__.py).
    from protonpass import ProtonPassSource


def register(ctx):
    ctx.register_secret_source(ProtonPassSource())
