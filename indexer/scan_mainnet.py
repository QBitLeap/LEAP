"""Resumable Qbit mainnet scan for canonical Leap Quantums."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from quantums.rpc import QbitRpc

from .discovery import discover_block


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"format": 1, "chain": "main", "last_height": -1, "quantums": []}
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("format") != 1 or state.get("chain") != "main" or not isinstance(state.get("quantums"), list):
        raise RuntimeError("invalid Leap mainnet scan state")
    return state


def _save(path: Path, state: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def scan(rpc: QbitRpc, state_path: Path, *, stop_height: int | None = None, checkpoint: int = 100) -> dict[str, Any]:
    state = _load(state_path)
    info = rpc.call("getblockchaininfo")
    available_tip = int(info["blocks"])
    last_height = int(state["last_height"])
    if last_height > available_tip:
        raise RuntimeError("saved scan tip is above the available mainnet tip")
    if last_height >= 0:
        active_hash = rpc.call("getblockhash", last_height)
        if state.get("tip_hash") != active_hash:
            raise RuntimeError("saved scan tip is not on the active mainnet chain; rollback is required")
    target = available_tip if stop_height is None else min(stop_height, available_tip)
    start = last_height + 1
    for height in range(start, target + 1):
        block_hash = rpc.call("getblockhash", height)
        block = rpc.call("getblock", block_hash, 2)
        for item in discover_block(block):
            state["quantums"].append({
                "number": len(state["quantums"]) + 1,
                "id": item.quantum_id,
                "height": item.height,
                "transaction_index": item.transaction_index,
                "data_output": item.data_output,
                "envelope_index": item.envelope_index,
                "version": item.artifact.format_version,
                "name": item.artifact.name,
                "content_type": item.artifact.content_type,
                "content_length": len(item.artifact.content),
                "address_at_creation": item.address,
            })
        state["last_height"] = height
        state["tip_hash"] = block_hash
        if height == target or height % checkpoint == 0:
            _save(state_path, state)
            print(f"height {height}/{target}: {len(state['quantums'])} Quantum(s)", flush=True)
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qbit-cli", required=True)
    parser.add_argument("--datadir", required=True)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--stop-height", type=int)
    args = parser.parse_args()
    rpc = QbitRpc(cli=args.qbit_cli, chain="main", datadir=args.datadir)
    state = scan(rpc, args.state, stop_height=args.stop_height)
    print(json.dumps({"height": state["last_height"], "quantums": len(state["quantums"])}, sort_keys=True))


if __name__ == "__main__":
    main()
