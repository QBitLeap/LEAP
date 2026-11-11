from contextlib import closing
import hashlib
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from indexer.materializers import operational_modules
from indexer.relationships import QuantumRelationshipQuery, RelationshipError
from indexer.unified import UnifiedIndex
from quantums.cli import parser
from quantums.envelope import create_artifact, encode, encode_batch
from quantums.rpc import RpcError
from quantums.script import quantum_op_return


def h(number):
    return f"{number:064x}"


SCRIPT = bytes.fromhex("5220" + "11" * 32)
OTHER_SCRIPT = bytes.fromhex("5220" + "22" * 32)


def output(value, script=SCRIPT, kind="witness_v2_p2mr"):
    return {"value_bits": value, "scriptPubKey": {"hex": script.hex(), "type": kind}}


def quantum(number, name, *, parents=None, owner=0, collection=None, metadata=None):
    artifact = create_artifact(name.encode(), name=name, content_type="text/plain", parent=parents,
                               ownership_output=owner, collection=collection, metadata=metadata)
    envelope = encode(artifact) if artifact.format_version == 1 else encode_batch((artifact,))
    outputs = [output(4000) for _ in range(owner + 1)]
    outputs.append(output(0, quantum_op_return(envelope), "nulldata"))
    return {"txid": h(number), "vin": [{"txid": h(900 + number), "vout": 0, "value_bits": 4000 * (owner + 1)}], "vout": outputs}


class Chain:
    """A real LEAP materialized toy chain and a read-only fake Core transport."""
    def __init__(self, directory, network="testnet4"):
        self.network, self.blocks, self.transactions, self.calls = network, {}, {}, []
        self.index = UnifiedIndex(directory, operational_modules(network, pebble_profile="off"))
        self.index.bind_network(network, "off")
        self.parent = f"{h(200)}:0"; self.other = f"{h(100)}:0"
        self.add([quantum(200, "Parent", collection="Canonical collection"), quantum(100, "Unrelated", collection="Canonical collection"),
                  quantum(300, "Metadata claim", metadata={"parent": self.parent})])

    def close(self):
        self.index.close()

    def add(self, transactions):
        height = len(self.blocks)
        fees = sum(sum(vin["value_bits"] for vin in tx["vin"]) - sum(vout["value_bits"] for vout in tx["vout"]) for tx in transactions)
        coinbase = {"txid": h(5000 + height), "vin": [{"coinbase": "00"}], "vout": [output(50 + fees)]}
        block = {"height": height, "hash": h(1000 + height), "previousblockhash": h(999 + height) if height else None,
                 "subsidy_bits": 50, "tx": [coinbase, *transactions]}
        for tx in block["tx"]:
            tx["blockhash"] = block["hash"]; self.transactions[tx["txid"]] = tx
        self.index.connect_block(block); self.blocks[height] = block
        return block

    def child(self, number, header, current, *, owner=1):
        tx = quantum(number, f"Child {number}", parents=header, owner=owner)
        parents = current if isinstance(current, tuple) else (current,)
        tx["vin"] = [{"txid": point.split(":")[0], "vout": int(point.split(":")[1]), "value_bits": 4000} for point in parents]
        tx["vin"].append({"txid": h(number + 900), "vout": 0, "value_bits": 4000})
        return tx

    def family(self):
        self.add([self.child(400, self.parent, self.parent)])
        self.add([self.child(500, f"{h(400)}:0", f"{h(400)}:0")])
        self.add([self.child(600, f"{h(400)}:0", f"{h(500)}:0"),
                  self.child(700, f"{h(400)}:1", f"{h(400)}:1")])

    def call(self, method, *params):
        self.calls.append((method, params))
        height = max(self.blocks)
        if method == "getblockchaininfo":
            return {"chain": self.network, "blocks": height, "headers": height, "bestblockhash": self.blocks[height]["hash"], "initialblockdownload": False}
        if method == "getblockhash":
            return self.blocks[params[0]]["hash"]
        if method == "getblock":
            block = next(block for block in self.blocks.values() if block["hash"] == params[0])
            return {"hash": block["hash"], "tx": [tx["txid"] for tx in block["tx"]]}
        if method == "getrawtransaction":
            tx = self.transactions[params[0]]
            if len(params) != 3 or params[1] is not True or params[2] != tx["blockhash"]:
                raise AssertionError("Recovery must be bound to the indexed block")
            return tx
        raise AssertionError("Relationship discovery must not use wallets or mutate Core: " + method)


class RelationshipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "testnet4"
        self.chain = Chain(self.directory); self.addCleanup(self.chain.close)

    def query(self):
        return QuantumRelationshipQuery(self.directory, "testnet4", self.chain)

    def test_stable_parents_old_successors_descendants_and_actual_transaction_order(self):
        self.chain.family(); report = self.query().discover()
        by_id = {row["id"]: row for row in report["quantums"]}
        self.assertEqual([row["id"] for row in report["quantums"][:2]], [self.chain.parent, self.chain.other])
        for number in (400, 500, 600):
            self.assertEqual(by_id[f"{h(number)}:1"]["parents"], [self.chain.parent])
            self.assertEqual(by_id[f"{h(number)}:1"]["parentStatus"], "verified")
        self.assertEqual(by_id[f"{h(700)}:1"]["parents"], [f"{h(400)}:1"])
        self.assertEqual(by_id[f"{h(300)}:0"]["parentStatus"], "none")
        self.assertEqual(report["families"][0]["members"], [self.chain.parent, *[f"{h(n)}:1" for n in (400, 500, 600, 700)]])
        self.assertEqual(len(report["families"]), 3, "Matching collection names do not join unrelated Quantums")
        self.assertTrue(report["synchronized"])
        self.assertNotIn("price", json.dumps(report))

    def test_same_public_api_for_mainnet_and_testnet4(self):
        main_directory = Path(self.temp.name) / "main"
        main = Chain(main_directory, "main"); self.addCleanup(main.close)
        self.chain.family(); main.family()
        first = self.query().discover(); second = QuantumRelationshipQuery(main_directory, "main", main).discover()
        self.assertEqual(first["families"], second["families"])
        self.assertEqual(second["network"], "main")
        self.assertEqual(first["network"], "testnet4")

    def test_read_only_index_and_immutable_recovery_cache(self):
        self.chain.family()
        before = {p.name: p.read_bytes() for p in self.directory.iterdir() if p.is_file()}
        query = self.query(); query.discover()
        recovered = sum(method == "getrawtransaction" for method, _ in self.chain.calls)
        query.discover()
        self.assertEqual(sum(method == "getrawtransaction" for method, _ in self.chain.calls), recovered)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.directory.iterdir() if p.is_file()})

    def test_multiple_parents_are_verified_as_one_ordered_proof(self):
        tx = self.chain.child(400, (self.chain.parent, self.chain.other), (self.chain.parent, self.chain.other), owner=2)
        self.chain.add([tx]); report = self.query().discover()
        child = next(row for row in report["quantums"] if row["id"] == f"{h(400)}:2")
        self.assertEqual(child["parents"], [self.chain.parent, self.chain.other])
        self.assertEqual(len(report["families"][0]["members"]), 3)

    def test_partial_or_reordered_parent_control_does_not_create_links(self):
        tx = self.chain.child(400, (self.chain.other, self.chain.parent), (self.chain.parent, self.chain.other), owner=2)
        self.chain.add([tx]); child = self.query().discover()["quantums"][-1]
        self.assertEqual(child["parents"], []); self.assertEqual(child["parentStatus"], "unresolved")

    def test_unspent_parent_claim_is_not_proof(self):
        tx = quantum(400, "Claim only", parents=self.chain.parent, owner=1)
        self.chain.add([tx]); child = self.query().discover()["quantums"][-1]
        self.assertEqual(child["parents"], [])
        self.assertEqual(child["declaredParents"], [self.chain.parent])

    def test_parent_recreation_must_keep_full_value_and_script(self):
        for number, change in [(400, "value"), (500, "script")]:
            current = self.chain.parent if number == 400 else f"{h(400)}:0"
            tx = self.chain.child(number, self.chain.parent, current)
            if change == "value": tx["vout"][0]["value_bits"] = 3999
            else: tx["vout"][0]["scriptPubKey"]["hex"] = OTHER_SCRIPT.hex()
            if number == 500:
                tx["vin"][0]["value_bits"] = 3999
                tx["vout"][0]["value_bits"] = 3999
            self.chain.add([tx])
            self.assertEqual(self.query().discover()["quantums"][-1]["parents"], [])

    def test_ambiguous_successor_outpoint_never_chooses_an_arbitrary_parent(self):
        transfer = {"txid": h(400), "vin": [{"txid": h(200), "vout": 0, "value_bits": 4000}, {"txid": h(100), "vout": 0, "value_bits": 4000}], "vout": [output(8000)]}
        self.chain.add([transfer])
        tx = self.chain.child(500, f"{h(400)}:0", f"{h(400)}:0")
        tx["vin"][0]["value_bits"] = 8000; tx["vout"][0]["value_bits"] = 8000
        self.chain.add([tx]); self.assertEqual(self.query().discover()["quantums"][-1]["parents"], [])

    def test_canonical_carrier_and_index_hash_are_both_checked(self):
        self.chain.family()
        self.chain.transactions[h(400)]["vout"][2]["value_bits"] = 1
        with self.assertRaisesRegex(RelationshipError, "not canonical"):
            self.query().discover()
        self.chain.transactions[h(400)]["vout"][2]["value_bits"] = 0
        with closing(sqlite3.connect(self.directory / "quantum.sqlite3")) as db, db:
            db.execute("UPDATE quantums SET content_sha256=? WHERE quantum_id=?", (h(0), self.chain.parent))
        with self.assertRaisesRegex(RelationshipError, "differs"):
            self.query().discover()

    def test_wrong_network_genesis_profile_and_tips_are_rejected(self):
        with self.assertRaises(RpcError): QuantumRelationshipQuery(self.directory, "main", self.chain).discover()
        with self.assertRaises(RelationshipError): QuantumRelationshipQuery(self.directory, "pebbletest", self.chain)
        with closing(sqlite3.connect(self.directory / "leap-index.sqlite3")) as db, db:
            db.execute("UPDATE index_config SET pebble_profile='portable'")
        with self.assertRaises(RelationshipError): self.query().discover()
        with closing(sqlite3.connect(self.directory / "leap-index.sqlite3")) as db, db:
            db.execute("UPDATE index_config SET pebble_profile='off'")
        with patch.object(self.chain, "call", side_effect=lambda method, *params: h(999) if method == "getblockhash" else {"chain": "testnet4"}):
            with self.assertRaisesRegex(RelationshipError, "committed chain"): self.query().discover()

    def test_reading_a_committed_prefix_does_not_claim_full_synchronization(self):
        original = self.chain.call
        def ahead(method, *params):
            value = original(method, *params)
            return {**value, "blocks": 1, "headers": 1, "bestblockhash": h(9999)} if method == "getblockchaininfo" else value
        with patch.object(self.chain, "call", side_effect=ahead):
            report = self.query().discover()
        self.assertFalse(report["synchronized"]); self.assertEqual(report["indexHeight"], 0)

    def test_reorg_during_recovery_is_rejected_and_new_query_rebuilds(self):
        self.chain.family(); original = self.chain.call; changed = False
        def reorg(method, *params):
            nonlocal changed
            value = original(method, *params)
            if method == "getrawtransaction" and not changed:
                changed = True; self.chain.index.disconnect_tip(); self.chain.blocks.pop(3)
            return value
        with patch.object(self.chain, "call", side_effect=reorg):
            with self.assertRaisesRegex(RelationshipError, "changed during discovery"): self.query().discover()
        self.assertEqual(len(self.query().discover()["quantums"]), 5)

    def test_cli_is_read_only_and_defaults_to_mainnet(self):
        default = parser().parse_args(["index", "relationships", "--data-dir", str(self.directory)])
        self.assertEqual(default.chain, "main")
        args = parser().parse_args(["index", "relationships", "--chain", "testnet4", "--data-dir", str(self.directory)])
        stream = StringIO()
        with patch("quantums.cli.rpc_from_args", return_value=self.chain) as rpc, patch("sys.stdout", stream):
            args.function(args)
        rpc.assert_called_once_with(args, public=True)
        self.assertEqual(json.loads(stream.getvalue())["network"], "testnet4")

    def test_missing_incompatible_and_partially_committed_indexes_are_not_created_or_used(self):
        missing = Path(self.temp.name) / "missing"
        with self.assertRaises(RelationshipError):
            QuantumRelationshipQuery(missing, "testnet4", self.chain).discover()
        self.assertFalse(missing.exists())
        with closing(sqlite3.connect(self.directory / "quantum.sqlite3")) as db, db:
            db.execute("UPDATE index_tip SET height=99")
        with self.assertRaisesRegex(RelationshipError, "synchronizing"): self.query().discover()
        with closing(sqlite3.connect(self.directory / "quantum.sqlite3")) as db, db:
            db.execute("UPDATE schema_info SET version=99")
        with self.assertRaisesRegex(RelationshipError, "schema"): self.query().discover()


if __name__ == "__main__":
    unittest.main()
