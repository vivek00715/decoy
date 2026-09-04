"""PyInstaller entry point wrapper. Not meant to be run directly by end
users -- see scripts/build_binary.py for why this exists (cli.py uses
package-relative imports that need `decoy` importable as a real
package, which requires `decoy` to be installed in the build
environment, e.g. `pip install -e ".[mcp]"`).
"""

import sys

from decoy.cli import main

if __name__ == "__main__":
    sys.exit(main())
