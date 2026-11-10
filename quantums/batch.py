"""Strict Leap batch YAML parsing for independent Quantum transactions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover - exercised by packaging environments
    raise RuntimeError("batch support requires PyYAML; install leap-qbit with dependencies") from exc

from .envelope import Artifact, artifact_from_path, parent_ids, split_batch
from .transaction import DEFAULT_OWNERSHIP_BITS


class BatchError(ValueError):
    pass


class _UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader: _UniqueLoader, node: Any, deep: bool = False) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise BatchError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping
)


ROOT_KEYS = {"version", "mode", "collection", "defaults", "inscriptions"}
DEFAULT_KEYS = {"ownership_bits", "collection", "content_type", "destination"}
ENTRY_KEYS = DEFAULT_KEYS | {"file", "name", "metadata", "utxo"}
MODES = {"parentless", "parented", "pick-utxo"}
OUTPOINT_RE = __import__("re").compile(r"^[0-9a-fA-F]{64}:\d+$")


@dataclass(frozen=True)
class BatchItem:
    file: Path
    artifact: Artifact
    destination: str | None
    ownership_bits: int
    utxo: str | None = None


@dataclass(frozen=True)
class Batch:
    source: Path
    items: tuple[BatchItem, ...]
    mode: str = "parentless"
    parent: str | tuple[str, ...] | None = None


def split_batch_plan(batch: Batch, *, pick_remainders: tuple[bool, ...] | None = None) -> tuple[Batch, ...]:
    """Return the canonical ordered-greedy transaction chunks for a batch."""
    if batch.mode == "pick-utxo":
        if pick_remainders is None:
            pick_remainders = (True,) * len(batch.items)
        if len(pick_remainders) != len(batch.items):
            raise BatchError("pick-utxo remainder plan has the wrong length")

    def positions(start: int, stop: int) -> tuple[int, ...]:
        if batch.mode == "parented":
            parent_count = len(parent_ids(batch.parent))
            return tuple(range(parent_count, parent_count + stop - start))
        if batch.mode == "pick-utxo":
            assert pick_remainders is not None
            result, position = [], 0
            for has_remainder in pick_remainders[start:stop]:
                result.append(position)
                position += 1 + int(has_remainder)
            return tuple(result)
        return tuple(range(stop - start))

    encoded_chunks = split_batch(tuple(item.artifact for item in batch.items), positions)
    chunks: list[Batch] = []
    offset = 0
    for artifacts in encoded_chunks:
        items = tuple(
            replace(item, artifact=artifact)
            for item, artifact in zip(batch.items[offset:offset + len(artifacts)], artifacts)
        )
        chunks.append(Batch(batch.source, items, batch.mode, batch.parent))
        offset += len(artifacts)
    return tuple(chunks)


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise BatchError(f"{label} must be a YAML mapping with string keys")
    return value


def _known(mapping: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise BatchError(f"unknown {label} field: {unknown[0]}")


def _positive_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise BatchError(f"{label} must be a positive integer")
    return value


def load_batch(path: Path) -> Batch:
    try:
        document = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise BatchError(f"cannot parse batch file {path}: {exc}") from exc
    root = _object(document, "batch")
    _known(root, ROOT_KEYS, "batch")
    if root.get("version", 1) != 1:
        raise BatchError("batch version must be 1 when specified")
    mode = root.get("mode")
    if mode not in MODES:
        raise BatchError("batch mode must be parentless, parented, or pick-utxo")
    collection_name: str | None = None
    parent: str | tuple[str, ...] | None = None
    collection_config = root.get("collection")
    if collection_config is not None:
        config = _object(collection_config, "collection")
        _known(config, {"name", "parent"}, "collection")
        collection_name = config.get("name")
        parent = config.get("parent")
        if not isinstance(collection_name, str) or not collection_name:
            raise BatchError("collection requires a name")
        if isinstance(parent, list):
            if not parent or any(not isinstance(value, str) for value in parent):
                raise BatchError("collection parent list must contain Quantum IDs")
            parent = tuple(parent)
        if parent is not None:
            try:
                parents = parent_ids(parent)
            except Exception as exc:
                raise BatchError("collection parent must be a Quantum ID or list of Quantum IDs") from exc
            if len(set(parents)) != len(parents) or any(not OUTPOINT_RE.fullmatch(value) for value in parents):
                raise BatchError("collection parents must be unique Quantum IDs")
    if mode == "parented" and parent is None:
        raise BatchError("parented mode requires collection.parent")
    if mode != "parented" and parent is not None:
        raise BatchError("only parented mode accepts collection.parent")
    defaults = _object(root.get("defaults", {}), "defaults")
    _known(defaults, DEFAULT_KEYS, "defaults")
    entries = root.get("inscriptions")
    if not isinstance(entries, list) or not entries:
        raise BatchError("inscriptions must be a non-empty YAML list")
    items: list[BatchItem] = []
    for index, raw_entry in enumerate(entries):
        entry = _object(raw_entry, f"inscription {index}")
        _known(entry, ENTRY_KEYS, f"inscription {index}")
        merged = {**defaults, **entry}
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise BatchError(f"inscription {index} requires a non-blank name")
        if collection_name is not None and "collection" not in merged:
            merged["collection"] = collection_name
        file_value = merged.get("file")
        if not isinstance(file_value, str) or not file_value:
            raise BatchError(f"inscription {index} requires file")
        file = (path.parent / file_value).resolve()
        if not file.is_file():
            raise BatchError(f"inscription {index} file does not exist: {file}")
        ownership_bits = _positive_int(
            merged.get("ownership_bits", DEFAULT_OWNERSHIP_BITS),
            f"inscription {index} ownership_bits",
        )
        destination = merged.get("destination")
        if destination is not None and (not isinstance(destination, str) or not destination):
            raise BatchError(f"inscription {index} destination must be text")
        for key in ("name", "content_type", "collection"):
            value = merged.get(key)
            if value is not None and not isinstance(value, str):
                raise BatchError(f"inscription {index} {key} must be text")
        metadata = merged.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise BatchError(f"inscription {index} metadata must be a mapping")
        utxo = merged.get("utxo")
        if utxo is not None and (not isinstance(utxo, str) or not OUTPOINT_RE.fullmatch(utxo)):
            raise BatchError(f"inscription {index} utxo must be txid:vout")
        if mode == "pick-utxo" and utxo is None:
            raise BatchError(f"pick-utxo inscription {index} requires utxo")
        if mode != "pick-utxo" and utxo is not None:
            raise BatchError("utxo is only valid in pick-utxo mode")
        artifact = artifact_from_path(
            file,
            name=merged.get("name"),
            content_type=merged.get("content_type"),
            collection=merged.get("collection"),
            metadata=metadata,
            parent=parent,
            source_utxo=utxo,
            format_version=2,
        )
        items.append(BatchItem(file, artifact, destination, ownership_bits, utxo))
    return Batch(path, tuple(items), mode, parent)
