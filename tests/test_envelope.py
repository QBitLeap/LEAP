from __future__ import annotations

import hashlib
import unittest

from quantums.envelope import EnvelopeError, create_artifact, decode, decode_batch, encode, encode_batch, split_batch
from quantums.script import extract_single_push
from quantums.transaction import DEFAULT_OWNERSHIP_BITS, _fee_rate, bits_to_qbt


class EnvelopeTests(unittest.TestCase):
    def artifact(self):
        return create_artifact(
            b"hello qbit",
            content_type="text/plain",
            name="hello.txt",
            collection="examples",
        )

    def test_round_trip_is_canonical(self):
        artifact = self.artifact()
        envelope = encode(artifact)
        self.assertEqual(decode(envelope), artifact)
        self.assertEqual(encode(decode(envelope)), envelope)

    def test_hash_and_length_are_verified(self):
        envelope = encode(self.artifact())
        with self.assertRaises(EnvelopeError):
            decode(envelope + b"!")
        changed = envelope.replace(hashlib.sha256(b"hello qbit").hexdigest().encode(), b"0" * 64)
        with self.assertRaises(EnvelopeError):
            decode(changed)

    def test_unknown_and_reordered_headers_are_rejected(self):
        envelope = encode(self.artifact())
        changed = envelope.replace(b"name:hello.txt\n", b"unknown:value\nname:hello.txt\n")
        with self.assertRaises(EnvelopeError):
            decode(changed)

    def test_newlines_in_metadata_are_rejected(self):
        with self.assertRaises(EnvelopeError):
            create_artifact(b"x", content_type="text/plain", name="bad\nname")

    def test_extracts_large_op_return_push(self):
        payload = encode(self.artifact())
        script = b"\x6a\x4d" + len(payload).to_bytes(2, "little") + payload
        self.assertEqual(extract_single_push(script), payload)

    def test_qbt_conversion_is_exact(self):
        self.assertEqual(bits_to_qbt(10_000), "0.0001")

    def test_default_ownership_value_is_four_thousand_bits(self):
        self.assertEqual(DEFAULT_OWNERSHIP_BITS, 4_000)

    def test_decimal_fee_rate_is_preserved_exactly(self):
        self.assertEqual(_fee_rate("0.25"), "0.25")
        self.assertEqual(_fee_rate("1.125"), "1.125")
        for invalid in ("0", "-0.1", "NaN", "Infinity"):
            with self.assertRaises(ValueError):
                _fee_rate(invalid)

    def test_version_two_batch_metadata_round_trip(self):
        first = create_artifact(b"one", content_type="text/plain", name="one", metadata={"Sequence": 1}, ownership_output=0)
        second = create_artifact(b"two", content_type="text/plain", name="two", metadata={"Tags": ["Leap", True]}, ownership_output=1)
        encoded = encode_batch((first, second))
        self.assertEqual(decode_batch(encoded), (first, second))
        self.assertEqual(encode_batch(decode_batch(encoded)), encoded)

    def test_version_two_quantum_preserves_multiple_ordered_parents(self):
        parents = ("1" * 64 + ":0", "2" * 64 + ":3")
        artifact = create_artifact(
            b"child", content_type="text/plain", name="child", parent=parents,
            ownership_output=2, format_version=2,
        )
        encoded = encode_batch((artifact,))
        self.assertEqual(encoded.count(b"parent:"), 2)
        self.assertEqual(decode_batch(encoded)[0].parent, parents)
        with self.assertRaisesRegex(EnvelopeError, "duplicate parent"):
            create_artifact(
                b"child", content_type="text/plain", name="child",
                parent=(parents[0], parents[0]), format_version=2,
            )
        with self.assertRaisesRegex(EnvelopeError, "invalid parent"):
            create_artifact(
                b"child", content_type="text/plain", name="child",
                parent="1" * 64 + ":\u0661", format_version=2,
            )

    def test_batch_splitting_is_ordered_greedy_and_counts_encoded_metadata(self):
        items = tuple(
            create_artifact(
                b"x" * 62_000,
                content_type="text/plain",
                name=f"item-{index}.txt",
                metadata={"sequence": index, "description": "m" * 1_000},
            )
            for index in range(4)
        )
        chunks = split_batch(items, lambda start, stop: tuple(range(stop - start)))
        self.assertEqual(tuple(item.name for chunk in chunks for item in chunk), tuple(item.name for item in items))
        self.assertEqual(tuple(len(chunk) for chunk in chunks), (3, 1))
        self.assertTrue(all(len(encode_batch(chunk)) <= 190_000 for chunk in chunks))

    def test_single_quantum_larger_than_carrier_cannot_be_split(self):
        item = create_artifact(
            b"x" * 190_000,
            content_type="application/octet-stream",
            name="large.bin",
            format_version=2,
        )
        with self.assertRaisesRegex(EnvelopeError, "batch index 0"):
            split_batch((item,), lambda start, stop: (0,))

    def test_version_one_rejects_version_two_features(self):
        artifact = create_artifact(b"x", content_type="text/plain", name="x", metadata={"x": 1})
        with self.assertRaises(EnvelopeError):
            encode(artifact)


if __name__ == "__main__":
    unittest.main()
