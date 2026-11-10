import hashlib
import json
import tempfile
import unittest
from threading import Event

from indexer.materializers import operational_modules
from indexer.unified import UnifiedIndex, follow
from quantums.envelope import Artifact, encode
from quantums.gallery import GalleryRoot, GalleryUpdate, encode as encode_gallery
from quantums.script import quantum_op_return


def h(number):
    return f"{number:064x}"


def output(value, script=b"", *, script_type="witness_v2_p2mr", address=None):
    info = {"hex": script.hex(), "type": script_type}
    if address:
        info["address"] = address
    return {"value_bits": value, "scriptPubKey": info}


def data(payload):
    return output(0, quantum_op_return(payload), script_type="nulldata")


def funded(txid, outputs, source):
    return {"txid": txid, "vin": [{"txid": source, "vout": 0, "value_bits": sum(item["value_bits"] for item in outputs)}], "vout": outputs}


def qpet(name):
    sprite = b"webp"
    metadata = json.dumps({
        "displayName": name, "format": "codex-pet-v1", "kind": "object",
        "sheet": {"contentType": "image/webp", "length": len(sprite),
                  "sha256": hashlib.sha256(sprite).hexdigest()},
    }, separators=(",", ":")).encode()
    return b"QPET\x01" + len(metadata).to_bytes(2, "little") + metadata + sprite


class MaterializerTests(unittest.TestCase):
    def block_zero(self):
        content = b"hello"
        quantum = Artifact("text/plain", "hello", content, hashlib.sha256(content).hexdigest())
        root_record = encode_gallery(GalleryRoot("Gallery", (f"{h(10)}:0",)))
        gallery = Artifact("application/x-leap-gallery", "gallery", root_record,
                           hashlib.sha256(root_record).hexdigest())
        return {
            "height": 0, "hash": h(100), "subsidy_bits": 50,
            "tx": [
                {"txid": h(1), "vin": [{"coinbase": "00"}], "vout": [output(50, address="miner")]},
                funded(h(10), [output(4, address="alice"), data(encode(quantum)), output(6)], h(90)),
                funded(h(11), [output(4, address="gallery-owner"), data(encode(gallery)), output(6)], h(91)),
                funded(h(12), [data(qpet("Qbit")), output(10, address="pet-owner")], h(92)),
            ],
        }

    def test_materializes_three_active_modules_and_disconnects(self):
        with tempfile.TemporaryDirectory() as directory:
            with UnifiedIndex(directory, operational_modules()) as index:
                index.connect_block(self.block_zero())
                self.assertEqual(index.tip, (0, h(100)))
                quantum = index.connection.execute("SELECT * FROM module_0.quantum_locations WHERE quantum_id=?", (f"{h(10)}:0",)).fetchone()
                self.assertEqual((quantum["current_txid"], quantum["current_vout"], quantum["current_offset"], quantum["current_address"]),
                                 (h(10), 0, 0, "alice"))
                gallery = index.connection.execute("SELECT r.name,s.* FROM module_1.gallery_roots r JOIN module_1.gallery_state s USING(root_id)").fetchone()
                self.assertEqual((gallery["name"], json.loads(gallery["members"])), ("Gallery", [f"{h(10)}:0"]))
                arbitrary = index.connection.execute("SELECT * FROM module_2.arbitraries").fetchone()
                arbitrary_location = index.connection.execute("SELECT * FROM module_2.arbitrary_locations").fetchone()
                self.assertEqual((arbitrary["number"], arbitrary["name"], arbitrary["binding_status"], arbitrary_location["current_vout"]),
                                 (1, "Qbit", "bound", 1))
                self.assertEqual(index.status(0).modules, {
                    "quantums": "ok", "galleries": "ok", "arbitraries": "ok",
                    "pebbles": "inactive", "pson": "inactive",
                })
                index.disconnect_tip()
                self.assertEqual(index.connection.execute("SELECT count(*) FROM module_0.quantums").fetchone()[0], 0)
                self.assertEqual(index.connection.execute("SELECT count(*) FROM module_1.gallery_roots").fetchone()[0], 0)
                self.assertEqual(index.connection.execute("SELECT count(*) FROM module_2.arbitraries").fetchone()[0], 0)

    def test_empty_block_records_no_row_mutations(self):
        with tempfile.TemporaryDirectory() as directory:
            block = {"height": 0, "hash": h(100), "subsidy_bits": 50,
                     "tx": [{"txid": h(1), "vin": [{"coinbase": "00"}], "vout": [output(50)]}]}
            with UnifiedIndex(directory, operational_modules()) as index:
                index.connect_block(block)
                self.assertEqual(index.tip, (0, h(100)))
                self.assertEqual(tuple(index.connection.execute(
                    "SELECT height,hash FROM blocks WHERE height=0"
                ).fetchone()), (0, h(100)))
                for ordinal in range(3):
                    self.assertEqual(index.connection.execute(
                        f"SELECT mutation_count FROM module_{ordinal}.block_undo WHERE height=0"
                    ).fetchone()[0], 0)
                    self.assertEqual(index.connection.execute(
                        f"SELECT count(*) FROM module_{ordinal}.undo_mutations WHERE height=0"
                    ).fetchone()[0], 0)
                index.disconnect_tip()
                self.assertEqual(index.tip, (-1, None))
                self.assertEqual(index.connection.execute("SELECT count(*) FROM blocks").fetchone()[0], 0)
                for ordinal in range(3):
                    self.assertEqual(index.connection.execute(
                        f"SELECT count(*) FROM module_{ordinal}.block_undo"
                    ).fetchone()[0], 0)

    def test_persists_and_moves_quantum_and_bound_qpet(self):
        with tempfile.TemporaryDirectory() as directory:
            index = UnifiedIndex(directory, operational_modules())
            index.connect_block(self.block_zero())
            index.connection.execute(
                "INSERT INTO module_2.arbitraries VALUES(2,'arbitrary:2',0,9,0,0,?,'other','Other',?,'unbound-no-adapter',NULL)",
                (h(88), hashlib.sha256(b"other").hexdigest()),
            )
            index.connection.commit()
            block_one = {
                "height": 1, "hash": h(101), "previousblockhash": h(100), "subsidy_bits": 50,
                "tx": [
                    {"txid": h(2), "vin": [{"coinbase": "00"}], "vout": [output(50)]},
                    {"txid": h(20), "vin": [{"txid": h(10), "vout": 0, "value_bits": 4}], "vout": [output(4, address="bob")]},
                    {"txid": h(21), "vin": [{"txid": h(12), "vout": 1, "value_bits": 10}], "vout": [output(10, address="pet-bob")]},
                    {"txid": h(22), "vin": [{"txid": h(20), "vout": 0, "value_bits": 4}], "vout": [output(4, address="carol")]},
                    {"txid": h(23), "vin": [{"txid": h(21), "vout": 0, "value_bits": 10}], "vout": [output(10, address="pet-carol")]},
                ],
            }
            index.connect_block(block_one)
            self.assertEqual(index.connection.execute("SELECT current_address FROM module_0.quantum_locations WHERE quantum_id=?", (f"{h(10)}:0",)).fetchone()[0], "carol")
            self.assertEqual(index.connection.execute("SELECT current_address FROM module_2.arbitrary_locations WHERE artifact_id='arbitrary:1'").fetchone()[0], "pet-carol")
            quantum_undo = index.connection.execute(
                "SELECT table_name,primary_key,previous_row FROM module_0.undo_mutations WHERE height=1"
            ).fetchall()
            self.assertEqual(len(quantum_undo), 1)
            self.assertEqual(quantum_undo[0]["table_name"], "quantum_locations")
            self.assertIn(f"{h(10)}:0", quantum_undo[0]["primary_key"])
            self.assertNotIn(f"{h(11)}:0", quantum_undo[0]["previous_row"])
            self.assertNotIn("content_sha256", quantum_undo[0]["previous_row"])
            arbitrary_undo = index.connection.execute(
                "SELECT table_name,previous_row FROM module_2.undo_mutations WHERE height=1"
            ).fetchall()
            self.assertEqual(len(arbitrary_undo), 1)
            self.assertEqual(arbitrary_undo[0]["table_name"], "arbitrary_locations")
            self.assertNotIn("payload_sha256", arbitrary_undo[0]["previous_row"])
            index.close()
            with UnifiedIndex(directory, operational_modules()) as resumed:
                self.assertEqual(resumed.tip, (1, h(101)))

    def test_underclaimed_fee_bit_keeps_identity_and_undo_restores_location(self):
        with tempfile.TemporaryDirectory() as directory:
            with UnifiedIndex(directory, operational_modules()) as index:
                index.connect_block(self.block_zero())
                quantum_id = f"{h(10)}:0"
                fee_block = {
                    "height": 1, "hash": h(101), "previousblockhash": h(100),
                    "subsidy_bits": 50,
                    "tx": [
                        {"txid": h(2), "vin": [{"coinbase": "00"}],
                         "vout": [output(50)]},
                        {"txid": h(20),
                         "vin": [{"txid": h(10), "vout": 0, "value_bits": 4}],
                         "vout": [output(0)]},
                    ],
                }
                index.connect_block(fee_block)
                self.assertIsNotNone(index.connection.execute(
                    "SELECT 1 FROM module_0.quantums WHERE quantum_id=?", (quantum_id,)).fetchone())
                self.assertIsNone(index.connection.execute(
                    "SELECT 1 FROM module_0.quantum_locations WHERE quantum_id=?", (quantum_id,)).fetchone())
                index.disconnect_tip()
                restored = index.connection.execute(
                    "SELECT current_txid,current_vout,current_address FROM module_0.quantum_locations WHERE quantum_id=?",
                    (quantum_id,),
                ).fetchone()
                self.assertEqual(tuple(restored), (h(10), 0, "alice"))

    def test_sparse_location_reads_and_arbitrary_fee_burn_are_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            with UnifiedIndex(directory, operational_modules()) as index:
                index.connect_block(self.block_zero())
                traced = []
                index.connection.set_trace_callback(traced.append)
                fee_block = {
                    "height": 1, "hash": h(101), "previousblockhash": h(100),
                    "subsidy_bits": 50,
                    "tx": [
                        {"txid": h(2), "vin": [{"coinbase": "00"}],
                         "vout": [output(50)]},
                        {"txid": h(20),
                         "vin": [{"txid": h(12), "vout": 1, "value_bits": 10}],
                         "vout": [output(0)]},
                    ],
                }
                index.connect_block(fee_block)
                index.connection.set_trace_callback(None)
                self.assertIsNone(index.connection.execute(
                    "SELECT 1 FROM module_2.arbitrary_locations "
                    "WHERE artifact_id='arbitrary:1'"
                ).fetchone())
                quantum = index.connection.execute(
                    "SELECT current_txid,current_vout FROM module_0.quantum_locations "
                    "WHERE quantum_id=?", (f"{h(10)}:0",),
                ).fetchone()
                self.assertEqual(tuple(quantum), (h(10), 0))
                location_reads = [
                    statement for statement in traced
                    if statement.startswith("SELECT * FROM module_0.quantum_locations ")
                    or statement.startswith("SELECT * FROM module_2.arbitrary_locations ")
                ]
                self.assertTrue(location_reads)
                self.assertTrue(all(" WHERE " in statement for statement in location_reads))
                index.disconnect_tip()
                restored = index.connection.execute(
                    "SELECT current_txid,current_vout,current_address "
                    "FROM module_2.arbitrary_locations WHERE artifact_id='arbitrary:1'"
                ).fetchone()
                self.assertEqual(tuple(restored), (h(12), 1, "pet-owner"))

    def test_gallery_successor_persists_membership(self):
        with tempfile.TemporaryDirectory() as directory:
            with UnifiedIndex(directory, operational_modules()) as index:
                index.connect_block(self.block_zero())
                index.connection.execute("INSERT INTO module_1.gallery_roots VALUES('unrelated-gallery','Other')")
                index.connection.execute("INSERT INTO module_1.gallery_state VALUES('unrelated-gallery','unrelated-gallery','[]')")
                index.connection.commit()
                update_record = encode_gallery(GalleryUpdate(f"{h(11)}:0", f"{h(11)}:0", (f"{h(99)}:0",), ()))
                artifact = Artifact("application/x-leap-gallery", "update", update_record,
                                    hashlib.sha256(update_record).hexdigest())
                block = {
                    "height": 1, "hash": h(101), "previousblockhash": h(100), "subsidy_bits": 50,
                    "tx": [
                        {"txid": h(2), "vin": [{"coinbase": "00"}], "vout": [output(50)]},
                        {"txid": h(30), "vin": [{"txid": h(11), "vout": 0, "value_bits": 4}],
                         "vout": [output(4, address="gallery-owner"), data(encode(artifact))]},
                    ],
                }
                index.connect_block(block)
                row = index.connection.execute("SELECT * FROM module_1.gallery_state").fetchone()
                self.assertEqual(row["current_id"], f"{h(30)}:0")
                self.assertEqual(json.loads(row["members"]), [f"{h(10)}:0", f"{h(99)}:0"])
                mutations = index.connection.execute(
                    "SELECT table_name,primary_key,previous_row FROM module_1.undo_mutations WHERE height=1 ORDER BY sequence"
                ).fetchall()
                self.assertEqual([item["table_name"] for item in mutations], ["gallery_state", "gallery_history"])
                self.assertNotIn("unrelated-gallery", "".join(item["previous_row"] or "" for item in mutations))
                self.assertIsNotNone(index.connection.execute(
                    "SELECT 1 FROM module_1.gallery_state WHERE root_id='unrelated-gallery'"
                ).fetchone())

    def test_rejects_incompatible_active_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            index = UnifiedIndex(directory, operational_modules())
            index.connection.execute("UPDATE module_0.schema_info SET version=99")
            index.connection.commit()
            index.close()
            with self.assertRaisesRegex(RuntimeError, "incompatible quantums database schema"):
                UnifiedIndex(directory, operational_modules())

    def test_rpc_follower_catches_up_and_stops_cleanly(self):
        block = self.block_zero()
        class Rpc:
            def call(self, method, *params):
                if method == "getblockchaininfo":
                    return {"blocks": 0}
                if method == "getblockhash":
                    return block["hash"]
                if method == "getblock":
                    self.verbosity = params[1]
                    return {key: value for key, value in block.items() if key != "subsidy_bits"}
                if method == "getblockstats":
                    return {"subsidy": block["subsidy_bits"]}
                raise AssertionError(method)
        with tempfile.TemporaryDirectory() as directory:
            stop = Event()
            rpc = Rpc()
            with UnifiedIndex(directory, operational_modules()) as index:
                follow(rpc, index, stop, poll_seconds=0.01, on_status=lambda status: stop.set())
                self.assertEqual(index.tip, (0, block["hash"]))
                self.assertEqual(rpc.verbosity, 2)

    def test_repeated_disconnect_and_alternate_multiblock_replay_is_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            with UnifiedIndex(directory, operational_modules()) as index:
                index.connect_block(self.block_zero())
                before = {
                    (schema, table): [tuple(row) for row in index.connection.execute(f"SELECT * FROM module_{schema}.{table} ORDER BY 1")]
                    for schema, table in ((0, "quantums"), (0, "quantum_locations"),
                                          (1, "gallery_roots"), (1, "gallery_state"),
                                          (2, "arbitraries"), (2, "arbitrary_locations"))
                }
                for height in (1, 2):
                    index.connect_block({
                        "height": height, "hash": h(100 + height), "previousblockhash": h(99 + height),
                        "subsidy_bits": 50,
                        "tx": [{"txid": h(height + 1), "vin": [{"coinbase": "00"}], "vout": [output(50)]}],
                    })
                index.disconnect_tip()
                index.disconnect_tip()
                after = {
                    (schema, table): [tuple(row) for row in index.connection.execute(f"SELECT * FROM module_{schema}.{table} ORDER BY 1")]
                    for schema, table in ((0, "quantums"), (0, "quantum_locations"),
                                          (1, "gallery_roots"), (1, "gallery_state"),
                                          (2, "arbitraries"), (2, "arbitrary_locations"))
                }
                self.assertEqual(after, before)
                replacement = {"height": 1, "hash": h(201), "previousblockhash": h(100), "subsidy_bits": 50,
                               "tx": [{"txid": h(202), "vin": [{"coinbase": "00"}], "vout": [output(50)]}]}
                index.connect_block(replacement)
                index.disconnect_tip()
                self.assertEqual(index.tip, (0, h(100)))


if __name__ == "__main__":
    unittest.main()
