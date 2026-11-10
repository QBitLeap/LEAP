"""Strict YAML manifests for Leap gallery roots and updates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .gallery import normalize_id


class GalleryYamlError(ValueError):
    pass


class _UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader: _UniqueLoader, node: Any, deep: bool = False) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise GalleryYamlError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


@dataclass(frozen=True)
class GalleryCreateManifest:
    name: str
    members: tuple[str, ...]
    destination: str | None = None
    ownership_bits: int = 4_000


@dataclass(frozen=True)
class GalleryUpdateManifest:
    gallery: str
    add: tuple[str, ...] = ()
    remove: tuple[str, ...] = ()


def _load(path: Path) -> dict[str, Any]:
    try:
        document = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise GalleryYamlError(f"cannot parse gallery YAML {path}: {exc}") from exc
    if not isinstance(document, dict) or any(not isinstance(key, str) for key in document):
        raise GalleryYamlError("gallery YAML must be a mapping with text keys")
    if document.get("version", 1) != 1:
        raise GalleryYamlError("gallery YAML version must be 1 when specified")
    return document


def _known(document: dict[str, Any], keys: set[str]) -> None:
    unknown = sorted(set(document) - keys)
    if unknown:
        raise GalleryYamlError(f"unknown gallery YAML field: {unknown[0]}")


def _ids(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise GalleryYamlError(f"{label} must be a YAML list")
    try:
        result = tuple(normalize_id(item) for item in value)
    except ValueError as exc:
        raise GalleryYamlError(f"invalid Quantum ID in {label}: {exc}") from exc
    if len(set(result)) != len(result):
        raise GalleryYamlError(f"{label} contains a duplicate Quantum ID")
    return result


def load_create(path: Path) -> GalleryCreateManifest:
    document = _load(path)
    _known(document, {"version", "name", "members", "destination", "ownership_bits"})
    name = document.get("name")
    if not isinstance(name, str) or not name:
        raise GalleryYamlError("gallery creation requires a name")
    if "members" not in document:
        raise GalleryYamlError("gallery creation requires a members list")
    members = _ids(document["members"], "members")
    destination = document.get("destination")
    if destination is not None and (not isinstance(destination, str) or not destination):
        raise GalleryYamlError("destination must be non-empty text")
    ownership_bits = document.get("ownership_bits", 4_000)
    if not isinstance(ownership_bits, int) or isinstance(ownership_bits, bool) or ownership_bits <= 0:
        raise GalleryYamlError("ownership_bits must be a positive integer")
    return GalleryCreateManifest(name, members, destination, ownership_bits)


def load_update(path: Path) -> GalleryUpdateManifest:
    document = _load(path)
    _known(document, {"version", "gallery", "add", "remove"})
    try:
        gallery = normalize_id(document.get("gallery"))
    except ValueError as exc:
        raise GalleryYamlError(f"gallery update requires a current gallery ID: {exc}") from exc
    additions = _ids(document.get("add", []), "add")
    removals = _ids(document.get("remove", []), "remove")
    if not additions and not removals:
        raise GalleryYamlError("gallery update must add or remove an ID")
    if set(additions) & set(removals):
        raise GalleryYamlError("gallery update cannot add and remove the same ID")
    return GalleryUpdateManifest(gallery, additions, removals)
