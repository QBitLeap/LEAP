"""Protocol-neutral inventory of Qbit OP_RETURN output data."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from quantums.envelope import EnvelopeError, decode_any
from quantums.script import quantum_op_return


@dataclass(frozen=True)
class DataOutput:
    vout: int
    script: bytes
    payload: bytes
    push_encoding: str
    canonical_single_push: bool
    readable_preview: str | None
    media_hint: str | None
    leap_items: int

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()


def _read_push(script: bytes, cursor: int) -> tuple[bytes, int] | None:
    if cursor >= len(script):
        return None
    opcode = script[cursor]
    cursor += 1
    if opcode == 0:
        return b"", cursor
    if opcode <= 0x4B:
        length = opcode
    elif opcode == 0x4C:
        if cursor + 1 > len(script):
            return None
        length, cursor = script[cursor], cursor + 1
    elif opcode == 0x4D:
        if cursor + 2 > len(script):
            return None
        length, cursor = int.from_bytes(script[cursor:cursor + 2], "little"), cursor + 2
    elif opcode == 0x4E:
        if cursor + 4 > len(script):
            return None
        length, cursor = int.from_bytes(script[cursor:cursor + 4], "little"), cursor + 4
    else:
        return None
    end = cursor + length
    return None if end > len(script) else (script[cursor:end], end)


def _payload(script: bytes) -> tuple[bytes, str, bool]:
    if not script or script[0] != 0x6A:
        raise ValueError("not OP_RETURN")
    cursor = 1
    pushes: list[bytes] = []
    while cursor < len(script):
        result = _read_push(script, cursor)
        if result is None:
            return script[1:], "non-push-script", False
        pushed, cursor = result
        pushes.append(pushed)
    payload = b"".join(pushes)
    canonical = len(pushes) == 1 and script == quantum_op_return(payload)
    return payload, ("single-push" if len(pushes) == 1 else "multi-push"), canonical


def _preview(payload: bytes, maximum: int = 160) -> str | None:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None
    printable = "".join(character if character.isprintable() or character in "\n\r\t" else "�" for character in text)
    return printable[:maximum] if printable else None


def _media_hint(payload: bytes) -> str | None:
    signatures = (
        (b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif"),
        (b"%PDF-", "application/pdf"), (b"PK\x03\x04", "application/zip"),
    )
    for magic, media_type in signatures:
        if payload.startswith(magic):
            return media_type
    try:
        decoded = payload.decode("utf-8")
        json.loads(decoded)
        return "application/json"
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "text/plain" if _preview(payload) is not None else None


def inventory_transaction(transaction: dict[str, Any]) -> tuple[DataOutput, ...]:
    outputs = transaction.get("vout")
    if not isinstance(outputs, list):
        return ()
    found: list[DataOutput] = []
    for fallback_vout, output in enumerate(outputs):
        script_info = output.get("scriptPubKey", output.get("scriptpubkey", {}))
        script_hex = script_info.get("hex") if isinstance(script_info, dict) else script_info
        if not isinstance(script_hex, str):
            continue
        try:
            script = bytes.fromhex(script_hex)
            if not script or script[0] != 0x6A:
                continue
            payload, encoding, canonical = _payload(script)
        except ValueError:
            continue
        leap_items = 0
        if canonical:
            try:
                leap_items = len(decode_any(payload))
            except EnvelopeError:
                pass
        vout = output.get("n", fallback_vout)
        found.append(DataOutput(int(vout), script, payload, encoding, canonical, _preview(payload), _media_hint(payload), leap_items))
    return tuple(found)
