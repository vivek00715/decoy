#!/usr/bin/env bash
# Fallback installer for the Phase 10 benchmark pre-commit check, for
# anyone who doesn't want to install the `pre-commit` framework (see
# .pre-commit-config.yaml for that route instead). This writes a plain
# git hook directly into .git/hooks/pre-commit -- .git/hooks/ is NOT
# tracked by git itself, so this script (which IS tracked) is how you
# actually get the hook installed after cloning.
#
# Usage: ./scripts/install-git-hooks.sh

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOK_PATH="$REPO_ROOT/.git/hooks/pre-commit"

cat > "$HOOK_PATH" <<'HOOK'
#!/usr/bin/env bash
# Installed by scripts/install-git-hooks.sh -- runs the Phase 10
# benchmark suite only when a change touches one of the three masking-
# quality-critical modules, so ordinary commits stay fast.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

changed_files="$(git diff --cached --name-only)"
if echo "$changed_files" | grep -qE '^src/decoy/(masker|record_masker|query_aware)\.py$'; then
    echo "Decoy: masker.py/record_masker.py/query_aware.py changed -- running benchmark suite..."
    if ! python -m pytest tests/test_benchmark_suite.py -q; then
        echo ""
        echo "Decoy: benchmark suite failed. Commit blocked -- see failures above."
        echo "(bypass with 'git commit --no-verify' if you're certain this is intentional)"
        exit 1
    fi
fi
HOOK

chmod +x "$HOOK_PATH"
echo "Installed pre-commit hook at $HOOK_PATH"
