"""Canonical Quantum discovery from decoded Qbit transactions."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from quantums.envelope import Artifact, EnvelopeError, decode_any
from quantums.script import extract_single_push, quantum_op_return


P2MR_TYPES = {"witness_v2_mast", "witness_v2_p2mr", "v2_p2mr"}


@dataclass(frozen=True)
class DiscoveredQuantum:
    height: int
    transaction_index: int
    data_output: int
    envelope_index: int
    quantum_id: str
    artifact: Artifact
    address: str | None


def _script(output: dict[str, Any]) -> dict[str, Any]:
    script = output.get("scriptPubKey", output.get("scriptpubkey", {}))
    return script if isinstance(script, dict) else {"hex": script}


def _zero_value(output: dict[str, Any]) -> bool:
    try:
        if "value_bits" in output:
            return type(output["value_bits"]) is int and output["value_bits"] == 0
        return Decimal(str(output.get("value", 0))) == 0
    except (TypeError, ValueError, InvalidOperation):
        return False


def _positive_value(output: dict[str, Any]) -> bool:
    try:
        if "value_bits" in output:
            return type(output["value_bits"]) is int and output["value_bits"] > 0
        return Decimal(str(output.get("value", 0))) > 0
    except (TypeError, ValueError, InvalidOperation):
        return False


def discover_transaction(transaction: dict[str, Any], *, height: int, transaction_index: int) -> tuple[DiscoveredQuantum, ...]:
    """Return only canonical envelopes bound to valid P2MR ownership outputs."""
    txid = transaction.get("txid")
    outputs = transaction.get("vout")
    if not isinstance(txid, str) or len(txid) != 64 or not isinstance(outputs, list):
        return ()
    discovered: list[DiscoveredQuantum] = []
    claimed_outputs: set[int] = set()
    for data_vout, output in enumerate(outputs):
        script_hex = _script(output).get("hex")
        if not isinstance(script_hex, str) or not _zero_value(output):
            continue
        try:
            script = bytes.fromhex(script_hex)
            payload = extract_single_push(script)
            if script != quantum_op_return(payload):
                continue
            artifacts = decode_any(payload)
        except (ValueError, EnvelopeError):
            continue
        positions = [artifact.ownership_output for artifact in artifacts]
        if len(set(positions)) != len(positions) or claimed_outputs.intersection(positions):
            continue
        candidates: list[DiscoveredQuantum] = []
        for envelope_index, artifact in enumerate(artifacts):
            if artifact.ownership_output >= len(outputs):
                break
            owner_script = _script(outputs[artifact.ownership_output])
            if owner_script.get("type") not in P2MR_TYPES or not _positive_value(outputs[artifact.ownership_output]):
                break
            address = owner_script.get("address")
            if not isinstance(address, str):
                addresses = owner_script.get("addresses")
                address = addresses[0] if isinstance(addresses, list) and len(addresses) == 1 else None
            candidates.append(
                DiscoveredQuantum(
                    height, transaction_index, data_vout, envelope_index,
                    f"{txid.lower()}:{artifact.ownership_output}", artifact, address,
                )
            )
        else:
            discovered.extend(candidates)
            claimed_outputs.update(positions)
    return tuple(discovered)


def discover_block(block: dict[str, Any]) -> tuple[DiscoveredQuantum, ...]:
    height = block.get("height")
    transactions = block.get("tx")
    if not isinstance(height, int) or not isinstance(transactions, list):
        return ()
    found: list[DiscoveredQuantum] = []
    for transaction_index, transaction in enumerate(transactions):
        if isinstance(transaction, dict):
            found.extend(discover_transaction(transaction, height=height, transaction_index=transaction_index))
    return tuple(found)
