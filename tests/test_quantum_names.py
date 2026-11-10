from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quantums.batch import BatchError, load_batch
from quantums.cli import parser


class QuantumNameTests(unittest.TestCase):
    def test_cli_requires_non_blank_names_before_any_rpc(self):
        commands = (
            ["inscribe", "art.webp", "--fee", "1"],
            ["wallet", "inscribe", "--file", "art.webp", "--fee", "1"],
            ["build", "art.webp", "--out", "art.quantum"],
        )
        for command in commands:
            for suffix in ([], ["--name", ""], ["--name", " \t\n"]):
                with self.subTest(command=command, suffix=suffix), redirect_stderr(StringIO()) as errors:
                    with patch("quantums.cli.rpc_from_args") as rpc, self.assertRaises(SystemExit) as result:
                        parser().parse_args(command + suffix)
                    self.assertEqual(result.exception.code, 2)
                    self.assertIn("--name", errors.getvalue())
                    rpc.assert_not_called()
            parsed = parser().parse_args(command + ["--name", "Sample Name"])
            self.assertEqual(parsed.name, "Sample Name")

    def test_each_batch_entry_requires_an_explicit_non_blank_text_name(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "art.txt").write_text("Artwork")
            path = root / "batch.yaml"
            for name_field in ("", "    name: null\n", '    name: ""\n', '    name: "   "\n', "    name: 42\n"):
                with self.subTest(name_field=name_field):
                    path.write_text("mode: parentless\ninscriptions:\n  - file: art.txt\n    name: First Artwork\n  - file: art.txt\n" + name_field)
                    with self.assertRaisesRegex(BatchError, "inscription 1 requires a non-blank name"):
                        load_batch(path)
            path.write_text('mode: parentless\ninscriptions:\n  - file: art.txt\n    name: Sample Name\n')
            self.assertEqual(load_batch(path).items[0].artifact.name, "Sample Name")


if __name__ == "__main__":
    unittest.main()
