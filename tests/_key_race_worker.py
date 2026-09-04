"""Standalone worker process for test_crypto_key_cross_process.py -- see
crypto.py's module docstring for the confirmed race this guards against
(10 concurrent processes racing to create the same key file for the
first time produced 5 different keys before the fix).

Usage: python _key_race_worker.py <key_path> <out_path>
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from decoy.crypto import load_or_create_key  # noqa: E402

key_path, out_path = sys.argv[1:3]

key = load_or_create_key(Path(key_path))
Path(out_path).write_bytes(key)
