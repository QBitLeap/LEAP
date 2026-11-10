"""Local creation policy, independent of canonical decoding and chain validity.

Inspect ZIP headers/directory records only; never extract or decompress archives.
"""
from io import BytesIO
from pathlib import PurePath
from zipfile import is_zipfile


ZIP_TYPES = frozenset({
    "application/zip", "application/x-zip", "application/x-zip-compressed",
    "application/x-compressed-zip", "multipart/x-zip",
})
ZIP_HEADERS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


def is_zip_content(content: bytes) -> bool:
    # is_zipfile checks bounded end records without enumerating archive members.
    # It also recognizes prefixed/self-extracting and image/ZIP polyglot files.
    return content.startswith(ZIP_HEADERS) or is_zipfile(BytesIO(content))


def require_non_zip(content: bytes, content_type: str, file_name: str | None = None) -> None:
    mime = content_type.lower().split(";", 1)[0].strip()
    suffix = PurePath(file_name or "").suffix.lower()
    if mime in ZIP_TYPES or suffix in {".zip", ".zipx"} or is_zip_content(content):
        raise ValueError("ZIP files and ZIP-based containers are not accepted for new inscriptions.")
