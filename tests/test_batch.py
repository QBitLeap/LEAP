from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from quantums.batch import Batch, BatchError, BatchItem, load_batch, split_batch_plan
from quantums.envelope import create_artifact, encode_batch


class BatchTests(unittest.TestCase):
    def test_all_documented_batch_iterations_load(self):
        parentless = load_batch(Path("batch-examples/parentless.yaml"))
        parented = load_batch(Path("batch-examples/parented.yaml"))
        selected = load_batch(Path("batch-examples/pick-utxo.yaml"))

        self.assertEqual(parentless.mode, "parentless")
        self.assertEqual(parentless.items[0].artifact.metadata["Sequence"], 1)
        self.assertEqual(parented.mode, "parented")
        self.assertEqual(parented.parent, "0931428b93490652872b35f91f57f7b33f66b8daf851a845c164d1de7ea86283:0")
        self.assertEqual(selected.mode, "pick-utxo")
        self.assertEqual(selected.items[1].utxo, "2" * 64 + ":1")

    def test_runnable_example_loads(self):
        batch = load_batch(Path("examples/batch-runnable.yaml"))
        self.assertEqual(len(batch.items), 2)
        self.assertEqual(batch.items[0].artifact.name, "Hello from Leap")
        self.assertEqual(batch.items[1].artifact.content_type, "application/json")

    def test_unknown_and_duplicate_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "x.txt").write_text("x")
            (root / "unknown.yaml").write_text(
                "version: 1\nmode: parentless\nbogus: true\ninscriptions:\n  - file: x.txt\n    name: Test Quantum\n"
            )
            with self.assertRaises(BatchError):
                load_batch(root / "unknown.yaml")
            (root / "duplicate.yaml").write_text(
                "version: 1\nversion: 1\nmode: parentless\ninscriptions:\n  - file: x.txt\n    name: Test Quantum\n"
            )
            with self.assertRaises(BatchError):
                load_batch(root / "duplicate.yaml")

    def test_mode_specific_fields_are_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "x.txt").write_text("x")
            (root / "missing-parent.yaml").write_text(
                "mode: parented\ncollection:\n  name: X\ninscriptions:\n  - file: x.txt\n    name: Test Quantum\n"
            )
            with self.assertRaises(BatchError):
                load_batch(root / "missing-parent.yaml")
            (root / "missing-utxo.yaml").write_text(
                "mode: pick-utxo\ninscriptions:\n  - file: x.txt\n    name: Test Quantum\n"
            )
            with self.assertRaises(BatchError):
                load_batch(root / "missing-utxo.yaml")

    def test_paths_are_relative_to_batch_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "x.txt").write_text("hello")
            (root / "batch.yaml").write_text(
                "version: 1\nmode: parentless\ninscriptions:\n  - file: x.txt\n    name: Test Quantum\n"
            )
            batch = load_batch(root / "batch.yaml")
            self.assertEqual(batch.items[0].file, (root / "x.txt").resolve())

    def test_parented_yaml_accepts_multiple_unique_parents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "x.txt").write_text("x")
            first, second = "1" * 64 + ":0", "2" * 64 + ":1"
            (root / "batch.yaml").write_text(
                "mode: parented\ncollection:\n  name: X\n  parent:\n"
                f"    - '{first}'\n    - '{second}'\n"
                "inscriptions:\n  - file: x.txt\n    name: Test Quantum\n"
            )
            batch = load_batch(root / "batch.yaml")
            self.assertEqual(batch.parent, (first, second))
            self.assertEqual(batch.items[0].artifact.parent, (first, second))
            chunk = split_batch_plan(batch)[0]
            self.assertEqual(chunk.items[0].artifact.ownership_output, 2)

    def test_split_plan_preserves_order_and_recomputes_chunk_vouts(self):
        artifacts = tuple(
            create_artifact(
                b"x" * 62_000,
                content_type="text/plain",
                name=f"{index}.txt",
                metadata={"description": "m" * 1_000},
                format_version=2,
            )
            for index in range(4)
        )
        items = tuple(BatchItem(Path(f"{index}.txt"), artifact, None, 4_000) for index, artifact in enumerate(artifacts))
        chunks = split_batch_plan(Batch(Path("batch.yaml"), items, "parented", "0" * 64 + ":0"))
        self.assertEqual(tuple(len(chunk.items) for chunk in chunks), (3, 1))
        self.assertEqual(tuple(item.artifact.name for chunk in chunks for item in chunk.items), tuple(a.name for a in artifacts))
        self.assertEqual(tuple(item.artifact.ownership_output for item in chunks[0].items), (1, 2, 3))
        self.assertEqual(tuple(item.artifact.ownership_output for item in chunks[1].items), (1,))
        self.assertTrue(all(len(encode_batch(tuple(item.artifact for item in chunk.items))) <= 190_000 for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
