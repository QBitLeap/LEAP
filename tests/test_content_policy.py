from dataclasses import replace
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from zipfile import ZipFile

from quantums.envelope import (Artifact, EnvelopeError, artifact_from_path,
    create_artifact, decode_any, encode, encode_batch)
from quantums.transaction import prepare_batch, prepare_gallery_successor, prepare_inscription


def archive():
    stream = BytesIO()
    with ZipFile(stream, "w") as zipped:
        zipped.writestr("example.txt", "local policy fixture, never inscribed")
    return stream.getvalue()


class ContentPolicyTests(unittest.TestCase):
    def historical(self, body=None):
        body = archive() if body is None else body
        return Artifact("application/zip", "Historical archive", body, sha256(body).hexdigest())

    def test_creation_rejects_zip_headers_mime_and_renamed_containers(self):
        for body, mime in [(archive(), "image/png"), (b"prefix" + archive(), "application/octet-stream"),
                           (archive() + b"trailing", "text/plain"), (b"PK\x03\x04truncated", "text/plain"),
                           (b"PK\x05\x06" + bytes(18), "application/octet-stream"),
                           (b"x", "Application/X-Zip-Compressed")]:
            with self.subTest(mime=mime, body=body[:12]):
                with self.assertRaisesRegex(EnvelopeError, "ZIP"):
                    create_artifact(body, content_type=mime, name="Renamed Quantum")

    def test_filename_cannot_bypass_policy_with_mime_and_name_overrides(self):
        with TemporaryDirectory() as directory:
            for filename, body in [("collection.ZIP", b"not even a valid archive"), ("image.png", archive())]:
                path = Path(directory) / filename
                path.write_bytes(body)
                with self.assertRaisesRegex(EnvelopeError, "ZIP"):
                    artifact_from_path(path, name="A different name", content_type="text/plain")

    def test_historical_v1_v2_and_recovery_stay_canonical(self):
        old = self.historical()
        v2 = replace(old, format_version=2, metadata={"legacy": True})
        for original, raw in [(old, encode(old)), (v2, encode_batch((v2,)))]:
            self.assertEqual(decode_any(raw), (original,))
            recovered = decode_any(raw)[0]
            self.assertEqual(encode(recovered) if recovered.format_version == 1 else encode_batch((recovered,)), raw)

    def test_manual_artifacts_rejected_before_any_rpc_or_signing(self):
        class NoRpc:
            def call(self, *args):
                raise AssertionError("ZIP policy must run before RPC")
        rpc = NoRpc()
        item = self.historical()
        for action in [lambda: prepare_inscription(rpc, item, fee_rate="1"),
                       lambda: prepare_batch(rpc, SimpleNamespace(items=[SimpleNamespace(artifact=item)])),
                       lambda: prepare_gallery_successor(rpc, item, current="0" * 64 + ":0", fee_rate="1")]:
            with self.assertRaisesRegex(EnvelopeError, "ZIP"):
                action()

    def test_non_zip_content_is_unchanged_and_not_decompressed(self):
        for body, mime in [(b"ordinary text mentioning PK", "text/plain"),
                           (b'{"layers":[]}', "application/json"),
                           (b"<html>recursive layers</html>", "text/html"),
                           (bytes(range(256)), "application/octet-stream")]:
            item = create_artifact(body, content_type=mime, name="Allowed")
            self.assertEqual(decode_any(encode(item))[0].content, body)
