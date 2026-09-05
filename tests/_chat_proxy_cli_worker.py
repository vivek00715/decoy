"""Standalone worker for test_chat_proxy_cli_audit_logging.py -- runs the
REAL `decoy chat-proxy` CLI entry point (decoy.cli.main, the same
argparse dispatch a real `decoy chat-proxy` invocation goes through) as
its own OS process, not chat_proxy.py's internal functions directly.

This is deliberate: the confirmed bug this test guards against
(_cmd_chat_proxy never wiring in an audit_log) lived entirely at the CLI
dispatch layer -- chat_proxy.py's own underlying code was already
correct (its audit_log parameter worked fine when a caller passed one).
Calling chat_proxy.create_app()/main() directly, the way earlier tests
in test_chat_proxy.py do, would NOT have caught this; only going through
decoy.cli.main(["chat-proxy", ...]) does.

Usage: python _chat_proxy_cli_worker.py <port>
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Points the proxy at a port nothing listens on, so the upstream call
# fails fast with no real network access -- the audit entry this test
# checks for is written by the masking step, which happens BEFORE the
# upstream call, so an upstream failure here doesn't affect what's being
# tested.
os.environ.setdefault("DECOY_ANTHROPIC_UPSTREAM_URL", "http://127.0.0.1:9")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-not-real")

from decoy.cli import main  # noqa: E402

port = sys.argv[1]
sys.exit(main(["chat-proxy", "--port", port]))
