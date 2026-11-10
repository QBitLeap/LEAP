from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from indexapp import bind_writer, configuration, sqlite_snapshot, observed_status
from service.rpc import CoreUnavailable, GENESIS

spec = importlib.util.spec_from_file_location("package", ROOT / "scripts/prepare_app_package.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


def fixture(directory, protocol="leap", chain="main"):
    with bind_writer(directory, protocol, chain):
        pass
    database = directory / ("leap-index.sqlite3" if protocol == "leap" else "index.sqlite")
    with closing(sqlite3.connect(database)) as db:
        db.execute("CREATE TABLE blocks(height INTEGER,hash TEXT)")
        db.execute("INSERT INTO blocks VALUES(0,?)", (GENESIS[chain],))
        if protocol == "leap":
            db.execute("CREATE TABLE index_config(singleton INTEGER,schema_version INTEGER,network TEXT,pebble_profile TEXT)")
            db.execute("INSERT INTO index_config VALUES(1,4,?,'off')", (chain,))
        else:
            db.execute("CREATE TABLE meta(key TEXT,value TEXT)")
            db.execute("INSERT INTO meta VALUES('schema','1')")
            db.execute("INSERT INTO meta VALUES('network',?)", ("mainnet" if chain == "main" else chain,))
            db.execute("CREATE TABLE inscriptions(number INTEGER,payload TEXT)")
            for number in range(3):
                db.execute("INSERT INTO inscriptions VALUES(?,?)", (number, json.dumps({"id": f"pins:{number}", "number": number})))
        db.commit()
    if protocol == "leap":
        with closing(sqlite3.connect(directory / "quantum.sqlite3")) as db:
            db.execute("CREATE TABLE index_tip(singleton INTEGER,height INTEGER,hash TEXT)")
            db.execute("INSERT INTO index_tip VALUES(1,0,?)", (GENESIS[chain],))
            db.execute("CREATE TABLE schema_info(singleton INTEGER,module TEXT,version INTEGER)")
            db.execute("INSERT INTO schema_info VALUES(1,'quantums',2)")
            db.execute("CREATE TABLE quantums(quantum_id TEXT,creation_height INTEGER,creation_txid TEXT,creation_vout INTEGER,name TEXT)")
            db.execute("CREATE TABLE quantum_locations(quantum_id TEXT,current_txid TEXT,current_vout INTEGER,current_address TEXT)")
            db.execute("INSERT INTO quantums VALUES('fixture:0',0,'fixture',0,'<script>literal</script>')")
            db.commit()
        for filename in ("gallery.sqlite3", "arbitrary.sqlite3", "pebble.sqlite3", "pson.sqlite3"):
            with closing(sqlite3.connect(directory / filename)) as db:
                db.execute("CREATE TABLE index_tip(singleton INTEGER,height INTEGER,hash TEXT)")
                db.execute("INSERT INTO index_tip VALUES(1,0,?)", (GENESIS[chain],))
                db.commit()


class IndexAppTests(unittest.TestCase):
    def test_image_role_is_fixed_and_network_must_be_explicit(self):
        expected = json.loads((ROOT / "app/protocol.json").read_text())["protocol"]
        with patch.dict(os.environ, {"INDEX_PROTOCOL": expected, "QBIT_CHAIN": "testnet4"}, clear=True):
            self.assertEqual(configuration(), (expected, "testnet4"))
        with patch.dict(os.environ, {"INDEX_PROTOCOL": "pins" if expected != "pins" else "leap",
                                     "QBIT_CHAIN": "main"}, clear=True):
            with self.assertRaises(ValueError):
                configuration()
        with patch.dict(os.environ, {"INDEX_PROTOCOL": expected}, clear=True):
            with self.assertRaises(ValueError):
                configuration()

    def test_writer_identity_and_lock_prevent_wrong_network_and_duplicate_writer(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            with bind_writer(directory, "leap", "main"):
                with self.assertRaises(BlockingIOError):
                    bind_writer(directory, "leap", "main")
            with self.assertRaises(ValueError):
                bind_writer(directory, "leap", "testnet4")
            with self.assertRaises(ValueError):
                bind_writer(directory, "pins", "main")

    def test_unmarked_existing_state_is_preserved_and_refused(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            (directory / "existing.sqlite").write_bytes(b"preserved")
            with self.assertRaises(ValueError):
                bind_writer(directory, "pins", "main")
            self.assertEqual((directory / "existing.sqlite").read_bytes(), b"preserved")
            self.assertFalse((directory / ".index-app.json").exists())

    def test_missing_index_is_not_initialized_by_read_api(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            with self.assertRaises(CoreUnavailable):
                sqlite_snapshot(directory, "leap", "main")
            self.assertEqual(list(directory.iterdir()), [])

    def test_quantum_snapshot_is_read_only_and_literal(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            fixture(directory)
            before = {p.name: p.read_bytes() for p in directory.iterdir()}
            result = sqlite_snapshot(directory, "leap", "main", catalog=True)
            self.assertEqual(result["items"], 1)
            self.assertEqual(result["rows"][0]["name"], "<script>literal</script>")
            self.assertEqual(before, {p.name: p.read_bytes() for p in directory.iterdir()})
            with closing(sqlite3.connect(directory / "quantum.sqlite3")) as db:
                db.execute("UPDATE index_tip SET height=1")
                db.commit()
            with self.assertRaises(CoreUnavailable):
                sqlite_snapshot(directory, "leap", "main")

    def test_pins_catalog_is_paginated_and_network_bound(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            fixture(directory, "pins")
            result = sqlite_snapshot(directory, "pins", "main", catalog=True, offset=1, limit=1)
            self.assertEqual(result["items"], 3)
            self.assertEqual(result["rows"], [{"id": "pins:1", "number": 1}])
            with self.assertRaises(CoreUnavailable):
                sqlite_snapshot(directory, "pins", "testnet4")

    def test_readiness_never_infers_routing_authority_and_checks_node_genesis(self):
        class Client:
            def __init__(self, genesis=GENESIS["main"], ibd=False):
                self.genesis, self.ibd = genesis, ibd
            def call(self, method, params):
                if method == "getblockhash":
                    return self.genesis
                return {"chain": "main", "blocks": 0, "headers": 0, "initialblockdownload": self.ibd}
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            fixture(directory)
            result = observed_status("leap", "main", directory, Client())
            self.assertTrue(result["observedSynchronized"])
            self.assertFalse(result["transactionRoutingAccepted"])
            self.assertFalse(observed_status("leap", "main", directory, Client(ibd=True))["observedSynchronized"])
            with self.assertRaises(CoreUnavailable):
                observed_status("leap", "main", directory, Client(genesis="b" * 64))

    def test_package_exposes_authenticated_api_without_core_cookie_or_shared_datadir(self):
        with tempfile.TemporaryDirectory() as name:
            output = Path(name) / "independent-index"
            report = package.prepare(output, output.name, "testnet4", 4100,
                                     "sha256:" + "a" * 64, "independent-core", True)
            services = json.loads((output / "docker-compose.yml").read_text())["services"]
            manifest = json.loads((output / "umbrel-app.yml").read_text())
            self.assertNotIn("submission", manifest)
            self.assertEqual(manifest["icon"], "https://raw.githubusercontent.com/QBitLeap/" + ROOT.name + "/main/icon.svg")
            self.assertEqual(manifest["dependencies"], ["independent-core"])
            self.assertEqual(services["indexer"]["environment"]["QBIT_CHAIN"], "testnet4")
            self.assertEqual(services["indexer"]["volumes"], ["${APP_DATA_DIR}/data/index:/data"])
            self.assertEqual(services["ui"]["volumes"], ["${APP_DATA_DIR}/data/index:/data:ro"])
            self.assertNotIn("volumes", services["transport"])
            self.assertNotIn("INDEX_CORE_READ_TOKEN", services["indexer"]["environment"])
            self.assertEqual(services["indexer"]["network_mode"], "service:transport")
            self.assertFalse(report["fullUmbrelAppAccepted"])
            for value in services.values():
                self.assertNotIn("ports", value)
            self.assertNotIn("cookie", (output / "exports.sh").read_text())


if __name__ == "__main__":
    unittest.main()
