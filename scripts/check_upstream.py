"""Report upstream movement without changing source locks or running services."""

import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


if __name__ == "__main__":
    lock = json.loads((ROOT / "upstream.lock.json").read_text())
    value = subprocess.check_output(["git", "ls-remote", lock["repository"], "HEAD"], text=True, timeout=60, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}).split()[0]
    print(json.dumps({"repository": lock["repository"], "pinnedCommit": lock["commit"],
                      "upstreamHead": value, "reviewAvailable": value != lock["commit"],
                      "sourceLockChanged": False}, indent=2))
