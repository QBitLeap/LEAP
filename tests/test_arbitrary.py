import unittest
import hashlib
import json

from indexer.arbitrary import (
    ArtifactPosition, Arbitrary, bind_qpet_v1, number_arbitraries,
    recognize_qpet_v1, register_qpet_v1,
)
from indexer.quantum import Block, OutPoint, QuantumIndexer, Transaction, TxInput, TxOutput


class ArbitraryTests(unittest.TestCase):
    @staticmethod
    def qpet(name="Qbit"):
        sprite = b"webp"
        metadata = json.dumps({
            "displayName": name,
            "format": "codex-pet-v1",
            "kind": "object",
            "sheet": {"contentType": "image/webp", "length": len(sprite),
                      "sha256": hashlib.sha256(sprite).hexdigest()},
        }, separators=(",", ":")).encode()
        return b"QPET\x01" + len(metadata).to_bytes(2, "little") + metadata + sprite

    def test_numbers_by_block_transaction_output_and_record(self):
        late = Arbitrary(ArtifactPosition(2, 0, 0), "b", "example", b"late", "Miner One")
        second = Arbitrary(ArtifactPosition(1, 1, 0), "a", "example", b"second")
        first = Arbitrary(ArtifactPosition(1, 0, 3), "c", "example", b"first", "Qbit")
        numbered = number_arbitraries((late, second, first))
        self.assertEqual([(number, item.payload) for number, item in numbered],
                         [(1, b"first"), (2, b"second"), (3, b"late")])
        self.assertIsNone(numbered[1][1].name)

    def test_qpet_v1_binds_vout_one_offset_zero_and_subsequently_moves(self):
        txid = "01" * 32
        payload = self.qpet()
        item = recognize_qpet_v1(payload, position=ArtifactPosition(1, 0, 0), txid=txid)
        self.assertIsNotNone(item)
        assert item is not None
        creation = Transaction(txid, (), (TxOutput(0, b"\x6a"), TxOutput(10, address="alice")))
        tracker = QuantumIndexer()
        bound = register_qpet_v1(item, creation, tracker, artifact_id="arbitrary:1")
        self.assertEqual((bound.bound_output, bound.binding_status), (1, "bound"))
        self.assertEqual(tracker.location("arbitrary:1").offset, 0)
        spend = Transaction("02" * 32,
                            (TxInput(OutPoint("09" * 32, 0), 3), TxInput(OutPoint(txid, 1), 10)),
                            (TxOutput(3), TxOutput(10, address="bob")))
        tracker.connect(Block(0, "03" * 32, None, 50,
                              Transaction("04" * 32, (), (TxOutput(50),)), (spend,)))
        self.assertEqual(tracker.location("arbitrary:1").outpoint, OutPoint("02" * 32, 1))
        self.assertEqual(tracker.location("arbitrary:1").offset, 0)
        self.assertEqual(tracker.at_address("bob"), ("arbitrary:1",))

    def test_qpet_invalid_binding_status_never_fabricates_ownership(self):
        txid = "01" * 32
        payload = self.qpet()
        item = recognize_qpet_v1(payload, position=ArtifactPosition(1, 0, 0), txid=txid)
        assert item is not None
        cases = (
            (Transaction(txid, (), (TxOutput(0, b"\x6a"),)), "invalid-binding-missing-vout-1"),
            (Transaction(txid, (), (TxOutput(0), TxOutput(0))), "invalid-binding-zero-value-vout-1"),
            (Transaction(txid, (), (TxOutput(0), TxOutput(1, b"\x6a"))), "invalid-binding-unspendable-vout-1"),
        )
        for transaction, status in cases:
            with self.subTest(status=status):
                result = bind_qpet_v1(item, transaction)
                self.assertEqual(result.binding_status, status)
                self.assertIsNone(result.bound_output)

    def test_format_without_adapter_stays_unbound(self):
        item = Arbitrary(ArtifactPosition(1, 0, 0), "01" * 32, "other", b"payload")
        self.assertEqual(item.binding_status, "unbound-no-adapter")
        self.assertIsNone(item.bound_output)
