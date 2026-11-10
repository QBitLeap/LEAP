from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quantums.cli import command_wallet_batch
from quantums.rpc import RpcError
from quantums.transaction import PreparedBatch


PARENT = "a" * 64 + ":0"


class FakeRpc:
    def __init__(self):
        self.sent = 0

    def call(self, method, *params):
        if method == "getbalances":
            return {"mine": {"trusted": 10, "untrusted_pending": 0}}
        if method == "sendrawtransaction":
            self.sent += 1
            return f"{self.sent:064x}"
        if method == "lockunspent":
            return True
        raise AssertionError(f"unexpected RPC call: {method}")


def prepared(chunk, number):
    artifacts = tuple(item.artifact for item in chunk.items)
    return PreparedBatch(
        artifacts=artifacts,
        owner_addresses=tuple(f"owner-{number}-{index}" for index in range(len(artifacts))),
        ownership_bits=tuple(item.ownership_bits for item in chunk.items),
        envelope_bytes=180_000,
        hex=f"signed-{number}",
        fee_qbt="0.00001",
        vsize=190_000,
        allowed=True,
        reject_reason=None,
        txid=f"{number + 100:064x}",
        inputs=(),
    )


class MultiTransactionCliTests(unittest.TestCase):
    def setUp(self):
        naming = patch("quantums.cli.require_available_names")
        self.addCleanup(naming.stop)
        naming.start()
        catalog = patch("quantums.cli.remember_pending_names")
        self.addCleanup(catalog.stop)
        catalog.start()

    def make_batch(self, root: Path, mode: str) -> Path:
        for index in range(4):
            (root / f"{index}.txt").write_bytes(b"x" * 63_000)
        parent = f"\ncollection:\n  name: Split Test\n  parent: {PARENT}" if mode == "parented" else ""
        inscriptions = "\n".join(f"  - file: {index}.txt\n    name: Quantum {index + 1}" for index in range(4))
        path = root / "batch.yaml"
        path.write_text(f"mode: {mode}{parent}\ninscriptions:\n{inscriptions}\n")
        return path

    def args(self, batch: Path, report: Path, *, broadcast: bool) -> argparse.Namespace:
        return argparse.Namespace(
            batch=batch, report=report, resume=False, broadcast=broadcast, yes=True,
            fee_rate=Decimal("0.33"), wallet="leap", rpc_url=None, cli="qbit-cli",
            chain="testnet4", datadir=None,
        )

    def test_parentless_prepares_every_chunk_before_dry_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch = self.make_batch(root, "parentless")
            calls = []

            def prepare(_rpc, chunk, **_kwargs):
                calls.append(chunk)
                return prepared(chunk, len(calls))

            with patch("quantums.cli.rpc_from_args", return_value=FakeRpc()), patch("quantums.cli.require_synchronized_index"), patch("quantums.cli.prepare_batch", side_effect=prepare):
                command_wallet_batch(self.args(batch, root / "report.json", broadcast=False))
            self.assertEqual(tuple(len(chunk.items) for chunk in calls), (3, 1))

    def test_parented_broadcast_chains_each_parent_successor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch = self.make_batch(root, "parented")
            parents = []

            def prepare(_rpc, chunk, **_kwargs):
                parents.append(chunk.parent)
                return prepared(chunk, len(parents))

            rpc = FakeRpc()
            with patch("quantums.cli.rpc_from_args", return_value=rpc), patch("quantums.cli.require_synchronized_index"), patch("quantums.cli.prepare_batch", side_effect=prepare):
                command_wallet_batch(self.args(batch, root / "report.json", broadcast=True))
            self.assertEqual(tuple(parents), (PARENT, f"{1:064x}:0"))
            self.assertEqual(rpc.sent, 2)

    def test_multiple_parents_chain_every_successor_between_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch = self.make_batch(root, "parented")
            second = "b" * 64 + ":2"
            text = batch.read_text().replace(
                f"parent: {PARENT}",
                f"parent:\n    - '{PARENT}'\n    - '{second}'",
            )
            batch.write_text(text)
            parents = []

            def prepare(_rpc, chunk, **_kwargs):
                parents.append(chunk.parent)
                return prepared(chunk, len(parents))

            rpc = FakeRpc()
            with patch("quantums.cli.rpc_from_args", return_value=rpc), patch("quantums.cli.require_synchronized_index"), patch("quantums.cli.prepare_batch", side_effect=prepare):
                command_wallet_batch(self.args(batch, root / "report.json", broadcast=True))
            self.assertEqual(parents[0], (PARENT, second))
            self.assertEqual(parents[1], (f"{1:064x}:0", f"{1:064x}:1"))

    def test_package_limit_during_funding_is_saved_for_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch = self.make_batch(root, "parented")
            report = root / "report.json"
            calls = 0

            def prepare(_rpc, chunk, **_kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RpcError("possibly exceeds descendant size limit [limit: 404000]")
                return prepared(chunk, calls)

            with patch("quantums.cli.rpc_from_args", return_value=FakeRpc()), patch("quantums.cli.require_synchronized_index"), patch("quantums.cli.prepare_batch", side_effect=prepare):
                command_wallet_batch(self.args(batch, report, broadcast=True))
            import json
            saved = json.loads(report.read_text())
            self.assertEqual(saved["chunks"][0]["status"], "broadcast")
            self.assertEqual(saved["chunks"][1]["status"], "awaiting-confirmation")

    def test_resuming_cannot_change_the_reviewed_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); batch=self.make_batch(root,'parentless'); report=root/'report.json'
            def prepare(_rpc,chunk,**_kwargs): return prepared(chunk,1)
            with patch('quantums.cli.rpc_from_args',return_value=FakeRpc()), patch('quantums.cli.require_synchronized_index'), patch('quantums.cli.prepare_batch',side_effect=prepare):
                command_wallet_batch(self.args(batch,report,broadcast=False))
            batch.write_text(batch.read_text().replace('name: Quantum 1','name: Changed Art'))
            args=self.args(batch,report,broadcast=False);args.resume=True
            with patch('quantums.cli.rpc_from_args',return_value=FakeRpc()), patch('quantums.cli.require_synchronized_index'), patch('quantums.cli.prepare_batch') as sign:
                with self.assertRaisesRegex(RuntimeError,'Quantum Names changed'): command_wallet_batch(args)
                sign.assert_not_called()


if __name__ == "__main__":
    unittest.main()
