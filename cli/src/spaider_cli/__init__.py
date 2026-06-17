"""SpAIder CLI — install wizard, agent management, Claude Code skill installer.

Public surface:

    spaider init           # one-shot first-time wizard
    spaider doctor         # self-check + repair
    spaider agent ...      # create / list / rotate-key / delete
    spaider mcp install    # write ~/.claude/.mcp.json + spaider.md skill

Implementation lives in ``spaider_cli.commands``; this module only re-exports
the entry-point app so ``[project.scripts]`` resolution stays simple.
"""

__version__ = "0.1.0-dev"
