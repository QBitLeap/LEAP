"""Compact canonical records for permissionless Quantum galleries."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .envelope import EnvelopeError


MAGIC = b"QGAL"
VERSION = 1
ROOT = 1
UPDATE = 2
MIME_TYPE = "application/x-leap-gallery"
ID_RE = re.compile(r"^([0-9a-f]{64}):(0|[1-9][0-9]*)$")
MAX_MEMBERS = 100_000


def _uvarint(value: int) -> bytes:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value >= 1 << 64:
        raise EnvelopeError("gallery integer is outside uint64")
    result = bytearray()
    while value >= 0x80:
        result.append((value & 0x7f) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


class _Reader:
    def __init__(self, raw: bytes):
        self.raw, self.offset = raw, 0

    def take(self, length: int) -> bytes:
        if length < 0 or self.offset + length > len(self.raw):
            raise EnvelopeError("truncated gallery record")
        value = self.raw[self.offset:self.offset + length]
        self.offset += length
        return value

    def byte(self) -> int:
        return self.take(1)[0]

    def uvarint(self) -> int:
        start, value, shift = self.offset, 0, 0
        while True:
            byte = self.byte()
            value |= (byte & 0x7f) << shift
            if not byte & 0x80:
                break
            shift += 7
            if shift >= 64:
                raise EnvelopeError("gallery varint overflow")
        if self.raw[start:self.offset] != _uvarint(value):
            raise EnvelopeError("noncanonical gallery varint")
        return value

    def finish(self) -> None:
        if self.offset != len(self.raw):
            raise EnvelopeError("trailing gallery record bytes")


def normalize_id(value: str) -> str:
    if not isinstance(value, str):
        raise EnvelopeError("Quantum ID must be text")
    match = ID_RE.fullmatch(value.lower())
    if not match:
        raise EnvelopeError("invalid gallery Quantum ID")
    return f"{match.group(1)}:{int(match.group(2))}"


def _id_bytes(value: str) -> bytes:
    txid, vout = normalize_id(value).split(":")
    return bytes.fromhex(txid) + _uvarint(int(vout))


def _one_id(reader: _Reader) -> str:
    txid = reader.take(32).hex()
    return f"{txid}:{reader.uvarint()}"


def _ids(values: tuple[str, ...], *, sorted_values: bool = False) -> bytes:
    normalized = tuple(normalize_id(value) for value in values)
    if len(normalized) > MAX_MEMBERS or len(set(normalized)) != len(normalized):
        raise EnvelopeError("gallery IDs are duplicated or exceed the limit")
    if sorted_values and normalized != tuple(sorted(normalized, key=_id_bytes)):
        raise EnvelopeError("gallery removals must be canonically sorted")
    result = bytearray(_uvarint(len(normalized)))
    previous_txid = None
    for value in normalized:
        txid, vout = value.split(":")
        if txid == previous_txid:
            result.append(0)
        else:
            result.append(1)
            result.extend(bytes.fromhex(txid))
            previous_txid = txid
        result.extend(_uvarint(int(vout)))
    return bytes(result)


def _read_ids(reader: _Reader) -> tuple[str, ...]:
    count = reader.uvarint()
    if count > MAX_MEMBERS:
        raise EnvelopeError("gallery member count exceeds limit")
    result, previous_txid = [], None
    for _ in range(count):
        tag = reader.byte()
        if tag == 1:
            previous_txid = reader.take(32).hex()
        elif tag != 0 or previous_txid is None:
            raise EnvelopeError("invalid packed gallery transaction ID")
        result.append(f"{previous_txid}:{reader.uvarint()}")
    if len(set(result)) != len(result):
        raise EnvelopeError("duplicate gallery IDs")
    return tuple(result)


@dataclass(frozen=True)
class GalleryRoot:
    name: str
    members: tuple[str, ...]


@dataclass(frozen=True)
class GalleryUpdate:
    root: str
    previous: str
    add: tuple[str, ...] = ()
    remove: tuple[str, ...] = ()


GalleryRecord = GalleryRoot | GalleryUpdate


def encode(record: GalleryRecord) -> bytes:
    if isinstance(record, GalleryRoot):
        name = record.name.encode("utf-8", "strict")
        if not name or len(name) > 255 or b"\x00" in name or b"\n" in name or b"\r" in name:
            raise EnvelopeError("invalid gallery name")
        return MAGIC + bytes((VERSION, ROOT)) + _uvarint(len(name)) + name + _ids(record.members)
    if isinstance(record, GalleryUpdate):
        additions = tuple(normalize_id(value) for value in record.add)
        removals = tuple(normalize_id(value) for value in record.remove)
        if not additions and not removals:
            raise EnvelopeError("gallery update must add or remove an ID")
        if set(additions) & set(removals):
            raise EnvelopeError("gallery update cannot add and remove the same ID")
        removals = tuple(sorted(removals, key=_id_bytes))
        return MAGIC + bytes((VERSION, UPDATE)) + _id_bytes(record.root) + _id_bytes(record.previous) + _ids(additions) + _ids(removals, sorted_values=True)
    raise EnvelopeError("unknown gallery record")


def decode(raw: bytes) -> GalleryRecord:
    reader = _Reader(raw)
    if reader.take(4) != MAGIC or reader.byte() != VERSION:
        raise EnvelopeError("unsupported gallery record")
    kind = reader.byte()
    if kind == ROOT:
        length = reader.uvarint()
        try:
            name = reader.take(length).decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise EnvelopeError("invalid gallery name") from exc
        record: GalleryRecord = GalleryRoot(name, _read_ids(reader))
    elif kind == UPDATE:
        record = GalleryUpdate(_one_id(reader), _one_id(reader), _read_ids(reader), _read_ids(reader))
    else:
        raise EnvelopeError("unknown gallery record kind")
    reader.finish()
    if encode(record) != raw:
        raise EnvelopeError("noncanonical gallery record")
    return record


def apply(members: tuple[str, ...], update: GalleryUpdate) -> tuple[str, ...]:
    current = list(normalize_id(value) for value in members)
    if any(value in current for value in update.add):
        raise EnvelopeError("gallery addition is already a member")
    if any(value not in current for value in update.remove):
        raise EnvelopeError("gallery removal is not a member")
    removed = set(update.remove)
    return tuple(value for value in current if value not in removed) + tuple(update.add)
