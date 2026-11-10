from __future__ import annotations

import unittest

from quantums.envelope import EnvelopeError
from quantums.gallery import GalleryRoot, GalleryUpdate, apply, decode, encode


A = "a" * 64 + ":0"
B = "a" * 64 + ":1"
C = "b" * 64 + ":300"
D = "c" * 64 + ":0"


class GalleryTests(unittest.TestCase):
    def test_root_round_trip_packs_repeated_transaction_ids(self):
        record = GalleryRoot("Leap Gallery", (A, B, C))
        raw = encode(record)
        self.assertEqual(decode(raw), record)
        # Two adjacent IDs share txid a; its 32 bytes occur only once.
        self.assertEqual(raw.count(bytes.fromhex("a" * 64)), 1)

    def test_update_adds_and_removes_with_immutable_history(self):
        update = GalleryUpdate(A, D, (C,), (B,))
        self.assertEqual(decode(encode(update)), update)
        self.assertEqual(apply((A, B), update), (A, C))

    def test_invalid_deltas_are_rejected(self):
        with self.assertRaises(EnvelopeError):
            encode(GalleryUpdate(A, A))
        with self.assertRaises(EnvelopeError):
            encode(GalleryUpdate(A, A, (B,), (B,)))
        with self.assertRaises(EnvelopeError):
            apply((A, B), GalleryUpdate(A, A, (B,), ()))
        with self.assertRaises(EnvelopeError):
            apply((A, B), GalleryUpdate(A, A, (), (C,)))

    def test_trailing_and_noncanonical_bytes_fail(self):
        raw = encode(GalleryRoot("G", (A,)))
        with self.assertRaises(EnvelopeError):
            decode(raw + b"\x00")


if __name__ == "__main__":
    unittest.main()
