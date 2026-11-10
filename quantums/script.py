from __future__ import annotations

from .envelope import EnvelopeError


OP_RETURN = 0x6A


def single_push(payload: bytes) -> bytes:
    length = len(payload)
    if length <= 0x4B:
        prefix = bytes([length])
    elif length <= 0xFF:
        prefix = b"\x4c" + bytes([length])
    elif length <= 0xFFFF:
        prefix = b"\x4d" + length.to_bytes(2, "little")
    elif length <= 0xFFFF_FFFF:
        prefix = b"\x4e" + length.to_bytes(4, "little")
    else:
        raise EnvelopeError("payload is too large for a script data push")
    return prefix + payload


def quantum_op_return(payload: bytes) -> bytes:
    return bytes([OP_RETURN]) + single_push(payload)


def extract_single_push(script: bytes) -> bytes:
    if not script or script[0] != OP_RETURN:
        raise EnvelopeError("output is not OP_RETURN")
    if len(script) < 2:
        raise EnvelopeError("OP_RETURN has no payload")
    opcode = script[1]
    cursor = 2
    if opcode <= 0x4B:
        length = opcode
    elif opcode == 0x4C:
        width = 1
        if cursor + width > len(script):
            raise EnvelopeError("truncated OP_PUSHDATA1")
        length = script[cursor]
        cursor += width
    elif opcode == 0x4D:
        width = 2
        if cursor + width > len(script):
            raise EnvelopeError("truncated OP_PUSHDATA2")
        length = int.from_bytes(script[cursor : cursor + width], "little")
        cursor += width
    elif opcode == 0x4E:
        width = 4
        if cursor + width > len(script):
            raise EnvelopeError("truncated OP_PUSHDATA4")
        length = int.from_bytes(script[cursor : cursor + width], "little")
        cursor += width
    else:
        raise EnvelopeError("OP_RETURN does not contain a single data push")
    end = cursor + length
    if end != len(script):
        raise EnvelopeError("truncated payload or trailing script data")
    return script[cursor:end]
