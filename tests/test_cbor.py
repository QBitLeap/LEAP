from __future__ import annotations

import unittest

from quantums import cbor


class CanonicalCborTests(unittest.TestCase):
    def test_supported_metadata_round_trip_is_stable(self):
        value = {"Sequence": 1, "Traits": ["Leap", True, None], "Nested": {"Count": -2}}
        raw = cbor.encode(value)
        self.assertEqual(cbor.decode(raw), value)
        self.assertEqual(cbor.encode(cbor.decode(raw)), raw)

    def test_map_order_and_nonminimal_integer_are_rejected(self):
        with self.assertRaises(cbor.CborError):
            cbor.decode(b"\x18\x01")
        with self.assertRaises(cbor.CborError):
            cbor.decode(b"\xa2\x62aa\x01\x61b\x02")

    def test_floats_bytes_and_non_text_keys_are_rejected(self):
        for value in (1.5, b"x", {1: "x"}):
            with self.assertRaises(cbor.CborError):
                cbor.encode(value)

    def test_deep_metadata_is_rejected_before_host_recursion_limit(self):
        value: object = None
        for _ in range(cbor.MAX_DEPTH + 2):
            value = [value]
        with self.assertRaises(cbor.CborError):
            cbor.encode(value)
        raw = b"\x81" * (cbor.MAX_DEPTH + 2) + b"\xf6"
        with self.assertRaises(cbor.CborError):
            cbor.decode(raw)


if __name__ == "__main__":
    unittest.main()
