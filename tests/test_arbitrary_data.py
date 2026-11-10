import hashlib
import unittest

from indexer.arbitrary_data import inventory_transaction
from quantums.envelope import Artifact, encode
from quantums.script import quantum_op_return


class ArbitraryDataTests(unittest.TestCase):
    def test_inventories_canonical_noncanonical_and_multi_push_data(self):
        plain = quantum_op_return(b"Qpet")
        nonminimal = b"\x6a\x4c\x04Qpet"
        multiple = b"\x6a\x02Qp\x02et"
        tx = {"vout": [
            {"n": 1, "value": 0, "scriptPubKey": {"hex": plain.hex()}},
            {"n": 2, "value": 0, "scriptPubKey": {"hex": nonminimal.hex()}},
            {"n": 3, "value": 0, "scriptPubKey": {"hex": multiple.hex()}},
        ]}
        found = inventory_transaction(tx)
        self.assertEqual([item.payload for item in found], [b"Qpet", b"Qpet", b"Qpet"])
        self.assertEqual([item.push_encoding for item in found], ["single-push", "single-push", "multi-push"])
        self.assertEqual([item.canonical_single_push for item in found], [True, False, False])

    def test_identifies_valid_leap_payload_without_assigning_status(self):
        content = b"hello"
        artifact = Artifact("text/plain", "hello.txt", content, hashlib.sha256(content).hexdigest())
        found = inventory_transaction({"vout": [{"value": 0, "scriptPubKey": {"hex": quantum_op_return(encode(artifact)).hex()}}]})
        self.assertEqual(found[0].leap_items, 1)
        self.assertEqual(found[0].media_hint, "text/plain")


if __name__ == "__main__":
    unittest.main()
