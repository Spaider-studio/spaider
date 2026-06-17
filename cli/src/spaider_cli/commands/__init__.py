"""SpAIder CLI subcommand modules.

Each module here exposes either a ``run`` function (for top-level commands
like ``spaider init``) or a ``typer.Typer()`` named ``app`` (for nested
command groups like ``spaider agent ...``).
"""
