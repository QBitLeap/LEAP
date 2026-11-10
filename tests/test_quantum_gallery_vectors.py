"""Shared canonical Gallery records for Leap and Qbit Core."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from quantums.envelope import EnvelopeError
from quantums.gallery import GalleryRoot, GalleryUpdate, apply, decode, encode
from scripts.generate_quantum_gallery_vectors import document


FIXTURE = Path(__file__).parent / "data/quantum_gallery_v1.json"


class QuantumGalleryVectorTests(unittest.TestCase):
    def test_shared_gallery_records(self) -> None:
        data = json.loads(FIXTURE.read_text())
        self.assertEqual(data, document())
        root = decode(bytes.fromhex(data["root"]["raw"]))
        update = decode(bytes.fromhex(data["update"]["raw"]))
        self.assertIsInstance(root, GalleryRoot)
        self.assertIsInstance(update, GalleryUpdate)
        self.assertEqual(encode(root).hex(), data["root"]["raw"])
        self.assertEqual(encode(update).hex(), data["update"]["raw"])
        self.assertEqual(apply(tuple(data["update"]["before"]), update),
                         tuple(data["update"]["after"]))
        for invalid in data["invalid"]:
            with self.subTest(id=invalid["id"]), self.assertRaises(EnvelopeError):
                decode(bytes.fromhex(invalid["raw"]))


if __name__ == "__main__":
    unittest.main()
