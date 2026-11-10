"""Deterministic CBOR subset used by Leap Quantum metadata."""

from __future__ import annotations

from typing import Any


class CborError(ValueError):
    pass


MAX_DEPTH = 256


def _head(major: int, value: int) -> bytes:
    if value < 0:
        raise CborError("negative CBOR argument")
    prefix = major << 5
    if value < 24:
        return bytes([prefix | value])
    if value <= 0xff:
        return bytes([prefix | 24, value])
    if value <= 0xffff:
        return bytes([prefix | 25]) + value.to_bytes(2, "big")
    if value <= 0xffff_ffff:
        return bytes([prefix | 26]) + value.to_bytes(4, "big")
    if value <= 0xffff_ffff_ffff_ffff:
        return bytes([prefix | 27]) + value.to_bytes(8, "big")
    raise CborError("CBOR integer exceeds uint64")


def encode(value: Any, _depth: int = 0) -> bytes:
    if _depth > MAX_DEPTH:
        raise CborError("metadata nesting exceeds limit")
    if value is None:
        return b"\xf6"
    if value is False:
        return b"\xf4"
    if value is True:
        return b"\xf5"
    if isinstance(value, int):
        if not -(1 << 63) <= value < (1 << 63):
            raise CborError("metadata integer exceeds signed 64-bit range")
        return _head(0, value) if value >= 0 else _head(1, -1 - value)
    if isinstance(value, str):
        raw = value.encode("utf-8", errors="strict")
        return _head(3, len(raw)) + raw
    if isinstance(value, list):
        return _head(4, len(value)) + b"".join(encode(item, _depth + 1) for item in value)
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise CborError("metadata map keys must be text")
        pairs = [(encode(key), encode(item, _depth + 1)) for key, item in value.items()]
        pairs.sort(key=lambda pair: (len(pair[0]), pair[0]))
        return _head(5, len(pairs)) + b"".join(key + item for key, item in pairs)
    raise CborError(f"unsupported metadata type: {type(value).__name__}")


class _Reader:
    def __init__(self, raw: bytes):
        self.raw = raw
        self.at = 0

    def take(self, size: int) -> bytes:
        end = self.at + size
        if end > len(self.raw):
            raise CborError("truncated CBOR")
        value = self.raw[self.at:end]
        self.at = end
        return value

    def argument(self, additional: int) -> int:
        if additional < 24:
            return additional
        widths = {24: 1, 25: 2, 26: 4, 27: 8}
        if additional not in widths:
            raise CborError("indefinite or reserved CBOR value")
        raw = self.take(widths[additional])
        value = int.from_bytes(raw, "big")
        if value < 24 or (additional == 25 and value <= 0xff) or (additional == 26 and value <= 0xffff) or (additional == 27 and value <= 0xffff_ffff):
            raise CborError("non-canonical CBOR integer or length")
        return value

    def item(self, depth: int = 0) -> Any:
        if depth > MAX_DEPTH:
            raise CborError("metadata nesting exceeds limit")
        initial = self.take(1)[0]
        major, additional = initial >> 5, initial & 31
        if major in (0, 1):
            value = self.argument(additional)
            return value if major == 0 else -1 - value
        if major == 3:
            try:
                return self.take(self.argument(additional)).decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise CborError("metadata text is not UTF-8") from exc
        if major == 4:
            return [self.item(depth + 1) for _ in range(self.argument(additional))]
        if major == 5:
            count = self.argument(additional)
            result: dict[str, Any] = {}
            encoded_keys: list[bytes] = []
            for _ in range(count):
                start = self.at
                key = self.item()
                encoded_key = self.raw[start:self.at]
                if not isinstance(key, str) or key in result:
                    raise CborError("metadata keys must be unique text")
                encoded_keys.append(encoded_key)
                result[key] = self.item(depth + 1)
            if encoded_keys != sorted(encoded_keys, key=lambda raw: (len(raw), raw)):
                raise CborError("metadata map keys are not canonically ordered")
            return result
        if major == 7 and additional in (20, 21, 22):
            return {20: False, 21: True, 22: None}[additional]
        raise CborError("unsupported CBOR metadata value")


def decode(raw: bytes) -> Any:
    reader = _Reader(raw)
    value = reader.item()
    if reader.at != len(raw) or encode(value) != raw:
        raise CborError("trailing or non-canonical CBOR")
    return value
