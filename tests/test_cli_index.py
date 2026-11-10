import argparse
import io
import json
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from quantums.cli import (
    command_index_run,
    command_index_status,
    main,
    parser,
    require_synchronized_index,
)


class FakeRpc:
    def call(self, method, *params):
        self.assert_no_wallet_method(method)
        return {"blocks": 7, "chain": "testnet4"}

    @staticmethod
    def assert_no_wallet_method(method):
        if "wallet" in method.lower():
            raise AssertionError("index status must not access a wallet")


class GuardRpc:
    def __init__(self, block_hash="a" * 64, *, initial_download=False, headers=7):
        self.block_hash = block_hash
        self.initial_download = initial_download
        self.headers = headers

    def call(self, method, *params):
        if method != "getblockchaininfo" or params:
            raise AssertionError(f"unexpected RPC: {method} {params}")
        return {
            "chain": "testnet4", "blocks": 7, "headers": self.headers,
            "bestblockhash": self.block_hash,
            "initialblockdownload": self.initial_download,
        }


class GuardConnection:
    @staticmethod
    def execute(_query):
        return SimpleNamespace(fetchone=lambda: ("testnet4", "off"))


class GuardIndex:
    connection = GuardConnection()

    def __init__(self, _directory, _modules):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    @staticmethod
    def bind_network(_network, _pebble_profile="auto"):
        pass

    @staticmethod
    def status(node_height):
        return SimpleNamespace(
            node_height=node_height, index_height=7, index_hash="a" * 64,
            modules={"quantums": "ok", "galleries": "ok", "arbitraries": "ok",
                     "pebbles": "inactive", "pson": "inactive"},
        )


class RunIndex:
    def __init__(self, _directory, _modules):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    @staticmethod
    def bind_network(_network, _pebble_profile="auto"):
        pass


class IndexCliTests(unittest.TestCase):
    def test_parser_exposes_explicit_status_and_run_commands(self):
        status = parser().parse_args(["index", "status", "--data-dir", "/tmp/index"])
        run = parser().parse_args(["index", "run", "--data-dir", "/tmp/index"])
        self.assertEqual((status.index_command, run.index_command), ("status", "run"))



    def test_transaction_commands_broadcast_by_default_and_offer_dry_run(self):
        commands = (
            ["inscribe", "item.bin", "--name", "Test Quantum", "--fee", "1"],
            ["gallery", "create", "--file", "gallery.yaml", "--fee", "1"],
            ["gallery", "update", "--gallery", "00" * 32 + ":0", "--fee", "1"],
            ["batch", "--batch", "batch.yaml", "--fee", "1"],
            ["wallet", "inscribe", "--file", "item.bin", "--name", "Test Quantum", "--fee", "1"],
            ["wallet", "gallery", "create", "--file", "gallery.yaml", "--fee", "1"],
            ["wallet", "gallery", "update", "--gallery", "00" * 32 + ":0", "--fee", "1"],
            ["wallet", "batch", "--batch", "batch.yaml", "--fee", "1"],
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(parser().parse_args(command).broadcast)
                self.assertFalse(parser().parse_args(command + ["--dry-run"]).broadcast)

    def test_concise_wallet_backed_commands_are_canonical(self):
        inscribe = parser().parse_args([
            "inscribe", "--wallet", "alice", "item.bin", "--name", "Test Quantum", "--fee", "1",
        ])
        balance = parser().parse_args(["balance", "--wallet", "alice"])
        receive = parser().parse_args(["receive", "--wallet", "alice"])
        gallery = parser().parse_args([
            "gallery", "inspect", "--wallet", "alice", "--gallery", "00" * 32 + ":0",
        ])
        batch = parser().parse_args([
            "batch", "--wallet", "alice", "--batch", "batch.yaml", "--fee", "1",
        ])
        self.assertEqual(inscribe.wallet, "alice")
        self.assertEqual(inscribe.content, Path("item.bin"))
        self.assertEqual(balance.wallet, "alice")
        self.assertEqual(receive.wallet, "alice")
        self.assertEqual(gallery.wallet, "alice")
        self.assertEqual(batch.wallet, "alice")

    def test_legacy_wallet_forms_remain_compatibility_aliases(self):
        concise = parser().parse_args(["balance", "--wallet", "alice"])
        legacy = parser().parse_args(["wallet", "balance", "--wallet", "alice"])
        self.assertIs(concise.function, legacy.function)

    def test_transaction_commands_accept_index_path_or_environment(self):
        direct = parser().parse_args([
            "inscribe", "item.bin", "--name", "Test Quantum", "--fee", "1", "--index-data-dir", "/tmp/index",
        ])
        self.assertEqual(direct.index_data_dir, Path("/tmp/index"))
        with patch.dict("os.environ", {"LEAP_INDEX_DATA_DIR": "/tmp/from-environment"}):
            inherited = parser().parse_args(["inscribe", "item.bin", "--name", "Test Quantum", "--fee", "1"])
        self.assertEqual(inherited.index_data_dir, Path("/tmp/from-environment"))

    def test_transaction_preflight_requires_current_healthy_network_bound_index(self):
        from indexer.unified import COORDINATOR_DATABASE_NAME, MODULE_DATABASE_NAMES

        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(index_data_dir=directory, chain="testnet4")
            with self.assertRaisesRegex(RuntimeError, "Leap index does not exist"):
                require_synchronized_index(args, GuardRpc())
            for name in (COORDINATOR_DATABASE_NAME, *MODULE_DATABASE_NAMES.values()):
                (Path(directory) / name).touch()
            with patch("indexer.unified.UnifiedIndex", GuardIndex):
                require_synchronized_index(args, GuardRpc())
                with self.assertRaisesRegex(RuntimeError, "not synchronized"):
                    require_synchronized_index(args, GuardRpc(block_hash="b" * 64))
                with self.assertRaisesRegex(RuntimeError, "Qbit node is not synchronized"):
                    require_synchronized_index(args, GuardRpc(initial_download=True))

    def test_transaction_failures_have_clear_bad_command_output(self):
        for mode in ([], ["--dry-run"]):
            with self.subTest(mode=mode):
                error = io.StringIO()
                argv = ["leap", "inscribe", "/definitely/missing", "--name", "Test Quantum", "--fee", "1", *mode]
                with patch("sys.argv", argv), redirect_stderr(error), self.assertRaises(SystemExit):
                    main()
                self.assertIn("bad command: error:", error.getvalue())

    def test_argument_errors_use_bad_command_output(self):
        error = io.StringIO()
        with redirect_stderr(error), self.assertRaises(SystemExit):
            parser().parse_args(["wallet", "balance", "--not-an-option"])
        self.assertIn("bad command: error: unrecognized arguments", error.getvalue())

    def test_status_reports_active_inactive_network_and_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(data_dir=directory, chain="testnet4")
            output = io.StringIO()
            with patch("quantums.cli.rpc_from_args", return_value=FakeRpc()), redirect_stdout(output):
                command_index_status(args)
            status = json.loads(output.getvalue())
            self.assertEqual(status["node_tip"], 7)
            self.assertEqual(status["index_tip"], -1)
            self.assertEqual(status["blocks_remaining"], 8)
            self.assertEqual(status["active_modules"], ["arbitraries", "galleries", "quantums"])
            self.assertEqual(status["inactive_modules"], ["pebbles", "pson"])
            self.assertEqual(status["network"], "testnet4")
            self.assertEqual(status["pebble_profile"], "off")
            self.assertEqual(status["status"], "catching-up")
            self.assertFalse(status["pebble_enabled"])
            self.assertEqual(status["custody"], "disabled")
            self.assertFalse(status["consensus_enforced"])
            self.assertIsNone(status["custody_warning"])
            self.assertEqual(status["runtime_database_directory"], str(Path(directory).resolve()))

    def test_run_reports_only_status_changes(self):
        status = SimpleNamespace(
            node_height=7, index_height=7,
            modules={"quantums": "ok", "pson": "inactive"},
        )

        def duplicate_status(_rpc, _index, stop, **options):
            options["on_status"](status)
            options["on_status"](status)
            stop.set()

        arguments = argparse.Namespace(
            poll_seconds=1, commit_batch=100, data_dir="/tmp/index",
            chain="testnet4",
        )
        output = io.StringIO()
        with (
            patch("quantums.cli.rpc_from_args", return_value=FakeRpc()),
            patch("indexer.unified.UnifiedIndex", RunIndex),
            patch("indexer.unified.follow", duplicate_status),
            redirect_stdout(output),
        ):
            command_index_run(arguments)
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        result = json.loads(lines[0])
        self.assertEqual(result["index_tip"], 7)
        self.assertEqual(result["status"], "synchronized")
        self.assertEqual(result["network"], "testnet4")
        self.assertEqual(result["pebble_profile"], "off")
        self.assertEqual(result["custody"], "disabled")


if __name__ == "__main__":
    unittest.main()
