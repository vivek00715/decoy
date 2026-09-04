#!/usr/bin/env python3
"""Build a standalone `decoy-proxy` executable with PyInstaller.

Phase 12: package Decoy's Python core so end users can run the MCP
masking proxy (and the rest of the `decoy` CLI) without Python or pip
installed at all. PyInstaller was chosen over Nuitka for this: it needs
no C compiler / MSVC-on-Windows toolchain on the build machine (Nuitka
compiles to C and needs one, which complicates the three-OS CI matrix
this targets), and it has mature, actively maintained hooks for exactly
the C-extension-heavy deps this project pulls in (cryptography,
sqlalchemy, pydantic-core used transitively by `mcp`) via
`pyinstaller-hooks-contrib` -- Nuitka's onefile mode has historically
been rougher on macOS code-signing/notarization, which matters here
since this binary needs to just run for end users with no dev setup.

Entry point: `decoy.cli:main` (NOT `decoy.mcp_proxy:main` directly) --
the built binary is the full `decoy` CLI (version/audit/overrides/proxy
subcommands), matching the project's actual `[project.scripts]` entry
point, so packaging it doesn't create a second, divergent entry point
from what `pip install decoy` gives you. It's named `decoy-proxy`
because the MCP proxy subcommand is the reason this binary needs to run
with no Python installed at all (a real MCP client like Claude Code
launches it as a subprocess) -- the other subcommands (version, audit,
overrides) come along for free since they're the same CLI.

What's bundled vs. left out, and why (checked via grep for unconditional
top-level imports before deciding, not assumed):
  - `mcp` + `anyio`: BUNDLED. mcp_proxy.py imports `mcp.types`,
    `mcp.client.*`, `mcp.server.*` unconditionally at module load time,
    and the whole point of this binary is running `decoy proxy`.
  - `cryptography`: BUNDLED (it's a core, non-optional dependency --
    vault.py imports it unconditionally).
  - `sqlalchemy`: BUNDLED (core dependency), but its dialect plugins
    (postgresql/mysql/etc. drivers) are NOT explicitly bundled -- grep
    confirms no `create_engine` call anywhere in src/decoy/*.py; engines
    are always supplied by the CALLING application, so a packaged
    decoy-proxy binary never constructs its own DB connection and never
    needs a specific dialect driver bundled.
  - `presidio-analyzer` (the `ner` extra): LEFT OUT. Not imported
    anywhere at module load time outside its own optional module.
  - `anthropic` (the `llm` extra): LEFT OUT. Same reasoning.
Run `grep -rn '^import\|^from' src/decoy/*.py` yourself before changing
this list if new unconditional imports get added later.

Note on `mcp`: do NOT blanket `--collect-submodules mcp` -- it pulls in
`mcp.cli`, which unconditionally imports `typer` (an mcp[cli]-only
extra Decoy doesn't depend on and doesn't install), and PyInstaller's
submodule collection fails outright if a submodule fails to import.
`mcp.client.*` and `mcp.server.*` (what mcp_proxy.py actually uses) are
collected explicitly instead.

Usage:
    python3 -m venv venv && source venv/bin/activate   # or reuse an existing venv
    pip install -e ".[mcp]" ".[build]"
    python scripts/build_binary.py

Produces dist/decoy-proxy (dist/decoy-proxy.exe on Windows).
"""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# NOT src/decoy/cli.py directly -- cli.py uses package-relative imports
# (`from . import __version__`) that only resolve when it's imported as
# part of the `decoy` package, not run as a standalone script. This tiny
# wrapper imports `decoy.cli` normally (via the installed package, same
# as the `decoy` console-script entry point does) and just calls its
# `main()`, so PyInstaller's analysis starts from a script that behaves
# exactly like the real entry point.
ENTRY_POINT = REPO_ROOT / "scripts" / "_pyinstaller_entry.py"
BINARY_NAME = "decoy-proxy"

# Hidden imports PyInstaller's static analysis can miss because they're
# reached dynamically (importlib / plugin-style loading) rather than via
# a plain top-level `import` PyInstaller's AST scan can see.
HIDDEN_IMPORTS = [
    # cryptography's backend is loaded via a Rust extension module whose
    # exact submodule PyInstaller's scan can miss depending on version.
    "cryptography.hazmat.bindings._rust",
    # mcp's stdio client/server + low-level Server -- used by mcp_proxy.py
    # but only imported inside decoy.mcp_proxy, which cli.py's `proxy`
    # subcommand imports lazily (see cli.py's _cmd_proxy), so make sure
    # PyInstaller's static scan (which starts from cli.py) still picks it
    # up rather than silently dropping a lazily-imported package.
    "mcp",
    "mcp.types",
    "mcp.client.session",
    "mcp.client.stdio",
    "mcp.server.lowlevel",
    "mcp.server.stdio",
    "anyio",
    "anyio._backends._asyncio",
]

# Full package trees to sweep in, for packages whose submodules PyInstaller's
# static import scan can't fully enumerate (dynamic imports elsewhere in
# each package's own code). `mcp` itself is deliberately NOT swept wholesale
# here -- see the module docstring's note on `mcp.cli`/`typer`; its
# client/server submodules are covered by the explicit hidden imports above
# instead.
COLLECT_SUBMODULES = ["mcp.client", "mcp.server", "anyio", "cryptography"]


def main() -> int:
    binary_name = BINARY_NAME + (".exe" if platform.system() == "Windows" else "")

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--name",
        BINARY_NAME,
        "--distpath",
        str(REPO_ROOT / "dist"),
        "--workpath",
        str(REPO_ROOT / "build"),
        "--specpath",
        str(REPO_ROOT / "build"),
        "--clean",
        "--noconfirm",
    ]
    for hi in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", hi]
    for pkg in COLLECT_SUBMODULES:
        cmd += ["--collect-submodules", pkg]
    cmd.append(str(ENTRY_POINT))

    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    if result.returncode != 0:
        print(f"PyInstaller build failed (exit {result.returncode})", file=sys.stderr)
        return result.returncode

    built = REPO_ROOT / "dist" / binary_name
    if not built.exists():
        print(f"Build reported success but {built} is missing", file=sys.stderr)
        return 1

    size_mb = built.stat().st_size / (1024 * 1024)
    print(f"Built {built} ({size_mb:.2f} MiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
