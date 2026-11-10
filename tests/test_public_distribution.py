"""Public release boundaries, with no live node or operational data."""

from contextlib import redirect_stderr
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

from indexer.materializers import operational_modules
from indexer.unified import IndexError, MODULE_DATABASE_NAMES, UnifiedIndex
from quantums.cli import parser
from quantums.networks import NETWORKS
from quantums.rpc import NetworkBoundRpc, RpcError


class PublicDistributionTests(unittest.TestCase):
    def test_experimental_packages_and_operations_are_not_installed(self):
        for name in ("pebbles", "quantums.pebbletest_ops", "quantums.portable_ops",
                     "quantums.profile_ops", "indexer.native_pebble", "indexer.pson"):
            with self.subTest(module=name):
                self.assertIsNone(importlib.util.find_spec(name))

    def test_public_cli_rejects_experimental_commands_networks_and_profiles(self):
        invalid = (
            ["pebble", "assets"],
            ["node", "status", "--chain", "pebbletest"],
            ["node", "status", "--pebble-profile", "native"],
            ["node", "status", "--pebble-profile", "portable"],
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    parser().parse_args(arguments)
                self.assertEqual(caught.exception.code, 2)
        self.assertNotIn("pebble", parser().format_help().lower())

    def test_public_networks_and_active_materializers_remain_available(self):
        self.assertEqual(NETWORKS, ("main", "testnet4", "regtest"))
        self.assertEqual(parser().parse_args(["node", "status"]).chain, "main")
        for network in NETWORKS:
            self.assertEqual(set(operational_modules(network)),
                             {"quantums", "galleries", "arbitraries"})

    def test_old_activation_requests_fail_instead_of_silently_downgrading(self):
        for network, profile in (("main", "native"), ("regtest", "portable"),
                                 ("pebbletest", "off")):
            with self.subTest(network=network, profile=profile):
                with self.assertRaises(ValueError):
                    operational_modules(network, pebble_profile=profile)

    def test_schema_slots_are_inactive_and_layout_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            with UnifiedIndex(directory, operational_modules("testnet4")) as index:
                index.bind_network("testnet4")
                self.assertEqual(index.tip, (-1, None))
                status = index.status(0)
                self.assertEqual(status.modules["pebbles"], "inactive")
                self.assertEqual(status.modules["pson"], "inactive")
                self.assertEqual(tuple(index.connection.execute(
                    "SELECT schema_version,network,pebble_profile FROM index_config"
                ).fetchone()), (4, "testnet4", "off"))
                for ordinal in (3, 4):
                    tables = {row[0] for row in index.connection.execute(
                        f"SELECT name FROM module_{ordinal}.sqlite_master WHERE type='table'"
                    )}
                    self.assertEqual(tables, {"index_tip"})
            self.assertEqual({p.name for p in Path(directory).glob("*.sqlite3")},
                             set(MODULE_DATABASE_NAMES.values()) | {"leap-index.sqlite3"})

    def test_active_legacy_index_is_refused_without_rebinding_its_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            with UnifiedIndex(directory) as index:
                index.bind_network("testnet4")
                index.connection.execute("UPDATE index_config SET pebble_profile='portable'")
                index.connection.commit()
            with UnifiedIndex(directory, operational_modules("testnet4")) as index:
                with self.assertRaisesRegex(IndexError, "bound to Pebble profile"):
                    index.bind_network("testnet4")
                self.assertEqual(index.connection.execute(
                    "SELECT pebble_profile FROM index_config"
                ).fetchone()[0], "portable")

    def test_wrong_network_cannot_reach_a_wallet_method(self):
        class Transport:
            def __init__(self):
                self.calls = []

            def for_wallet(self, wallet):
                if wallet is not None:
                    raise AssertionError("no wallet should be selected")
                return self

            def call(self, method, *params):
                self.calls.append(method)
                if method != "getblockchaininfo":
                    raise AssertionError("wallet method reached")
                return {"chain": "testnet4"}

        transport = Transport()
        with self.assertRaises(RpcError):
            NetworkBoundRpc(transport, "main").call("getbalances")
        self.assertEqual(transport.calls, ["getblockchaininfo"])


if __name__ == "__main__":
    unittest.main()
