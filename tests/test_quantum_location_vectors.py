"""Pinned Quantum movement cases shared with the Core transition model."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from scripts.generate_quantum_location_vectors import document
from indexer.quantum import Location, OutPoint, QuantumIndexer


FIXTURE = Path(__file__).parent / "data/quantum_location_v2.json"


class QuantumLocationVectorTests(unittest.TestCase):
    def test_indexer_cases_are_reproducible(self) -> None:
        pinned = json.loads(FIXTURE.read_text())
        self.assertEqual(pinned, document())
        cases = {case["id"]: case for case in pinned["valid"]}
        self.assertEqual(len(cases), 7)
        self.assertEqual(cases["fee-to-coinbase"]["after"][0]["current"]["txid"], f"{3:064x}")
        self.assertEqual(cases["same-block-create-spend"]["changes"][0]["before"], None)
        self.assertEqual(cases["same-block-create-spend"]["after"][0]["current"]["txid"], f"{11:064x}")
        self.assertTrue(cases["underclaimed-fee-burn"]["after"][0]["burned"])
        self.assertEqual(cases["underclaimed-fee-burn"]["changes"][0]["before"]["offset"], 9)

    def test_distinct_quantums_can_share_one_bit(self) -> None:
        index = QuantumIndexer()
        point = OutPoint("c" * 64, 3)
        index.add("b:2", Location(point, 7))
        index.add("a:0", Location(point, 7))
        self.assertEqual(index.at_outpoint(point), ("a:0", "b:2"))


if __name__ == "__main__":
    unittest.main()
