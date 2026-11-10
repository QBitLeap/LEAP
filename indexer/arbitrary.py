"""Deterministic catalog boundary for recognized non-Leap artifacts."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from typing import Final, Iterable

from .quantum import Location, OutPoint, QuantumIndexer, Transaction


ARBITRARY_DATABASE_NAME: Final = "arbitrary.sqlite3"


@dataclass(frozen=True, order=True)
class ArtifactPosition:
    height: int
    transaction_index: int
    output_index: int
    record_index: int = 0


@dataclass(frozen=True)
class Arbitrary:
    position: ArtifactPosition
    txid: str
    format: str
    payload: bytes
    name: str | None = None
    bound_output: int | None = None
    binding_status: str = "unbound-no-adapter"


def recognize_qpet_v1(payload: bytes, *, position: ArtifactPosition, txid: str) -> Arbitrary | None:
    """Recognize QPET v1; transaction validation applies its binding adapter.

    QPET v1 is ``QPET``, version 1, a uint16-le JSON length, that JSON, and a
    remaining sprite sheet.  Recognition alone does not fabricate a location.
    """

    if len(payload) < 7 or payload[:5] != b"QPET\x01":
        return None
    metadata_end = 7 + int.from_bytes(payload[5:7], "little")
    if metadata_end > len(payload):
        return None
    try:
        metadata = json.loads(payload[7:metadata_end].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    sheet = metadata.get("sheet") if isinstance(metadata, dict) else None
    sprite = payload[metadata_end:]
    if (
        not isinstance(metadata, dict)
        or metadata.get("format") != "codex-pet-v1"
        or metadata.get("kind") != "object"
        or not isinstance(sheet, dict)
        or sheet.get("contentType") != "image/webp"
        or sheet.get("length") != len(sprite)
        or sheet.get("sha256") != hashlib.sha256(sprite).hexdigest()
        or not sprite
    ):
        return None
    name = metadata.get("displayName")
    return Arbitrary(position, txid, "qpet-v1", payload, name if isinstance(name, str) else None)


def bind_qpet_v1(record: Arbitrary, transaction: Transaction) -> Arbitrary:
    """Apply QPET v1's format-specific vout-1, offset-0 binding rule."""

    if record.format != "qpet-v1":
        raise ValueError("QPET binding adapter requires a validated QPET v1 record")
    if transaction.txid.lower() != record.txid.lower():
        raise ValueError("QPET record and transaction identifiers differ")
    if len(transaction.outputs) <= 1:
        return replace(record, binding_status="invalid-binding-missing-vout-1")
    output = transaction.outputs[1]
    if output.value <= 0:
        return replace(record, binding_status="invalid-binding-zero-value-vout-1")
    if output.script.startswith(b"\x6a"):
        return replace(record, binding_status="invalid-binding-unspendable-vout-1")
    return replace(record, bound_output=1, binding_status="bound")


def register_qpet_v1(
    record: Arbitrary,
    transaction: Transaction,
    tracker: QuantumIndexer,
    *,
    artifact_id: str,
) -> Arbitrary:
    """Bind a QPET and register its bit in the shared sparse location tracker."""

    bound = bind_qpet_v1(record, transaction)
    if bound.bound_output is not None:
        output = transaction.outputs[bound.bound_output]
        tracker.add(
            artifact_id,
            Location(OutPoint(transaction.txid, bound.bound_output), 0),
            address=output.address,
        )
    return bound


def number_arbitraries(records: Iterable[Arbitrary]) -> tuple[tuple[int, Arbitrary], ...]:
    """Number recognized records by block, transaction, output, then record."""

    ordered = sorted(records, key=lambda record: record.position)
    if len({record.position for record in ordered}) != len(ordered):
        raise ValueError("two Arbitraries occupy the same chain position")
    return tuple(enumerate(ordered, start=1))
