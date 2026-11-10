"""Resumable inventory of all Qbit mainnet OP_RETURN outputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from quantums.rpc import QbitRpc

from .arbitrary_data import inventory_transaction


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"format": 1, "chain": "main", "scope": "op_return_outputs", "last_height": -1, "outputs": []}
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("format") != 1 or state.get("chain") != "main" or not isinstance(state.get("outputs"), list):
        raise RuntimeError("invalid Leap arbitrary-data scan state")
    return state


def _save(path: Path, state: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def scan(rpc: QbitRpc, state_path: Path, *, checkpoint: int = 5_000) -> dict[str, Any]:
    state = _load(state_path)
    tip = int(rpc.call("getblockchaininfo")["blocks"])
    last_height = int(state["last_height"])
    if last_height > tip:
        raise RuntimeError("saved data scan is above the available mainnet tip")
    if last_height >= 0 and state.get("tip_hash") != rpc.call("getblockhash", last_height):
        raise RuntimeError("saved data scan is not on the active mainnet chain; rollback is required")
    for height in range(last_height + 1, tip + 1):
        block_hash = rpc.call("getblockhash", height)
        block = rpc.call("getblock", block_hash, 2)
        for transaction_index, transaction in enumerate(block.get("tx", [])):
            txid = transaction.get("txid")
            for item in inventory_transaction(transaction):
                state["outputs"].append({
                    "number": len(state["outputs"]) + 1,
                    "height": height,
                    "transaction_index": transaction_index,
                    "txid": txid,
                    "vout": item.vout,
                    "payload_bytes": len(item.payload),
                    "payload_sha256": item.sha256,
                    "script_bytes": len(item.script),
                    "push_encoding": item.push_encoding,
                    "canonical_single_push": item.canonical_single_push,
                    "media_hint": item.media_hint,
                    "readable_preview": item.readable_preview,
                    "within_quantum_envelope_limit": len(item.payload) <= 190_000,
                    "canonical_leap_items": item.leap_items,
                })
        state["last_height"] = height
        state["tip_hash"] = block_hash
        if height == tip or height % checkpoint == 0:
            _save(state_path, state)
            print(f"height {height}/{tip}: {len(state['outputs'])} data output(s)", flush=True)
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qbit-cli", required=True)
    parser.add_argument("--datadir", required=True)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--checkpoint", type=int, default=5_000)
    args = parser.parse_args()
    if args.checkpoint <= 0:
        parser.error("--checkpoint must be positive")
    state = scan(QbitRpc(cli=args.qbit_cli, chain="main", datadir=args.datadir), args.state, checkpoint=args.checkpoint)
    print(json.dumps({"height": state["last_height"], "data_outputs": len(state["outputs"])}, sort_keys=True))


if __name__ == "__main__":
    main()
