"""Persistent atomic coordinator for the unified Leap index."""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from decimal import Decimal
import sqlite3
import os
from pathlib import Path
from threading import Event
from typing import Any, Callable, Final, Mapping, Protocol

from .undo import UndoRecorder, apply_undo, initialize_undo


COORDINATOR_DATABASE_NAME: Final = "leap-index.sqlite3"
MODULE_DATABASE_NAMES: Final = {
    "quantums": "quantum.sqlite3",
    "galleries": "gallery.sqlite3",
    "arbitraries": "arbitrary.sqlite3",
    # Reserved inactive schema-4 slots; no corresponding processors are shipped.
    "pebbles": "pebble.sqlite3",
    "pson": "pson.sqlite3",
}


class IndexError(RuntimeError):
    pass


class Module(Protocol):
    def connect(self, connection: sqlite3.Connection, schema: str, block: Mapping[str, Any]) -> None: ...
    def disconnect(self, connection: sqlite3.Connection, schema: str, block: Mapping[str, Any]) -> None: ...


class EmptyModule:
    """Inactive module boundary; it advances no protocol state of its own."""

    def connect(self, connection: sqlite3.Connection, schema: str, block: Mapping[str, Any]) -> None:
        del connection, schema, block

    def disconnect(self, connection: sqlite3.Connection, schema: str, block: Mapping[str, Any]) -> None:
        del connection, schema, block


@dataclass(frozen=True)
class IndexStatus:
    node_height: int
    index_height: int
    index_hash: str | None
    progress: float
    modules: dict[str, str]


class UnifiedIndex:
    """Coordinate separate SQLite modules with SQLite rollback-journal commits.

    Atomic multi-file commit relies on SQLite's super-journal protocol.  Every
    database is a regular file in one local runtime directory, the main database
    is file-backed, and every schema uses DELETE (not WAL) journaling.
    """

    def __init__(self, data_directory: str | Path, modules: Mapping[str, Module] | None = None) -> None:
        self.data_directory = Path(data_directory).expanduser().resolve()
        self.data_directory.mkdir(parents=True, exist_ok=True)
        if not self.data_directory.is_dir():
            raise IndexError("index data path is not a directory")
        supplied = dict(modules or {})
        unknown = supplied.keys() - MODULE_DATABASE_NAMES.keys()
        if unknown:
            raise IndexError(f"unknown index modules: {', '.join(sorted(unknown))}")
        self.modules = {name: supplied.get(name, EmptyModule()) for name in MODULE_DATABASE_NAMES}
        self.module_health = {name: ("ok" if name in supplied else "inactive") for name in MODULE_DATABASE_NAMES}
        database_paths = [self.data_directory / COORDINATOR_DATABASE_NAME]
        database_paths.extend(self.data_directory / filename for filename in MODULE_DATABASE_NAMES.values())
        if any(path.is_symlink() for path in database_paths):
            raise IndexError("index databases must not be symbolic links")
        if any(path.exists() and not path.is_file() for path in database_paths):
            raise IndexError("index database path is not a regular file")
        if len({os.stat(path.parent).st_dev for path in database_paths}) != 1:
            raise IndexError("all index databases must be on one filesystem")
        self.connection = sqlite3.connect(database_paths[0])
        try:
            self._initialize()
        except BaseException:
            self.connection.close()
            raise

    def _initialize(self) -> None:
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self._configure_schema("main")
        for ordinal, (name, filename) in enumerate(MODULE_DATABASE_NAMES.items()):
            schema = f"module_{ordinal}"
            self.connection.execute(f"ATTACH DATABASE ? AS {schema}", (str(self.data_directory / filename),))
            self._configure_schema(schema)
        for ordinal, name in enumerate(MODULE_DATABASE_NAMES):
            schema = f"module_{ordinal}"
            self.connection.execute(
                f"CREATE TABLE IF NOT EXISTS {schema}.index_tip "
                "(singleton INTEGER PRIMARY KEY CHECK(singleton=1), height INTEGER NOT NULL, hash TEXT)"
            )
            self.connection.execute(
                f"INSERT OR IGNORE INTO {schema}.index_tip(singleton,height,hash) VALUES(1,-1,NULL)"
            )
            initialize = getattr(self.modules[name], "initialize", None)
            if initialize is not None:
                initialize(self.connection, schema)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS index_config "
            "(singleton INTEGER PRIMARY KEY CHECK(singleton=1), schema_version INTEGER NOT NULL, "
            "network TEXT, pebble_profile TEXT)"
        )
        config = self.connection.execute("SELECT schema_version FROM index_config WHERE singleton=1").fetchone()
        if config is None:
            self.connection.execute("INSERT INTO index_config VALUES(1,4,NULL,NULL)")
        elif config[0] == 2:
            self.connection.execute(
                "CREATE TABLE blocks_v3 (height INTEGER PRIMARY KEY, hash TEXT UNIQUE NOT NULL, previous_hash TEXT)"
            )
            self.connection.execute(
                "INSERT INTO blocks_v3(height,hash,previous_hash) SELECT height,hash,previous_hash FROM blocks"
            )
            self.connection.execute("DROP TABLE blocks")
            self.connection.execute("ALTER TABLE blocks_v3 RENAME TO blocks")
            self.connection.execute("ALTER TABLE index_config ADD COLUMN pebble_profile TEXT")
            self.connection.execute("UPDATE index_config SET schema_version=4 WHERE singleton=1")
        elif config[0] == 3:
            columns = {
                row[1] for row in self.connection.execute("PRAGMA table_info(index_config)")
            }
            if "pebble_profile" not in columns:
                self.connection.execute("ALTER TABLE index_config ADD COLUMN pebble_profile TEXT")
            self.connection.execute("UPDATE index_config SET schema_version=4 WHERE singleton=1")
        elif config[0] != 4:
            raise IndexError(f"incompatible coordinator schema version: {config[0]}")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS blocks "
            "(height INTEGER PRIMARY KEY, hash TEXT UNIQUE NOT NULL, previous_hash TEXT)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS chain_utxos (txid TEXT NOT NULL, vout INTEGER NOT NULL, "
            "value_bits INTEGER NOT NULL CHECK(value_bits>=0), script BLOB NOT NULL, address TEXT, "
            "PRIMARY KEY(txid,vout))"
        )
        initialize_undo(self.connection, "main", {"chain_utxos"})
        self.connection.commit()
        self._assert_common_tip()

    def _configure_schema(self, schema: str) -> None:
        mode = self.connection.execute(f"PRAGMA {schema}.journal_mode=DELETE").fetchone()[0]
        if str(mode).lower() != "delete":
            raise IndexError(f"{schema} database does not support DELETE journal mode")
        self.connection.execute(f"PRAGMA {schema}.synchronous=FULL")
        self.connection.execute(f"PRAGMA {schema}.fullfsync=ON")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "UnifiedIndex":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _tips(self) -> dict[str, tuple[int, str | None]]:
        return {
            name: tuple(self.connection.execute(f"SELECT height,hash FROM module_{ordinal}.index_tip").fetchone())
            for ordinal, name in enumerate(MODULE_DATABASE_NAMES)
        }

    def _assert_common_tip(self) -> tuple[int, str | None]:
        tips = self._tips()
        unique = set(tips.values())
        if len(unique) != 1:
            raise IndexError(f"module tips are not synchronized: {tips}")
        return unique.pop()

    @property
    def tip(self) -> tuple[int, str | None]:
        return self._assert_common_tip()

    def bind_network(self, network: str, pebble_profile: str = "auto") -> None:
        from quantums.networks import NETWORKS
        from .compatibility import resolve_index_profile

        if network not in NETWORKS:
            raise IndexError(f"unsupported Qbit network: {network}")
        try:
            profile = resolve_index_profile(network, pebble_profile)
        except ValueError as exc:
            raise IndexError(str(exc)) from exc
        current, current_profile = self.connection.execute(
            "SELECT network,pebble_profile FROM index_config WHERE singleton=1"
        ).fetchone()
        if current is None:
            current = network
        elif current != network:
            raise IndexError(f"index database is bound to {current}, not {network}")
        if current_profile is None:
            current_profile = profile
        elif current_profile != profile:
            raise IndexError(
                f"index database is bound to Pebble profile {current_profile}, not {profile}; "
                "rebuild into a fresh directory"
            )
        self.connection.execute(
            "UPDATE index_config SET network=?,pebble_profile=? WHERE singleton=1",
            (current, current_profile),
        )
        self.connection.commit()

    def connect_block(self, block: Mapping[str, Any]) -> None:
        self.connect_blocks((block,))

    def connect_blocks(self, blocks: tuple[Mapping[str, Any], ...]) -> None:
        if not blocks:
            return
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            for block in blocks:
                self._connect_block(block)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def _connect_block(self, block: Mapping[str, Any]) -> None:
        height, block_hash = block.get("height"), block.get("hash")
        previous_hash = block.get("previousblockhash")
        tip_height, tip_hash = self.tip
        if not isinstance(height, int) or not isinstance(block_hash, str):
            raise IndexError("block requires integer height and string hash")
        if height != tip_height + 1 or (tip_height >= 0 and previous_hash != tip_hash):
            raise IndexError("block does not extend the common index tip")
        block = self._resolve_prevouts(block)
        for ordinal, (name, module) in enumerate(self.modules.items()):
            schema = f"module_{ordinal}"
            module.connect(self.connection, schema, block)
            self.connection.execute(f"UPDATE {schema}.index_tip SET height=?,hash=? WHERE singleton=1", (height, block_hash))
        self.connection.execute(
            "INSERT INTO blocks(height,hash,previous_hash) VALUES(?,?,?)",
            (height, block_hash, previous_hash),
        )

    @staticmethod
    def _output_bits(output: Mapping[str, Any]) -> int:
        if "value_bits" in output:
            return int(output["value_bits"])
        return int(Decimal(str(output.get("value", 0))) * Decimal(100_000_000))

    @staticmethod
    def _output_script(output: Mapping[str, Any]) -> tuple[bytes, str | None, bool]:
        info = output.get("scriptPubKey", output.get("scriptpubkey", {}))
        if not isinstance(info, Mapping):
            return b"", None, False
        try:
            script = bytes.fromhex(str(info.get("hex", "")))
        except ValueError:
            script = b""
        address = info.get("address")
        if not isinstance(address, str):
            addresses = info.get("addresses")
            address = addresses[0] if isinstance(addresses, list) and len(addresses) == 1 else None
        unspendable = info.get("type") == "nulldata" or script.startswith(b"\x6a")
        return script, address, unspendable

    def _resolve_prevouts(self, source: Mapping[str, Any]) -> dict[str, Any]:
        block = deepcopy(dict(source))
        undo = UndoRecorder(self.connection, "main", int(block["height"]), str(block["hash"]))
        transactions = block.get("tx", [])
        if not isinstance(transactions, list):
            raise IndexError("decoded block has no transaction list")
        for transaction in transactions:
            txid = str(transaction.get("txid", "")).lower()
            if len(txid) != 64:
                raise IndexError("decoded transaction has no valid txid")
            for txin in transaction.get("vin", []):
                if "coinbase" in txin:
                    continue
                previous = {"txid": str(txin.get("txid", "")).lower(), "vout": int(txin.get("vout", -1))}
                row = self.connection.execute(
                    "SELECT * FROM chain_utxos WHERE txid=? AND vout=?",
                    (previous["txid"], previous["vout"]),
                ).fetchone()
                if row is None and "value_bits" in txin:
                    continue  # Explicitly resolved fixture input; live RPC never supplies this field.
                if row is None:
                    raise IndexError(f"missing chain prevout {previous['txid']}:{previous['vout']}")
                txin["value_bits"] = row["value_bits"]
                txin["prevout"] = {
                    "value_bits": row["value_bits"],
                    "scriptPubKey": {
                        "hex": bytes(row["script"]).hex(),
                        **({"address": row["address"]}
                           if row["address"] is not None else {}),
                    },
                }
                undo.delete("chain_utxos", previous)
            for fallback_vout, output in enumerate(transaction.get("vout", [])):
                vout = int(output.get("n", fallback_vout))
                value = self._output_bits(output)
                output["value_bits"] = value
                script, address, unspendable = self._output_script(output)
                if not unspendable:
                    undo.insert("chain_utxos", {
                        "txid": txid, "vout": vout, "value_bits": value,
                        "script": script, "address": address,
                    }, {"txid": txid, "vout": vout})
        return block

    def disconnect_tip(self) -> None:
        height, block_hash = self.tip
        if height < 0:
            raise IndexError("cannot disconnect an empty index")
        row = self.connection.execute("SELECT previous_hash FROM blocks WHERE height=?", (height,)).fetchone()
        if row is None:
            raise IndexError("tip block undo record is missing")
        block = {"height": height, "hash": block_hash, "previousblockhash": row["previous_hash"]}
        previous_hash = row["previous_hash"]
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            for ordinal, (name, module) in reversed(tuple(enumerate(self.modules.items()))):
                schema = f"module_{ordinal}"
                module.disconnect(self.connection, schema, block)
                self.connection.execute(
                    f"UPDATE {schema}.index_tip SET height=?,hash=? WHERE singleton=1",
                    (height - 1, previous_hash),
                )
            apply_undo(self.connection, "main", height, block_hash)
            self.connection.execute("DELETE FROM blocks WHERE height=? AND hash=?", (height, block_hash))
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def status(self, node_height: int) -> IndexStatus:
        height, block_hash = self.tip
        progress = 1.0 if node_height <= 0 else min(1.0, max(0.0, (height + 1) / (node_height + 1)))
        return IndexStatus(node_height, height, block_hash, progress, dict(self.module_health))

    def rebuild(self) -> None:
        while self.tip[0] >= 0:
            self.disconnect_tip()


def follow(rpc: Any, index: UnifiedIndex, stop: Event, *, poll_seconds: float = 1.0, commit_batch: int = 100,
           on_status: Callable[[IndexStatus], None] | None = None) -> None:
    """Follow Qbit until stopped, resolving ordinary reorganizations by hash."""

    if commit_batch <= 0:
        raise ValueError("commit batch must be positive")

    while not stop.is_set():
        node_height = int(rpc.call("getblockchaininfo")["blocks"])
        while index.tip[0] >= 0 and (
            index.tip[0] > node_height or rpc.call("getblockhash", index.tip[0]) != index.tip[1]
        ):
            index.disconnect_tip()
        while index.tip[0] < node_height and not stop.is_set():
            pending = []
            first_height = index.tip[0] + 1
            final_height = min(node_height, first_height + commit_batch - 1)
            for next_height in range(first_height, final_height + 1):
                if stop.is_set():
                    break
                block_hash = rpc.call("getblockhash", next_height)
                block = rpc.call("getblock", block_hash, 2)
                stats = rpc.call("getblockstats", next_height, ["subsidy"])
                block["subsidy_bits"] = int(stats["subsidy"])
                pending.append(block)
            index.connect_blocks(tuple(pending))
        if on_status is not None:
            on_status(index.status(node_height))
        stop.wait(poll_seconds)
