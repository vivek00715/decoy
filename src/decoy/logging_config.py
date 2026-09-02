"""Structured application logging for Decoy.

This is deliberately separate from the privacy audit trail (audit_log.py,
added in Phase 7). Application logs describe what the *system* did
(files loaded, connections opened, errors) and must never contain real
or fake sensitive values -- the same rule the audit trail follows, but
enforced independently here since the two serve different purposes and
have different retention/inspection needs.
"""

from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False


def get_logger(name: str) -> logging.Logger:
    """Return a module logger, configuring the root Decoy logger once.

    Level is controlled by the DECOY_LOG_LEVEL env var (default INFO).
    Never pass sensitive values as log arguments -- this function does
    not scrub anything, callers are responsible for that guarantee.
    """
    global _CONFIGURED
    if not _CONFIGURED:
        level_name = os.environ.get("DECOY_LOG_LEVEL", "INFO").upper()
        level = getattr(logging, level_name, logging.INFO)
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        root = logging.getLogger("decoy")
        root.setLevel(level)
        root.addHandler(handler)
        root.propagate = False
        _CONFIGURED = True
    return logging.getLogger(f"decoy.{name}")
