"""Fetch exactly the reviewed upstream commit and apply recorded, hashed patches."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def git(directory, *args):
    environment = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                   "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.check_output(["git", "-c", "core.hooksPath=" + os.devnull, *args],
                                   cwd=directory, env=environment, text=True).strip()


def fetch(output, lock, root=ROOT, mirror=None):
    repository, revision = lock["repository"], lock["commit"]
    if (not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git", repository)
            or not re.fullmatch(r"[0-9a-f]{40}", revision)):
        raise ValueError("A public GitHub URL and full reviewed commit are required")
    patches = []
    for entry in lock.get("patches", []):
        path = root / entry["file"]
        if (path.is_symlink() or not path.resolve().is_relative_to(root.resolve())
                or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]):
            raise ValueError("Recorded upstream patch is missing or changed")
        patches.append(path.resolve())
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("New source checkout required")
    output.mkdir(parents=True)
    git(output, "init", "--quiet")
    git(output, "remote", "add", "origin", repository)
    git(output, "fetch", "--quiet", "--depth=1", str(mirror.resolve()) if mirror else repository, revision)
    git(output, "checkout", "--quiet", "--detach", "FETCH_HEAD")
    if git(output, "rev-parse", "HEAD") != revision:
        raise ValueError("Fetched source identity differs")
    for path in patches:
        git(output, "apply", "--check", str(path))
        git(output, "apply", "--index", str(path))
    report = {"repository": repository, "commit": revision, "patches": lock.get("patches", [])}
    (output / ".source-receipt.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(fetch(args.output, json.loads((ROOT / "upstream.lock.json").read_text()))))
