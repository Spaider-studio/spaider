"""SpAIder CLI — install wizard, agent management, Claude Code skill installer.

Public surface:

    spaider init           # one-shot first-time wizard
    spaider doctor         # self-check + repair
    spaider agent ...      # create / list / rotate-key / delete
    spaider mcp install    # write ~/.claude/.mcp.json + spaider.md skill

Implementation lives in ``spaider_cli.commands``; this module only re-exports
the entry-point app so ``[project.scripts]`` resolution stays simple.
"""

# Single source of truth is pyproject.toml; read it back from the installed
# package metadata so `spaider --version` can never drift from the wheel.
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("spaider-cli")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+source"
