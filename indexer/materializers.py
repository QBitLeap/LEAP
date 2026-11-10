"""Operational SQLite materializers for settled Leap artifact families."""

from __future__ import annotations

from decimal import Decimal
import json
import sqlite3
from typing import Any, Iterable, Mapping

from quantums.envelope import EnvelopeError
from quantums.gallery import GalleryRoot, GalleryUpdate, apply as apply_gallery, decode as decode_gallery

from .arbitrary import ArtifactPosition, bind_qpet_v1, recognize_qpet_v1
from .arbitrary_data import inventory_transaction
from .discovery import discover_block
from .quantum import Block, Location, OutPoint, QuantumIndexer, Transaction, TxInput, TxOutput
from .undo import UndoRecorder, apply_undo, initialize_undo


SCHEMA_VERSION = 2
LOCATION_QUERY_PAIRS = 400


def _initialize(connection: sqlite3.Connection, schema: str, module: str, ddl: Iterable[str]) -> None:
    connection.execute(
        f"CREATE TABLE IF NOT EXISTS {schema}.schema_info "
        "(singleton INTEGER PRIMARY KEY CHECK(singleton=1), module TEXT NOT NULL, version INTEGER NOT NULL)"
    )
    row = connection.execute(f"SELECT module,version FROM {schema}.schema_info WHERE singleton=1").fetchone()
    if row is None:
        connection.execute(
            f"INSERT INTO {schema}.schema_info VALUES(1,?,?)", (module, SCHEMA_VERSION)
        )
    elif tuple(row) != (module, SCHEMA_VERSION):
        raise RuntimeError(f"incompatible {module} database schema: {tuple(row)}")
    for statement in ddl:
        connection.execute(statement.format(schema=schema))


def _bits(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return int(Decimal(str(value)) * Decimal(100_000_000))


def _script(output: Mapping[str, Any]) -> tuple[bytes, str | None]:
    info = output.get("scriptPubKey", output.get("scriptpubkey", {}))
    if not isinstance(info, Mapping):
        return b"", None
    try:
        raw = bytes.fromhex(str(info.get("hex", "")))
    except ValueError:
        raw = b""
    address = info.get("address")
    if not isinstance(address, str):
        addresses = info.get("addresses")
        address = addresses[0] if isinstance(addresses, list) and len(addresses) == 1 else None
    return raw, address


def _transaction(raw: Mapping[str, Any]) -> Transaction:
    inputs = []
    for txin in raw.get("vin", []):
        if "coinbase" in txin:
            continue
        prevout = txin.get("prevout", {})
        if "value_bits" in txin:
            value = int(txin["value_bits"])
        elif isinstance(prevout, Mapping) and "value" in prevout:
            value = _bits(prevout["value"])
        else:
            raise RuntimeError("decoded block lacks input prevout values; RPC verbosity 3 is required")
        inputs.append(TxInput(OutPoint(str(txin["txid"]).lower(), int(txin["vout"])), value))
    outputs = []
    for output in raw.get("vout", []):
        script, address = _script(output)
        value = int(output["value_bits"]) if "value_bits" in output else _bits(output.get("value", 0))
        outputs.append(TxOutput(value, script, address))
    return Transaction(str(raw["txid"]).lower(), tuple(inputs), tuple(outputs))


def normalized_block(raw: Mapping[str, Any]) -> Block:
    transactions = raw.get("tx")
    if not isinstance(transactions, list) or not transactions:
        raise RuntimeError("decoded block has no transactions")
    coinbase = _transaction(transactions[0])
    ordinary = tuple(_transaction(tx) for tx in transactions[1:])
    subsidy = raw.get("subsidy_bits")
    if not isinstance(subsidy, int):
        raise RuntimeError("decoded block requires subsidy_bits from getblockstats")
    return Block(
        int(raw["height"]), str(raw["hash"]), raw.get("previousblockhash"), subsidy,
        coinbase, ordinary,
    )


def _spent_outpoints(raw: Mapping[str, Any]) -> tuple[tuple[str, int], ...]:
    """Return the distinct non-coinbase outpoints consumed by one block."""

    spent: set[tuple[str, int]] = set()
    transactions = raw.get("tx")
    if not isinstance(transactions, list):
        raise RuntimeError("decoded block has no transaction list")
    for transaction in transactions:
        if not isinstance(transaction, Mapping):
            raise RuntimeError("decoded transaction shape is invalid")
        inputs = transaction.get("vin", [])
        if not isinstance(inputs, list):
            raise RuntimeError("decoded transaction has no input list")
        for txin in inputs:
            if not isinstance(txin, Mapping):
                raise RuntimeError("decoded transaction input shape is invalid")
            if "coinbase" in txin:
                continue
            txid = str(txin.get("txid", "")).lower()
            vout = txin.get("vout")
            if len(txid) != 64 or type(vout) is not int or vout < 0:
                raise RuntimeError("decoded transaction input outpoint is invalid")
            spent.add((txid, vout))
    return tuple(sorted(spent))


def _spent_location_rows(
    connection: sqlite3.Connection, schema: str, table: str,
    spent: tuple[tuple[str, int], ...],
) -> tuple[sqlite3.Row, ...]:
    """Load only live artifacts whose containing output this block spends."""

    rows: list[sqlite3.Row] = []
    for start in range(0, len(spent), LOCATION_QUERY_PAIRS):
        chunk = spent[start:start + LOCATION_QUERY_PAIRS]
        pairs = ",".join("(?,?)" for _ in chunk)
        parameters = tuple(value for outpoint in chunk for value in outpoint)
        rows.extend(connection.execute(
            f"SELECT * FROM {schema}.{table} "
            f"WHERE (current_txid,current_vout) IN (VALUES {pairs})",
            parameters,
        ))
    return tuple(rows)


class QuantumMaterializer:
    def initialize(self, connection: sqlite3.Connection, schema: str) -> None:
        _initialize(connection, schema, "quantums", (
            "CREATE TABLE IF NOT EXISTS {schema}.quantums ("
            "quantum_id TEXT PRIMARY KEY, creation_height INTEGER NOT NULL, creation_txid TEXT NOT NULL, "
            "creation_vout INTEGER NOT NULL, data_vout INTEGER NOT NULL, envelope_index INTEGER NOT NULL, "
            "name TEXT NOT NULL, content_type TEXT NOT NULL, content_sha256 TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS {schema}.quantum_locations ("
            "quantum_id TEXT PRIMARY KEY REFERENCES quantums(quantum_id), "
            "current_txid TEXT NOT NULL, current_vout INTEGER NOT NULL, current_offset INTEGER NOT NULL, current_value INTEGER NOT NULL, "
            "current_script BLOB NOT NULL, current_address TEXT)",
            "CREATE INDEX IF NOT EXISTS {schema}.quantum_locations_outpoint "
            "ON quantum_locations(current_txid,current_vout)",
        ))
        initialize_undo(connection, schema, {"quantums", "quantum_locations"})

    def connect(self, connection: sqlite3.Connection, schema: str, raw: Mapping[str, Any]) -> None:
        height, block_hash = int(raw["height"]), str(raw["hash"])
        undo = UndoRecorder(connection, schema, height, block_hash)
        tracker = QuantumIndexer()
        for row in _spent_location_rows(
            connection, schema, "quantum_locations", _spent_outpoints(raw)
        ):
            tracker.add(
                row["quantum_id"],
                Location(OutPoint(row["current_txid"], row["current_vout"]), row["current_offset"]),
                address=row["current_address"],
            )
        tracker.tip_height = height - 1
        tracker.tip_hash = raw.get("previousblockhash")
        discovered = discover_block(dict(raw))
        transactions = raw["tx"]
        for item in discovered:
            tx = _transaction(transactions[item.transaction_index])
            output = tx.outputs[item.artifact.ownership_output]
            tracker.add(item.quantum_id, Location(OutPoint(tx.txid, item.artifact.ownership_output), 0), address=output.address)
            undo.insert("quantums", {
                "quantum_id": item.quantum_id, "creation_height": item.height,
                "creation_txid": tx.txid, "creation_vout": item.artifact.ownership_output,
                "data_vout": item.data_output, "envelope_index": item.envelope_index,
                "name": item.artifact.name, "content_type": item.artifact.content_type,
                "content_sha256": item.artifact.content_sha256,
            }, {"quantum_id": item.quantum_id})
            undo.insert("quantum_locations", {
                "quantum_id": item.quantum_id, "current_txid": tx.txid,
                "current_vout": item.artifact.ownership_output, "current_offset": 0,
                "current_value": output.value, "current_script": output.script,
                "current_address": output.address,
            }, {"quantum_id": item.quantum_id})
        block = normalized_block(raw)
        tracker.connect(block)
        # The immutable identity remains in quantums. A fee bit omitted from
        # coinbase outputs has no spendable location and cannot authorize use.
        for quantum_id in sorted(tracker.burned):
            undo.delete("quantum_locations", {"quantum_id": quantum_id})
        transactions_by_id = {
            tx.txid: tx for tx in block.transactions + (block.coinbase,)
        }
        for quantum_id, location in tracker.locations.items():
            transaction = transactions_by_id.get(location.outpoint.txid)
            current = connection.execute(f"SELECT current_value,current_script FROM {schema}.quantum_locations WHERE quantum_id=?", (quantum_id,)).fetchone()
            value, script = current["current_value"], current["current_script"]
            if transaction is not None:
                script = transaction.outputs[location.outpoint.vout].script
                value = transaction.outputs[location.outpoint.vout].value
            undo.update("quantum_locations", {"quantum_id": quantum_id}, {
                "current_txid": location.outpoint.txid, "current_vout": location.outpoint.vout,
                "current_offset": location.offset, "current_value": value, "current_script": script,
                "current_address": tracker.addresses.get(quantum_id),
            })

    def disconnect(self, connection: sqlite3.Connection, schema: str, raw: Mapping[str, Any]) -> None:
        apply_undo(connection, schema, raw["height"], raw["hash"])


class GalleryMaterializer:
    def initialize(self, connection: sqlite3.Connection, schema: str) -> None:
        _initialize(connection, schema, "galleries", (
            "CREATE TABLE IF NOT EXISTS {schema}.gallery_roots (root_id TEXT PRIMARY KEY, name TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS {schema}.gallery_state (root_id TEXT PRIMARY KEY REFERENCES gallery_roots(root_id), current_id TEXT NOT NULL, members TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS {schema}.gallery_history (quantum_id TEXT PRIMARY KEY, root_id TEXT NOT NULL, previous_id TEXT, height INTEGER NOT NULL, record BLOB NOT NULL)",
        ))
        initialize_undo(connection, schema, {"gallery_roots", "gallery_state", "gallery_history"})

    def connect(self, connection: sqlite3.Connection, schema: str, raw: Mapping[str, Any]) -> None:
        undo_log = UndoRecorder(connection, schema, raw["height"], raw["hash"])
        for item in discover_block(dict(raw)):
            if item.artifact.content_type != "application/x-leap-gallery":
                continue
            try:
                record = decode_gallery(item.artifact.content)
            except EnvelopeError:
                continue
            if isinstance(record, GalleryRoot):
                undo_log.insert("gallery_roots", {"root_id": item.quantum_id, "name": record.name}, {"root_id": item.quantum_id})
                undo_log.insert("gallery_state", {"root_id": item.quantum_id, "current_id": item.quantum_id, "members": json.dumps(record.members)}, {"root_id": item.quantum_id})
                undo_log.insert("gallery_history", {"quantum_id": item.quantum_id, "root_id": item.quantum_id,
                                                     "previous_id": None, "height": raw["height"],
                                                     "record": item.artifact.content}, {"quantum_id": item.quantum_id})
            else:
                row = connection.execute(f"SELECT * FROM {schema}.gallery_state WHERE root_id=?", (record.root,)).fetchone()
                if row is None or row["current_id"] != record.previous:
                    continue
                previous = None
                for mutation in connection.execute(
                    "SELECT primary_key,previous_row FROM module_0.undo_mutations "
                    "WHERE height=? AND table_name='quantum_locations' AND action='update' ORDER BY sequence",
                    (raw["height"],),
                ):
                    if json.loads(mutation["primary_key"]).get("quantum_id") == record.previous:
                        previous = json.loads(mutation["previous_row"])
                        if isinstance(previous.get("current_script"), dict):
                            previous["current_script"] = bytes.fromhex(previous["current_script"]["__bytes__"])
                        break
                tx_raw = raw["tx"][item.transaction_index]
                vin = tx_raw.get("vin", [])
                tx = _transaction(tx_raw)
                if (
                    previous is None or item.artifact.ownership_output != 0 or not vin
                    or vin[0].get("txid", "").lower() != previous["current_txid"]
                    or int(vin[0].get("vout", -1)) != previous["current_vout"]
                    or not tx.outputs or tx.outputs[0].script != previous["current_script"]
                    or tx.outputs[0].value != previous["current_value"]
                ):
                    continue
                members = apply_gallery(tuple(json.loads(row["members"])), record)
                undo_log.update("gallery_state", {"root_id": record.root}, {"current_id": item.quantum_id, "members": json.dumps(members)})
                undo_log.insert("gallery_history", {"quantum_id": item.quantum_id, "root_id": record.root,
                                                     "previous_id": record.previous, "height": raw["height"],
                                                     "record": item.artifact.content}, {"quantum_id": item.quantum_id})

    def disconnect(self, connection: sqlite3.Connection, schema: str, raw: Mapping[str, Any]) -> None:
        apply_undo(connection, schema, raw["height"], raw["hash"])


class ArbitraryMaterializer:
    def initialize(self, connection: sqlite3.Connection, schema: str) -> None:
        _initialize(connection, schema, "arbitraries", (
            "CREATE TABLE IF NOT EXISTS {schema}.arbitraries ("
            "number INTEGER PRIMARY KEY, artifact_id TEXT UNIQUE NOT NULL, height INTEGER NOT NULL, "
            "transaction_index INTEGER NOT NULL, output_index INTEGER NOT NULL, record_index INTEGER NOT NULL, "
            "txid TEXT NOT NULL, format TEXT NOT NULL, name TEXT, payload_sha256 TEXT NOT NULL, "
            "binding_status TEXT NOT NULL, bound_output INTEGER)",
            "CREATE TABLE IF NOT EXISTS {schema}.arbitrary_locations ("
            "artifact_id TEXT PRIMARY KEY REFERENCES arbitraries(artifact_id), current_txid TEXT NOT NULL, current_vout INTEGER NOT NULL, "
            "current_offset INTEGER NOT NULL, current_script BLOB NOT NULL, current_address TEXT)",
            "CREATE INDEX IF NOT EXISTS {schema}.arbitrary_locations_outpoint "
            "ON arbitrary_locations(current_txid,current_vout)",
        ))
        initialize_undo(connection, schema, {"arbitraries", "arbitrary_locations"})

    def connect(self, connection: sqlite3.Connection, schema: str, raw: Mapping[str, Any]) -> None:
        import hashlib
        undo = UndoRecorder(connection, schema, raw["height"], raw["hash"])
        tracker = QuantumIndexer()
        for row in _spent_location_rows(
            connection, schema, "arbitrary_locations", _spent_outpoints(raw)
        ):
            tracker.add(row["artifact_id"], Location(OutPoint(row["current_txid"], row["current_vout"]), row["current_offset"]), address=row["current_address"])
        tracker.tip_height = int(raw["height"]) - 1
        tracker.tip_hash = raw.get("previousblockhash")
        next_number = connection.execute(f"SELECT coalesce(max(number),0)+1 FROM {schema}.arbitraries").fetchone()[0]
        for transaction_index, tx_raw in enumerate(raw["tx"]):
            tx = _transaction(tx_raw)
            for item in inventory_transaction(tx_raw):
                record = recognize_qpet_v1(item.payload, position=ArtifactPosition(raw["height"], transaction_index, item.vout), txid=tx.txid)
                if record is None:
                    continue
                record = bind_qpet_v1(record, tx)
                artifact_id = f"arbitrary:{next_number}"
                location = None
                script = None
                address = None
                if record.bound_output is not None:
                    output = tx.outputs[record.bound_output]
                    location = Location(OutPoint(tx.txid, record.bound_output), 0)
                    tracker.add(artifact_id, location, address=output.address)
                    script, address = output.script, output.address
                undo.insert("arbitraries", {
                    "number": next_number, "artifact_id": artifact_id, "height": raw["height"],
                    "transaction_index": transaction_index, "output_index": item.vout, "record_index": 0,
                    "txid": tx.txid, "format": record.format, "name": record.name,
                    "payload_sha256": hashlib.sha256(item.payload).hexdigest(),
                    "binding_status": record.binding_status, "bound_output": record.bound_output,
                }, {"number": next_number})
                if location is not None:
                    undo.insert("arbitrary_locations", {
                        "artifact_id": artifact_id, "current_txid": location.outpoint.txid,
                        "current_vout": location.outpoint.vout, "current_offset": location.offset,
                        "current_script": script, "current_address": address,
                    }, {"artifact_id": artifact_id})
                next_number += 1
        block = normalized_block(raw)
        tracker.connect(block)
        transactions = {tx.txid: tx for tx in block.transactions + (block.coinbase,)}
        for artifact_id in sorted(tracker.burned):
            undo.delete("arbitrary_locations", {"artifact_id": artifact_id})
        for artifact_id, location in tracker.locations.items():
            tx = transactions.get(location.outpoint.txid)
            output = tx.outputs[location.outpoint.vout] if tx else None
            previous_script = connection.execute(
                f"SELECT current_script FROM {schema}.arbitrary_locations WHERE artifact_id=?", (artifact_id,)
            ).fetchone()[0]
            undo.update("arbitrary_locations", {"artifact_id": artifact_id}, {
                "current_txid": location.outpoint.txid, "current_vout": location.outpoint.vout,
                "current_offset": location.offset, "current_script": output.script if output else previous_script,
                "current_address": tracker.addresses.get(artifact_id),
            })

    def disconnect(self, connection: sqlite3.Connection, schema: str, raw: Mapping[str, Any]) -> None:
        apply_undo(connection, schema, raw["height"], raw["hash"])


def operational_modules(
    network: str | None = None, *, pebble_profile: str = "off",
) -> dict[str, Any]:
    from .compatibility import resolve_index_profile

    resolve_index_profile(network, pebble_profile)
    return {
        "quantums": QuantumMaterializer(),
        "galleries": GalleryMaterializer(),
        "arbitraries": ArbitraryMaterializer(),
    }
