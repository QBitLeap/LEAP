import unittest
import hashlib

from indexer.discovery import discover_block
from quantums.envelope import Artifact, encode, encode_batch
from quantums.script import quantum_op_return


def h(number: int) -> str:
    return f"{number:064x}"


def p2mr(address: str = "qbit-address") -> dict:
    return {"value": 0.00004, "scriptPubKey": {"type": "witness_v2_p2mr", "address": address, "hex": "52"}}


def artifact(name: str, content: bytes, ownership_output: int, *, version: int = 1) -> Artifact:
    return Artifact("text/plain", name, content, hashlib.sha256(content).hexdigest(), ownership_output, format_version=version)


def data(payload: bytes) -> dict:
    return {"value": 0, "scriptPubKey": {"type": "nulldata", "hex": quantum_op_return(payload).hex()}}


class DiscoveryTests(unittest.TestCase):
    def test_numbers_canonical_v1_and_v2_in_chain_order(self):
        one = artifact("one.txt", b"one", 0)
        two = artifact("two.txt", b"two", 0, version=2)
        three = artifact("three.txt", b"three", 1, version=2)
        block = {"height": 7, "tx": [
            {"txid": h(1), "vout": [p2mr(), data(encode(one))]},
            {"txid": h(2), "vout": [p2mr("a"), p2mr("b"), data(encode_batch((two, three)))]},
        ]}
        found = discover_block(block)
        self.assertEqual([item.quantum_id for item in found], [f"{h(1)}:0", f"{h(2)}:0", f"{h(2)}:1"])
        self.assertEqual([(item.transaction_index, item.envelope_index) for item in found], [(0, 0), (1, 0), (1, 1)])

    def test_rejects_lookalike_without_p2mr_and_noncanonical_script_push(self):
        item = artifact("one.txt", b"one", 0)
        payload = encode(item)
        nonminimal = b"\x6a\x4d" + len(payload).to_bytes(2, "little") + payload
        block = {"height": 1, "tx": [
            {"txid": h(1), "vout": [{"value": 1, "scriptPubKey": {"type": "pubkeyhash"}}, data(payload)]},
            {"txid": h(2), "vout": [p2mr(), {"value": 0, "scriptPubKey": {"hex": nonminimal.hex()}}]},
        ]}
        self.assertEqual(discover_block(block), ())


if __name__ == "__main__":
    unittest.main()
