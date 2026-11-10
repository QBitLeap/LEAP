#!/usr/bin/env python3
"""Pin canonical Quantum creation discovery for a future Core parser."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from indexer.discovery import discover_transaction
from quantums.envelope import Artifact, encode, encode_batch
from quantums.script import quantum_op_return


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tests/data/quantum_discovery_v1.json"
HEIGHT = 123


def h(number: int) -> str:
    return f"{number:064x}"


def artifact(name: str, content: bytes, owner: int, *, version: int,
             metadata: object | None = None, parent: str | None = None,
             source_utxo: str | None = None) -> Artifact:
    return Artifact("text/plain", name, content, hashlib.sha256(content).hexdigest(),
                    owner, metadata=metadata, parent=parent, source_utxo=source_utxo,
                    format_version=version)


def owner(seed: int) -> dict:
    return {"value_bits": 1, "value": 1, "scriptPubKey": {
        "type": "witness_v2_p2mr", "hex": (b"\x52\x20" + bytes([seed]) * 32).hex()}}


def data(payload: bytes, *, value: int = 0, script: bytes | None = None) -> dict:
    return {"value_bits": value, "value": value, "scriptPubKey": {
        "type": "nulldata", "hex": (script if script is not None else quantum_op_return(payload)).hex()}}


def tx(number: int, outputs: list[dict]) -> dict:
    return {"txid": h(number), "vout": outputs}


def case(name: str, transaction: dict, *, valid: bool) -> dict:
    discovered = discover_transaction(transaction, height=HEIGHT, transaction_index=1)
    rows = [{"identity_txid": transaction["txid"],
             "identity_index": item.artifact.ownership_output,
             "creation_height": item.height,
             "data_output_index": item.data_output,
             "envelope_index": item.envelope_index,
             "format_version": item.artifact.format_version,
             "content_type": item.artifact.content_type,
             "name": item.artifact.name,
             "content_sha256": item.artifact.content_sha256,
             "parents": ([item.artifact.parent] if isinstance(item.artifact.parent, str)
                         else list(item.artifact.parent or ())),
             "content": item.artifact.content.hex()}
            for item in discovered]
    if valid != bool(rows):
        raise AssertionError(f"unexpected discovery for {name}")
    return {"id": name, "transaction": transaction, "expected": rows}


def document() -> dict:
    one = artifact("one.txt", b"one", 0, version=1)
    two = artifact("two.txt", b"two", 0, version=2,
                   metadata={"rank": 2, "kind": "member"}, parent=f"{h(99)}:0")
    three = artifact("three.txt", b"three", 1, version=2,
                     source_utxo=f"{h(88)}:1")
    v1 = encode(one)
    v2 = encode_batch((two, three))
    duplicate_owner = encode_batch((two, artifact("other.txt", b"other", 0, version=2)))
    corrupted = bytearray(v1)
    corrupted[-1] ^= 1
    nonminimal = b"\x6a\x4d" + len(v1).to_bytes(2, "little") + v1
    empty_owner = owner(10)
    empty_owner["value"] = 0
    empty_owner["value_bits"] = 0
    valid = [
        case("single-v1", tx(1, [owner(1), data(v1)]), valid=True),
        case("batch-v2-metadata-parent-source", tx(2, [owner(2), owner(3), data(v2)]), valid=True),
        case("two-data-outputs", tx(3, [owner(4), owner(5), data(v1),
                                      data(encode_batch((artifact("four.txt", b"four", 1, version=2),)))]), valid=True),
    ]
    invalid = [
        case("nonminimal-push", tx(4, [owner(6), data(v1, script=nonminimal)]), valid=False),
        case("wrong-content-hash", tx(5, [owner(7), data(bytes(corrupted))]), valid=False),
        case("missing-p2mr-owner", tx(6, [{"value_bits": 1, "value": 1,
                                          "scriptPubKey": {"type": "pubkeyhash", "hex": "51"}}, data(v1)]), valid=False),
        case("nonzero-data-output", tx(7, [owner(8), data(v1, value=1)]), valid=False),
        case("duplicate-batch-owner", tx(8, [owner(9), data(duplicate_owner)]), valid=False),
        case("zero-value-owner", tx(9, [empty_owner, data(v1)]), valid=False),
    ]
    return {"format": 1, "height": HEIGHT, "valid": valid, "invalid": invalid}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = json.dumps(document(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text() != content:
            raise SystemExit(f"stale Quantum discovery fixture: {OUTPUT}")
    else:
        OUTPUT.write_text(content)


if __name__ == "__main__":
    main()
