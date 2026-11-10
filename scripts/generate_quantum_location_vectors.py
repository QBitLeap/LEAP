#!/usr/bin/env python3
"""Pin inactive Core Quantum bit movement to Leap's existing indexer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from indexer.quantum import Block, Location, OutPoint, QuantumIndexer, Transaction, TxInput, TxOutput


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tests/data/quantum_location_v2.json"


def h(n: int) -> str:
    return f"{n:064x}"


def point(txid: int, index: int) -> dict:
    return {"txid": h(txid), "index": index}


def location(txid: int, index: int, offset: int) -> dict:
    return {"current": point(txid, index), "offset": offset}


def row(creation_txid: int, creation_index: int, current_txid: int,
        current_index: int, offset: int) -> dict:
    return {"id": point(creation_txid, creation_index),
            **location(current_txid, current_index, offset)}


def tx(txid: int, inputs: list[tuple[int, int, int]], outputs: list[int],
       created: list[int] | None = None) -> dict:
    return {"txid": h(txid),
            "inputs": [{"previous": point(source, index), "value": value}
                       for source, index, value in inputs],
            "outputs": outputs, "created": created or []}


def _parse_point(raw: dict) -> OutPoint:
    return OutPoint(raw["txid"], raw["index"])


def _parse_tx(raw: dict) -> Transaction:
    return Transaction(raw["txid"],
                       tuple(TxInput(_parse_point(item["previous"]), item["value"])
                             for item in raw["inputs"]),
                       tuple(TxOutput(value) for value in raw["outputs"]))


def _sort(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda item: (item["id"]["txid"], item["id"]["index"]))


def _case(name: str, before: list[dict], coinbase: dict,
          ordinary: list[dict], subsidy: int) -> dict:
    index = QuantumIndexer()
    for entry in before:
        identity = entry["id"]
        current = entry["current"]
        index.add(f'{identity["txid"]}:{identity["index"]}',
                  Location(_parse_point(current), entry["offset"]))
    # The Leap materializer discovers all new envelopes before moving bits.
    for transaction in [coinbase, *ordinary]:
        for output_index in transaction["created"]:
            identity = point(int(transaction["txid"], 16), output_index)
            index.add(f'{identity["txid"]}:{output_index}',
                      Location(_parse_point(identity), 0))
    block = Block(0, h(100), None, subsidy, _parse_tx(coinbase),
                  tuple(_parse_tx(item) for item in ordinary))
    index.connect(block)
    after = []
    for identifier, current in index.locations.items():
        creation_txid, creation_index = identifier.split(":")
        after.append({"id": {"txid": creation_txid, "index": int(creation_index)},
                      "current": {"txid": current.outpoint.txid,
                                  "index": current.outpoint.vout},
                      "offset": current.offset})
    for identifier in index.burned:
        creation_txid, creation_index = identifier.split(":")
        after.append({"id": {"txid": creation_txid, "index": int(creation_index)},
                      "burned": True})
    after = _sort(after)
    original = {(r["id"]["txid"], r["id"]["index"]): r for r in before}
    changes = []
    for current in after:
        key = (current["id"]["txid"], current["id"]["index"])
        prior = original.get(key)
        if prior != current:
            changes.append({"id": current["id"],
                            "before": None if prior is None else
                            {key: value for key, value in prior.items() if key != "id"},
                            "after": {key: value for key, value in current.items() if key != "id"}})
    return {"id": name, "before": _sort(before), "block": {
        "coinbase": coinbase, "ordinary": ordinary, "subsidy": subsidy},
        "after": after, "changes": changes}


def document() -> dict:
    cases = [
        _case("merge-split", [row(1, 0, 1, 0, 0)],
              tx(3, [], [51]), [tx(2, [(9, 1, 7), (1, 0, 10)], [5, 8, 3])], 50),
        _case("two-in-one-coin", [row(1, 0, 1, 0, 2), row(1, 1, 1, 0, 7)],
              tx(3, [], [50]), [tx(2, [(1, 0, 10)], [5, 5])], 50),
        _case("fee-to-coinbase", [row(1, 0, 1, 0, 9)],
              tx(3, [], [51, 1]), [tx(2, [(1, 0, 10)], [8])], 50),
        _case("ordered-fees", [row(1, 0, 1, 0, 4), row(2, 0, 2, 0, 4)],
              tx(12, [], [100, 1, 1]),
              [tx(10, [(1, 0, 5)], [4]), tx(11, [(2, 0, 5)], [4])], 100),
        _case("same-block-create-spend", [], tx(3, [], [50]),
              [tx(10, [(9, 0, 5)], [5], [0]),
               tx(11, [(10, 0, 5)], [5])], 50),
        _case("coinbase-create", [], tx(3, [], [50], [0]), [], 50),
        _case("underclaimed-fee-burn", [row(1, 0, 1, 0, 9)],
              tx(3, [], [51]), [tx(2, [(1, 0, 10)], [8])], 50),
    ]
    return {"format": 2, "valid": cases}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = json.dumps(document(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text() != content:
            raise SystemExit(f"stale Quantum location fixture: {OUTPUT}")
    else:
        OUTPUT.write_text(content)


if __name__ == "__main__":
    main()
