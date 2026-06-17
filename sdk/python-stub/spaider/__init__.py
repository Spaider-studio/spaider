"""Reserved placeholder. Install ``spaider-client`` for the actual SDK."""

_MESSAGE = (
    "The 'spaider' package on PyPI is a reserved placeholder, not the "
    "actual Spaider Memory Infrastructure SDK.\n\n"
    "Install the real client:\n"
    "    pip install spaider-client\n\n"
    "Then import as:\n"
    "    from spaider import Spaider\n\n"
    "See https://pypi.org/project/spaider-client/"
)

raise RuntimeError(_MESSAGE)
