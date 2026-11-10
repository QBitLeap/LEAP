#!/usr/bin/env python3
"""Generate canonical Leap Gallery record vectors for Core."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantums.envelope import EnvelopeError
from quantums.gallery import GalleryRoot, GalleryUpdate, apply, decode, encode


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tests/data/quantum_gallery_v1.json"
A = "aa" * 32 + ":0"
B = "aa" * 32 + ":1"
C = "bb" * 32 + ":300"
D = "cc" * 32 + ":0"


def document() -> dict:
    root = GalleryRoot("Leap Gallery", (A, B, C))
    update = GalleryUpdate(A, D, (C,), (B,))
    root_raw = encode(root)
    update_raw = encode(update)
    invalid = [
        ("trailing-root", root_raw + b"\x00"),
        ("wrong-version", root_raw[:4] + b"\x02" + root_raw[5:]),
        ("nonminimal-name-length", root_raw[:6] + b"\x8c\x00" + root_raw[7:]),
        ("bad-packed-tag", root_raw[:20] + b"\x02" + root_raw[21:]),
    ]
    for name, raw in invalid:
        try:
            decode(raw)
        except EnvelopeError:
            pass
        else:
            raise AssertionError(f"invalid Gallery vector accepted: {name}")
    return {
        "format": 1,
        "root": {"raw": root_raw.hex(), "name": root.name,
                 "members": list(root.members)},
        "update": {"raw": update_raw.hex(), "root": update.root,
                   "previous": update.previous, "add": list(update.add),
                   "remove": list(update.remove),
                   "before": [A, B], "after": list(apply((A, B), update))},
        "invalid": [{"id": name, "raw": raw.hex()} for name, raw in invalid],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = json.dumps(document(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text() != content:
            raise SystemExit(f"stale Gallery fixture: {OUTPUT}")
    else:
        OUTPUT.write_text(content)


if __name__ == "__main__":
    main()
