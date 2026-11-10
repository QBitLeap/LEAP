import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from indexer.undo import UndoRecorder, apply_undo, initialize_undo


class IncrementalUndoTests(unittest.TestCase):
    def connection(self, directory):
        connection = sqlite3.connect(Path(directory) / "undo.sqlite3")
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("ATTACH DATABASE ? AS module_0", (str(Path(directory) / "module.sqlite3"),))
        connection.execute("CREATE TABLE module_0.objects(id INTEGER PRIMARY KEY, immutable BLOB NOT NULL, value INTEGER NOT NULL)")
        initialize_undo(connection, "module_0", {"objects", "locations"})
        return connection

    def test_insert_update_delete_inverse_mutations_restore_exact_state(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = self.connection(directory)
            connection.execute("INSERT INTO module_0.objects VALUES(1,?,10)", (b"immutable",))
            before = [tuple(row) for row in connection.execute("SELECT * FROM module_0.objects")]
            undo = UndoRecorder(connection, "module_0", 0, "block")
            undo.update("objects", {"id": 1}, {"value": 11})
            undo.insert("objects", {"id": 2, "immutable": b"new", "value": 20}, {"id": 2})
            undo.delete("objects", {"id": 1})
            apply_undo(connection, "module_0", 0, "block")
            self.assertEqual([tuple(row) for row in connection.execute("SELECT * FROM module_0.objects")], before)

    def test_malformed_or_missing_sequence_fails_without_partial_inverse(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = self.connection(directory)
            connection.execute("INSERT INTO module_0.objects VALUES(1,?,10)", (b"immutable",))
            undo = UndoRecorder(connection, "module_0", 0, "block")
            undo.update("objects", {"id": 1}, {"value": 11})
            undo.update("objects", {"id": 1}, {"value": 12})
            connection.execute("DELETE FROM module_0.undo_mutations WHERE height=0 AND sequence=0")
            connection.commit()
            connection.execute("BEGIN")
            with self.assertRaisesRegex(RuntimeError, "missing, duplicated, or out of order"):
                apply_undo(connection, "module_0", 0, "block")
            connection.rollback()
            self.assertEqual(connection.execute("SELECT value FROM module_0.objects WHERE id=1").fetchone()[0], 12)

    def test_undo_rows_scale_with_mutations_across_two_thousand_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = self.connection(directory)
            connection.execute("CREATE TABLE module_0.locations(id INTEGER PRIMARY KEY, value INTEGER NOT NULL)")
            for object_id in range(100):
                connection.execute("INSERT INTO module_0.objects VALUES(?,?,0)", (object_id, b"x" * 256))
                connection.execute("INSERT INTO module_0.locations VALUES(?,0)", (object_id,))
            for height in range(2_000):
                object_id = height % 100
                undo = UndoRecorder(connection, "module_0", height, f"block-{height}")
                undo.update("locations", {"id": object_id}, {"value": height + 1})
            connection.commit()
            self.assertEqual(connection.execute("SELECT count(*) FROM module_0.undo_mutations").fetchone()[0], 2_000)
            self.assertEqual(connection.execute("SELECT sum(mutation_count) FROM module_0.block_undo").fetchone()[0], 2_000)
            sample = connection.execute("SELECT previous_row FROM module_0.undo_mutations WHERE height=1999").fetchone()[0]
            self.assertLess(len(sample), 40)

    def test_interrupted_transaction_leaves_neither_state_change_nor_undo(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = self.connection(directory)
            connection.execute("INSERT INTO module_0.objects VALUES(1,?,10)", (b"immutable",))
            connection.commit()
            connection.close()
            script = r'''
import os, sqlite3, sys
from pathlib import Path
from indexer.undo import UndoRecorder
root=Path(sys.argv[1])
c=sqlite3.connect(root/'undo.sqlite3')
c.row_factory=sqlite3.Row
c.execute('PRAGMA foreign_keys=ON')
c.execute('ATTACH DATABASE ? AS module_0',(str(root/'module.sqlite3'),))
c.execute('BEGIN IMMEDIATE')
u=UndoRecorder(c,'module_0',0,'block')
u.update('objects',{'id':1},{'value':11})
os._exit(91)
'''
            result = subprocess.run([sys.executable, "-c", script, directory], cwd=Path(__file__).parents[1])
            self.assertEqual(result.returncode, 91)
            recovered = sqlite3.connect(Path(directory) / "undo.sqlite3")
            recovered.row_factory = sqlite3.Row
            recovered.execute("ATTACH DATABASE ? AS module_0", (str(Path(directory) / "module.sqlite3"),))
            self.assertEqual(recovered.execute("SELECT value FROM module_0.objects WHERE id=1").fetchone()[0], 10)
            self.assertEqual(recovered.execute("SELECT count(*) FROM module_0.block_undo").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
