"""Build the pinned standalone indexer; publication requires --publish."""

import argparse
import json
from pathlib import Path

from fetch_upstream import fetch
from prepare_context import prepare
from release_common import build, stage, write_report

ROOT = Path(__file__).resolve().parents[1]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    output, revision = stage(ROOT, args.version)
    upstream = output / "upstream"
    fetch(upstream, json.loads((ROOT / "upstream.lock.json").read_text()))
    context = output / "context"
    prepare(upstream, context)
    report = build(ROOT, context, ROOT.name.removesuffix("-umbrel") + "-indexer",
                   args.version, revision, args.publish)
    write_report(output, [report])
