"""PyInstaller entry point: runs the package ``__main__`` with a proper
package context (relative imports work; ``python -m spaider_foldable`` stays
the dev entry)."""

import sys

from spaider_foldable.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
