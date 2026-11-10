from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import mimetypes
from pathlib import Path
import re
from typing import Callable

from . import cbor
from .content_policy import require_non_zip


MAGIC = b"QUANTUMS"
FORMAT_VERSION = 1
PREAMBLE = MAGIC + bytes([FORMAT_VERSION])
FORMAT_VERSION_2 = 2
PREAMBLE_V2 = MAGIC + bytes([FORMAT_VERSION_2])
MAX_ENVELOPE_BYTES = 190_000
MAX_NAME_BYTES = 255
MAX_COLLECTION_BYTES = 255
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
QUANTUM_ID_RE = re.compile(r"^[0-9a-f]{64}:[0-9]+$")
MEDIA_TYPE_RE = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,126}/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$"
)


class EnvelopeError(ValueError):
    pass


@dataclass(frozen=True)
class Artifact:
    content_type: str
    name: str
    content: bytes
    content_sha256: str
    ownership_output: int = 0
    collection: str | None = None
    metadata: object | None = None
    parent: str | tuple[str, ...] | None = None
    source_utxo: str | None = None
    format_version: int = 1

    @property
    def inscription_suffix(self) -> str:
        return f":{self.ownership_output}"


def _validate_text(value: str, label: str, maximum: int) -> None:
    if not value or "\n" in value or "\r" in value or "\x00" in value:
        raise EnvelopeError(f"invalid {label}")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise EnvelopeError(f"invalid {label}") from exc
    if len(encoded) > maximum:
        raise EnvelopeError(f"{label} is too long")


def validate_artifact(artifact: Artifact) -> None:
    if not MEDIA_TYPE_RE.fullmatch(artifact.content_type):
        raise EnvelopeError("invalid content type")
    _validate_text(artifact.name, "name", MAX_NAME_BYTES)
    if artifact.collection is not None:
        _validate_text(artifact.collection, "collection", MAX_COLLECTION_BYTES)
    if not isinstance(artifact.content, bytes) or not artifact.content:
        raise EnvelopeError("content must be non-empty bytes")
    digest = hashlib.sha256(artifact.content).hexdigest()
    if not SHA256_RE.fullmatch(artifact.content_sha256) or digest != artifact.content_sha256:
        raise EnvelopeError("content SHA-256 mismatch")
    if not isinstance(artifact.ownership_output, int) or artifact.ownership_output < 0:
        raise EnvelopeError("invalid ownership output")
    if artifact.metadata is not None:
        try:
            cbor.encode(artifact.metadata)
        except (cbor.CborError, UnicodeError) as exc:
            raise EnvelopeError(f"invalid metadata: {exc}") from exc
    parents = parent_ids(artifact.parent)
    if len(set(parents)) != len(parents):
        raise EnvelopeError("duplicate parent Quantum ID")
    if any(not QUANTUM_ID_RE.fullmatch(parent) for parent in parents):
        raise EnvelopeError("invalid parent Quantum ID")
    if artifact.source_utxo is not None and not QUANTUM_ID_RE.fullmatch(artifact.source_utxo):
        raise EnvelopeError("invalid source UTXO")
    if artifact.format_version not in (1, 2):
        raise EnvelopeError("unsupported artifact format version")


def validate_creation_artifact(artifact: Artifact) -> None:
    """Check new inscriptions without changing historical envelope validity."""
    validate_artifact(artifact)
    try:
        require_non_zip(artifact.content, artifact.content_type)
    except ValueError as exc:
        raise EnvelopeError(str(exc)) from exc


def parent_ids(parent: str | tuple[str, ...] | None) -> tuple[str, ...]:
    if parent is None:
        return ()
    if isinstance(parent, str):
        return (parent,)
    if isinstance(parent, tuple) and all(isinstance(value, str) for value in parent):
        return parent
    raise EnvelopeError("parent must be a Quantum ID or tuple of Quantum IDs")


def create_artifact(
    content: bytes,
    *,
    content_type: str,
    name: str,
    collection: str | None = None,
    metadata: object | None = None,
    parent: str | tuple[str, ...] | None = None,
    source_utxo: str | None = None,
    ownership_output: int = 0,
    format_version: int | None = None,
) -> Artifact:
    selected_version = format_version or (2 if metadata is not None or parent is not None or source_utxo is not None or ownership_output != 0 else 1)
    artifact = Artifact(
        content_type=content_type.lower(),
        name=name,
        content=content,
        content_sha256=hashlib.sha256(content).hexdigest(),
        collection=collection,
        metadata=metadata,
        parent=parent,
        source_utxo=source_utxo,
        ownership_output=ownership_output,
        format_version=selected_version,
    )
    validate_creation_artifact(artifact)
    return artifact


def encode(artifact: Artifact) -> bytes:
    validate_artifact(artifact)
    if artifact.format_version != 1 or artifact.ownership_output != 0 or artifact.metadata is not None or artifact.parent is not None or artifact.source_utxo is not None:
        raise EnvelopeError("version 1 does not support this Quantum")
    lines = [
        f"content-type:{artifact.content_type}",
        f"content-length:{len(artifact.content)}",
        f"content-sha256:{artifact.content_sha256}",
        f"name:{artifact.name}",
        "ownership-output:0",
    ]
    if artifact.collection is not None:
        lines.append(f"collection:{artifact.collection}")
    envelope = PREAMBLE + ("\n".join(lines) + "\n\n").encode("utf-8") + artifact.content
    if len(envelope) > MAX_ENVELOPE_BYTES:
        raise EnvelopeError(
            f"envelope is {len(envelope)} bytes; maximum is {MAX_ENVELOPE_BYTES}"
        )
    return envelope


def _encode_v2_item(artifact: Artifact) -> bytes:
    validate_artifact(artifact)
    if artifact.format_version != 2:
        raise EnvelopeError("version-2 batch requires version-2 artifacts")
    metadata = b"" if artifact.metadata is None else cbor.encode(artifact.metadata)
    lines = [
        f"content-type:{artifact.content_type}",
        f"content-length:{len(artifact.content)}",
        f"content-sha256:{artifact.content_sha256}",
        f"name:{artifact.name}",
        f"ownership-output:{artifact.ownership_output}",
    ]
    if artifact.collection is not None:
        lines.append(f"collection:{artifact.collection}")
    if artifact.metadata is not None:
        lines.extend((f"metadata-length:{len(metadata)}", f"metadata-sha256:{hashlib.sha256(metadata).hexdigest()}"))
    for parent in parent_ids(artifact.parent):
        lines.append(f"parent:{parent}")
    if artifact.source_utxo is not None:
        lines.extend((f"source-utxo:{artifact.source_utxo}", "source-offset:0"))
    return ("\n".join(lines) + "\n\n").encode("utf-8") + metadata + artifact.content


def encode_batch(artifacts: tuple[Artifact, ...]) -> bytes:
    if not artifacts:
        raise EnvelopeError("Quantum batch is empty")
    items = [_encode_v2_item(artifact) for artifact in artifacts]
    envelope = PREAMBLE_V2 + f"quantum-count:{len(items)}\n\n".encode("ascii")
    envelope += b"".join(len(item).to_bytes(4, "big") + item for item in items)
    if len(envelope) > MAX_ENVELOPE_BYTES:
        raise EnvelopeError(f"envelope is {len(envelope)} bytes; maximum is {MAX_ENVELOPE_BYTES}")
    return envelope


def split_batch(
    artifacts: tuple[Artifact, ...],
    ownership_outputs: Callable[[int, int], tuple[int, ...]],
) -> tuple[tuple[Artifact, ...], ...]:
    """Greedily pack indivisible Quantums in source order under the carrier cap."""
    if not artifacts:
        raise EnvelopeError("Quantum batch is empty")
    chunks: list[tuple[Artifact, ...]] = []
    start = 0
    while start < len(artifacts):
        accepted: tuple[Artifact, ...] | None = None
        stop = start + 1
        while stop <= len(artifacts):
            positions = ownership_outputs(start, stop)
            if len(positions) != stop - start:
                raise EnvelopeError("ownership-output planner returned the wrong count")
            candidate = tuple(
                replace(artifact, ownership_output=position)
                for artifact, position in zip(artifacts[start:stop], positions)
            )
            try:
                encode_batch(candidate)
            except EnvelopeError as exc:
                if "maximum" not in str(exc):
                    raise
                break
            accepted = candidate
            stop += 1
        if accepted is None:
            raise EnvelopeError(f"Quantum at batch index {start} exceeds the carrier limit")
        chunks.append(accepted)
        start += len(accepted)
    return tuple(chunks)


def decode(envelope: bytes) -> Artifact:
    if len(envelope) > MAX_ENVELOPE_BYTES:
        raise EnvelopeError("envelope exceeds maximum size")
    if not envelope.startswith(PREAMBLE):
        raise EnvelopeError("not a supported Quantum envelope")
    separator = envelope.find(b"\n\n", len(PREAMBLE))
    if separator < 0:
        raise EnvelopeError("missing header/body separator")
    try:
        header = envelope[len(PREAMBLE):separator].decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise EnvelopeError("header is not valid UTF-8") from exc
    body = envelope[separator + 2 :]
    lines = header.split("\n")
    fields: list[tuple[str, str]] = []
    for line in lines:
        if ":" not in line:
            raise EnvelopeError("malformed header field")
        key, value = line.split(":", 1)
        fields.append((key, value))
    required_order = [
        "content-type",
        "content-length",
        "content-sha256",
        "name",
        "ownership-output",
    ]
    keys = [key for key, _ in fields]
    if keys not in (required_order, required_order + ["collection"]):
        raise EnvelopeError("unknown, duplicate, or reordered header fields")
    values = dict(fields)
    try:
        declared_length = int(values["content-length"], 10)
        ownership_output = int(values["ownership-output"], 10)
    except ValueError as exc:
        raise EnvelopeError("invalid numeric header") from exc
    if str(declared_length) != values["content-length"] or declared_length != len(body):
        raise EnvelopeError("content length mismatch")
    artifact = Artifact(
        content_type=values["content-type"],
        name=values["name"],
        content=body,
        content_sha256=values["content-sha256"],
        ownership_output=ownership_output,
        collection=values.get("collection"),
    )
    validate_artifact(artifact)
    if encode(artifact) != envelope:
        raise EnvelopeError("non-canonical envelope")
    return artifact


def _decode_v2_item(raw: bytes) -> Artifact:
    separator = raw.find(b"\n\n")
    if separator < 0:
        raise EnvelopeError("missing version-2 item header separator")
    try:
        header = raw[:separator].decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise EnvelopeError("version-2 item header is not UTF-8") from exc
    fields: list[tuple[str, str]] = []
    for line in header.split("\n"):
        if ":" not in line:
            raise EnvelopeError("malformed version-2 item header")
        fields.append(tuple(line.split(":", 1)))
    required = ["content-type", "content-length", "content-sha256", "name", "ownership-output"]
    allowed_optional = ["collection", "metadata-length", "metadata-sha256", "parent", "source-utxo", "source-offset"]
    keys = [key for key, _ in fields]
    if keys[:5] != required or len(set(key for key in keys if key != "parent")) != len([key for key in keys if key != "parent"]):
        raise EnvelopeError("missing, duplicate, or reordered version-2 fields")
    optionals = keys[5:]
    expected_optionals = []
    for key in allowed_optional:
        expected_optionals.extend([key] * (optionals.count(key) if key == "parent" else int(key in optionals)))
    if optionals != expected_optionals:
        raise EnvelopeError("unknown or reordered version-2 fields")
    if ("metadata-length" in keys) != ("metadata-sha256" in keys):
        raise EnvelopeError("incomplete metadata commitment")
    if ("source-utxo" in keys) != ("source-offset" in keys):
        raise EnvelopeError("incomplete source UTXO commitment")
    values = dict(fields)
    parents = tuple(value for key, value in fields if key == "parent")
    try:
        content_length = int(values["content-length"], 10)
        ownership_output = int(values["ownership-output"], 10)
        metadata_length = int(values.get("metadata-length", "0"), 10)
    except ValueError as exc:
        raise EnvelopeError("invalid version-2 numeric field") from exc
    for number, original in ((content_length, values["content-length"]), (ownership_output, values["ownership-output"]), (metadata_length, values.get("metadata-length", "0"))):
        if number < 0 or str(number) != original:
            raise EnvelopeError("non-canonical version-2 numeric field")
    body = raw[separator + 2:]
    if len(body) != metadata_length + content_length:
        raise EnvelopeError("version-2 item body length mismatch")
    metadata_raw, content = body[:metadata_length], body[metadata_length:]
    metadata = None
    if "metadata-length" in values:
        if hashlib.sha256(metadata_raw).hexdigest() != values["metadata-sha256"]:
            raise EnvelopeError("metadata SHA-256 mismatch")
        try:
            metadata = cbor.decode(metadata_raw)
        except cbor.CborError as exc:
            raise EnvelopeError(f"invalid canonical metadata: {exc}") from exc
    if values.get("source-offset") not in (None, "0"):
        raise EnvelopeError("pick-utxo source offset must be zero")
    artifact = Artifact(
        content_type=values["content-type"],
        name=values["name"],
        content=content,
        content_sha256=values["content-sha256"],
        ownership_output=ownership_output,
        collection=values.get("collection"),
        metadata=metadata,
        parent=(parents[0] if len(parents) == 1 else parents or None),
        source_utxo=values.get("source-utxo"),
        format_version=2,
    )
    validate_artifact(artifact)
    if _encode_v2_item(artifact) != raw:
        raise EnvelopeError("non-canonical version-2 item")
    return artifact


def decode_batch(envelope: bytes) -> tuple[Artifact, ...]:
    if len(envelope) > MAX_ENVELOPE_BYTES or not envelope.startswith(PREAMBLE_V2):
        raise EnvelopeError("not a supported version-2 Quantum batch")
    separator = envelope.find(b"\n\n", len(PREAMBLE_V2))
    if separator < 0:
        raise EnvelopeError("missing batch header separator")
    header = envelope[len(PREAMBLE_V2):separator]
    match = re.fullmatch(rb"quantum-count:([1-9][0-9]*)", header)
    if match is None:
        raise EnvelopeError("invalid batch Quantum count")
    count = int(match.group(1))
    cursor = separator + 2
    artifacts: list[Artifact] = []
    for _ in range(count):
        if cursor + 4 > len(envelope):
            raise EnvelopeError("truncated version-2 item length")
        length = int.from_bytes(envelope[cursor:cursor + 4], "big")
        cursor += 4
        end = cursor + length
        if not length or end > len(envelope):
            raise EnvelopeError("truncated version-2 item")
        artifacts.append(_decode_v2_item(envelope[cursor:end]))
        cursor = end
    if cursor != len(envelope) or encode_batch(tuple(artifacts)) != envelope:
        raise EnvelopeError("trailing or non-canonical version-2 batch data")
    return tuple(artifacts)


def decode_any(envelope: bytes) -> tuple[Artifact, ...]:
    if envelope.startswith(PREAMBLE_V2):
        return decode_batch(envelope)
    return (decode(envelope),)


def artifact_from_path(
    path: Path,
    *,
    name: str | None = None,
    content_type: str | None = None,
    collection: str | None = None,
    metadata: object | None = None,
    parent: str | tuple[str, ...] | None = None,
    source_utxo: str | None = None,
    ownership_output: int = 0,
    format_version: int | None = None,
) -> Artifact:
    guessed_type, _ = mimetypes.guess_type(path.name)
    content = path.read_bytes()
    try:
        require_non_zip(content, content_type or guessed_type or "application/octet-stream", path.name)
    except ValueError as exc:
        raise EnvelopeError(str(exc)) from exc
    return create_artifact(
        content,
        content_type=content_type or guessed_type or "application/octet-stream",
        name=name or path.name,
        collection=collection,
        metadata=metadata,
        parent=parent,
        source_utxo=source_utxo,
        ownership_output=ownership_output,
        format_version=format_version,
    )
