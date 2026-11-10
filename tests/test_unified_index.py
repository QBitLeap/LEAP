import tempfile
import unittest
from pathlib import Path
import os
import subprocess
import sys

from indexer.unified import MODULE_DATABASE_NAMES, UnifiedIndex


class RecordingModule:
    def __init__(self, fail=False):
        self.fail = fail

    def connect(self, connection, schema, block):
        connection.execute(f"CREATE TABLE IF NOT EXISTS {schema}.seen (hash TEXT PRIMARY KEY)")
        connection.execute(f"INSERT INTO {schema}.seen VALUES (?)", (block["hash"],))
        if self.fail:
            raise ValueError("module failure")

    def disconnect(self, connection, schema, block):
        connection.execute(f"DELETE FROM {schema}.seen WHERE hash=?", (block["hash"],))


class UnifiedIndexTests(unittest.TestCase):
    def test_missing_databases_are_created_with_required_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            with UnifiedIndex(directory) as index:
                self.assertEqual(index.tip, (-1, None))
                for ordinal in range(len(MODULE_DATABASE_NAMES)):
                    self.assertEqual(index.connection.execute(f"PRAGMA module_{ordinal}.journal_mode").fetchone()[0], "delete")
                    self.assertEqual(index.connection.execute(f"PRAGMA module_{ordinal}.synchronous").fetchone()[0], 2)
                    self.assertEqual(index.connection.execute(f"PRAGMA module_{ordinal}.fullfsync").fetchone()[0], 1)
                self.assertEqual(set(index.status(10).modules.values()), {"inactive"})

    def test_module_tip_is_atomic_when_one_module_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            modules = {name: RecordingModule(name == "pson") for name in MODULE_DATABASE_NAMES}
            with UnifiedIndex(directory, modules) as index:
                with self.assertRaisesRegex(ValueError, "module failure"):
                    index.connect_block({"height": 0, "hash": "one"})
                self.assertEqual(index.tip, (-1, None))
                for ordinal in range(len(MODULE_DATABASE_NAMES)):
                    tables = index.connection.execute(
                        f"SELECT name FROM module_{ordinal}.sqlite_master WHERE name='seen'"
                    ).fetchall()
                    if tables:
                        self.assertEqual(index.connection.execute(f"SELECT count(*) FROM module_{ordinal}.seen").fetchone()[0], 0)

    def test_persists_common_tip_and_disconnects_then_replays(self):
        with tempfile.TemporaryDirectory() as directory:
            modules = {name: RecordingModule() for name in MODULE_DATABASE_NAMES}
            index = UnifiedIndex(directory, modules)
            index.connect_block({"height": 0, "hash": "one"})
            index.close()
            with UnifiedIndex(directory, modules) as resumed:
                self.assertEqual(resumed.tip, (0, "one"))
                resumed.disconnect_tip()
                self.assertEqual(resumed.tip, (-1, None))
                resumed.connect_block({"height": 0, "hash": "replacement"})
                self.assertEqual(resumed.tip, (0, "replacement"))
                self.assertEqual(set(path.name for path in Path(directory).glob("*.sqlite3")),
                                 set(MODULE_DATABASE_NAMES.values()) | {"leap-index.sqlite3"})

    def test_startup_rejects_mismatched_module_tips(self):
        with tempfile.TemporaryDirectory() as directory:
            index = UnifiedIndex(directory)
            index.connection.execute("UPDATE module_0.index_tip SET height=7,hash='wrong'")
            index.connection.commit()
            index.close()
            with self.assertRaisesRegex(Exception, "module tips are not synchronized"):
                UnifiedIndex(directory)

    def test_runtime_directory_is_bound_to_one_network(self):
        with tempfile.TemporaryDirectory() as directory:
            with UnifiedIndex(directory) as index:
                index.bind_network("testnet4")
            with UnifiedIndex(directory) as reopened:
                reopened.bind_network("testnet4")
                with self.assertRaisesRegex(Exception, "bound to testnet4"):
                    reopened.bind_network("main")

    def test_process_interruption_leaves_no_partial_module_advance(self):
        with tempfile.TemporaryDirectory() as directory:
            script = r'''
import os, sys
from indexer.unified import MODULE_DATABASE_NAMES, UnifiedIndex
class Crash:
    def __init__(self, final=False): self.final = final
    def connect(self, connection, schema, block):
        connection.execute(f"CREATE TABLE IF NOT EXISTS {schema}.seen(hash TEXT)")
        connection.execute(f"INSERT INTO {schema}.seen VALUES (?)", (block["hash"],))
        if self.final: os._exit(91)
    def disconnect(self, connection, schema, block): pass
names=list(MODULE_DATABASE_NAMES)
modules={name: Crash(name == names[-1]) for name in names}
UnifiedIndex(sys.argv[1], modules).connect_block({"height":0,"hash":"interrupted"})
'''
            result = subprocess.run([sys.executable, "-c", script, directory], cwd=Path(__file__).parents[1])
            self.assertEqual(result.returncode, 91)
            with UnifiedIndex(directory) as recovered:
                self.assertEqual(recovered.tip, (-1, None))
                self.assertIsNone(recovered.connection.execute("SELECT 1 FROM blocks").fetchone())
