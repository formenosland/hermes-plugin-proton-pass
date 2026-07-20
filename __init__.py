from protonpass import ProtonPassSource


def register(ctx):
    ctx.register_secret_source(ProtonPassSource())
