import unittest

from indexer.quantum import Block, Location, OutPoint, QuantumIndexError, QuantumIndexer, Transaction, TxInput, TxOutput


def h(number: int) -> str:
    return f"{number:064x}"


class QuantumIndexerTests(unittest.TestCase):
    def test_tracks_only_registered_quantum_through_merge_and_split(self):
        index = QuantumIndexer()
        index.add("quantum", Location(OutPoint(h(1), 0), 0))
        tx = Transaction(h(2), (TxInput(OutPoint(h(9), 1), 7), TxInput(OutPoint(h(1), 0), 10)), (TxOutput(5), TxOutput(8), TxOutput(3)))
        block = Block(0, h(100), None, 50, Transaction(h(3), (), (TxOutput(51),)), (tx,))
        index.connect(block)
        self.assertEqual(index.location("quantum"), Location(OutPoint(h(2), 1), 2))

    def test_multi_input_output_move_updates_address_and_gallery_presentation(self):
        index = QuantumIndexer()
        index.add("member-a", Location(OutPoint(h(1), 0), 2), address="old")
        index.add("member-b", Location(OutPoint(h(2), 0), 1), address="old")
        tx = Transaction(
            h(3),
            (TxInput(OutPoint(h(9), 0), 3), TxInput(OutPoint(h(1), 0), 5), TxInput(OutPoint(h(2), 0), 4)),
            (TxOutput(4, address="alice"), TxOutput(8, address="bob")),
        )
        index.connect(Block(0, h(100), None, 50, Transaction(h(4), (), (TxOutput(50),)), (tx,)))
        self.assertEqual(index.at_address("bob"), ("member-a", "member-b"))
        self.assertEqual(index.address_groups("bob", {"member-a": "gallery", "member-b": "gallery"}),
                         (("gallery", ("member-a", "member-b")),))

    def test_disconnect_and_alternate_chain_replay_restore_current_address(self):
        index = QuantumIndexer()
        original = Location(OutPoint(h(1), 0), 0)
        index.add("quantum", original, address="alice")
        tx = Transaction(h(2), (TxInput(original.outpoint, 1),), (TxOutput(1, address="bob"),))
        block = Block(0, h(100), None, 50, Transaction(h(3), (), (TxOutput(50),)), (tx,))
        index.connect(block)
        self.assertEqual(index.at_address("bob"), ("quantum",))
        index.disconnect()
        self.assertEqual(index.at_address("alice"), ("quantum",))
        replacement_tx = Transaction(h(5), (TxInput(original.outpoint, 1),), (TxOutput(1, address="carol"),))
        replacement = Block(0, h(101), None, 50, Transaction(h(6), (), (TxOutput(50),)), (replacement_tx,))
        index.connect(replacement)
        self.assertEqual(index.at_address("carol"), ("quantum",))

    def test_fee_bit_moves_to_coinbase_and_keeps_tracking(self):
        index = QuantumIndexer()
        index.add("quantum", Location(OutPoint(h(1), 0), 9))
        fee_tx = Transaction(h(2), (TxInput(OutPoint(h(1), 0), 10),), (TxOutput(8),))
        coinbase = Transaction(h(3), (), (TxOutput(51), TxOutput(1)))
        index.connect(Block(0, h(100), None, 50, coinbase, (fee_tx,)))
        self.assertEqual(index.location("quantum"), Location(OutPoint(h(3), 1), 0))
        spend = Transaction(h(4), (TxInput(OutPoint(h(3), 1), 1),), (TxOutput(1),))
        index.connect(Block(1, h(101), h(100), 50, Transaction(h(5), (), (TxOutput(50),)), (spend,)))
        self.assertEqual(index.location("quantum"), Location(OutPoint(h(4), 0), 0))

    def test_fee_order_uses_block_transaction_order(self):
        index = QuantumIndexer()
        index.add("first", Location(OutPoint(h(1), 0), 4))
        index.add("second", Location(OutPoint(h(2), 0), 4))
        first = Transaction(h(10), (TxInput(OutPoint(h(1), 0), 5),), (TxOutput(4),))
        second = Transaction(h(11), (TxInput(OutPoint(h(2), 0), 5),), (TxOutput(4),))
        coinbase = Transaction(h(12), (), (TxOutput(100), TxOutput(1), TxOutput(1)))
        index.connect(Block(0, h(100), None, 100, coinbase, (first, second)))
        self.assertEqual(index.location("first"), Location(OutPoint(h(12), 1), 0))
        self.assertEqual(index.location("second"), Location(OutPoint(h(12), 2), 0))

    def test_underclaimed_coinbase_burns_fee_bit_and_disconnect_restores_it(self):
        index = QuantumIndexer()
        original = Location(OutPoint(h(1), 0), 9)
        index.add("quantum", original, address="alice")
        fee_tx = Transaction(h(2), (TxInput(original.outpoint, 10),), (TxOutput(8),))
        block = Block(0, h(100), None, 50, Transaction(h(3), (), (TxOutput(51),)), (fee_tx,))
        index.connect(block)
        self.assertTrue(index.is_burned("quantum"))
        self.assertIsNone(index.location("quantum"))
        self.assertEqual(index.at_address("alice"), ())
        with self.assertRaisesRegex(QuantumIndexError, "already indexed"):
            index.add("quantum", original)
        index.disconnect()
        self.assertFalse(index.is_burned("quantum"))
        self.assertEqual(index.location("quantum"), original)
        self.assertEqual(index.at_address("alice"), ("quantum",))

    def test_multiple_quantums_in_one_output_remain_independently_trackable(self):
        index = QuantumIndexer()
        shared = OutPoint(h(1), 0)
        index.add("first", Location(shared, 2))
        index.add("second", Location(shared, 7))
        tx = Transaction(h(2), (TxInput(shared, 10),), (TxOutput(5), TxOutput(5)))
        index.connect(Block(0, h(100), None, 50, Transaction(h(3), (), (TxOutput(50),)), (tx,)))
        self.assertEqual(index.location("first"), Location(OutPoint(h(2), 0), 2))
        self.assertEqual(index.location("second"), Location(OutPoint(h(2), 1), 2))

    def test_block_failure_is_atomic_and_disconnect_restores_location(self):
        index = QuantumIndexer()
        original = Location(OutPoint(h(1), 0), 0)
        index.add("quantum", original)
        tx = Transaction(h(2), (TxInput(original.outpoint, 1),), (TxOutput(1),))
        index.connect(Block(0, h(100), None, 50, Transaction(h(3), (), (TxOutput(50),)), (tx,)))
        index.disconnect()
        self.assertEqual(index.location("quantum"), original)
        bad = Block(0, h(101), None, 50, Transaction(h(4), (), (TxOutput(51),)), ())
        with self.assertRaisesRegex(QuantumIndexError, "coinbase outputs exceed"):
            index.connect(bad)
        self.assertEqual(index.location("quantum"), original)
        self.assertEqual(index.tip_height, -1)


if __name__ == "__main__":
    unittest.main()
