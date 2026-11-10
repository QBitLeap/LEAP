"""Pinned v1/v2 Quantum creation cases for the Core discovery port."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from scripts.generate_quantum_discovery_vectors import document


FIXTURE = Path(__file__).parent / "data/quantum_discovery_v1.json"


class QuantumDiscoveryVectorTests(unittest.TestCase):
    def test_canonical_creation_and_rejections(self) -> None:
        pinned = json.loads(FIXTURE.read_text())
        self.assertEqual(pinned, document())
        self.assertEqual(len(pinned["valid"]), 3)
        self.assertEqual(len(pinned["invalid"]), 6)
        self.assertEqual([len(case["expected"]) for case in pinned["valid"]], [1, 2, 2])
        self.assertTrue(all(not case["expected"] for case in pinned["invalid"]))


if __name__ == "__main__":
    unittest.main()
