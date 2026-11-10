"""Incremental, validated inverse-row mutations for Leap module databases."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Mapping


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")


def _name(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise RuntimeError(f"unsafe SQLite identifier: {value!r}")
    return value


def _json_value(value: Any) -> Any:
    return {"__bytes__": value.hex()} if isinstance(value, bytes) else value


def _encode(row: Mapping[str, Any] | None) -> str | None:
    if row is None:
        return None
    return json.dumps({key: _json_value(value) for key, value in row.items()}, sort_keys=True, separators=(",", ":"))


def _decode(raw: str | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    row = json.loads(raw)
    for key, value in row.items():
        if isinstance(value, dict) and set(value) == {"__bytes__"}:
            row[key] = bytes.fromhex(value["__bytes__"])
    return row


def initialize_undo(connection: sqlite3.Connection, schema: str, allowed_tables: set[str]) -> None:
    schema = _name(schema)
    allowed = tuple(sorted(_name(table) for table in allowed_tables))
    if not allowed:
        raise RuntimeError("undo must permit at least one state table")
    connection.execute(f"CREATE TABLE IF NOT EXISTS {schema}.undo_tables (table_name TEXT PRIMARY KEY)")
    existing = tuple(row[0] for row in connection.execute(f"SELECT table_name FROM {schema}.undo_tables ORDER BY table_name"))
    if not existing:
        connection.executemany(f"INSERT INTO {schema}.undo_tables VALUES(?)", ((table,) for table in allowed))
    elif existing != allowed:
        raise RuntimeError(f"undo table registry is incompatible: {existing}")
    connection.execute(
        f"CREATE TABLE IF NOT EXISTS {schema}.block_undo ("
        "height INTEGER PRIMARY KEY, hash TEXT NOT NULL UNIQUE, mutation_count INTEGER NOT NULL DEFAULT 0 CHECK(mutation_count>=0))"
    )
    connection.execute(
        f"CREATE TABLE IF NOT EXISTS {schema}.undo_mutations ("
        "height INTEGER NOT NULL, sequence INTEGER NOT NULL CHECK(sequence>=0), "
        "action TEXT NOT NULL CHECK(action IN ('delete','update','insert')), "
        "table_name TEXT NOT NULL, primary_key TEXT NOT NULL, previous_row TEXT, expected_row TEXT, "
        "PRIMARY KEY(height,sequence), FOREIGN KEY(height) REFERENCES block_undo(height) ON DELETE CASCADE, "
        "FOREIGN KEY(table_name) REFERENCES undo_tables(table_name), "
        "CHECK((action='delete' AND previous_row IS NULL AND expected_row IS NOT NULL) OR "
        "(action='update' AND previous_row IS NOT NULL AND expected_row IS NOT NULL) OR "
        "(action='insert' AND previous_row IS NOT NULL AND expected_row IS NULL)))"
    )


def _where(primary_key: Mapping[str, Any]) -> tuple[str, tuple[Any, ...]]:
    if not primary_key:
        raise RuntimeError("undo primary key cannot be empty")
    columns = tuple(sorted(primary_key))
    return " AND ".join(f"{_name(column)}=?" for column in columns), tuple(primary_key[column] for column in columns)


def _one(connection: sqlite3.Connection, schema: str, table: str, primary_key: Mapping[str, Any]) -> dict[str, Any] | None:
    where, values = _where(primary_key)
    row = connection.execute(f"SELECT * FROM {_name(schema)}.{_name(table)} WHERE {where}", values).fetchone()
    return None if row is None else dict(row)


class UndoRecorder:
    def __init__(self, connection: sqlite3.Connection, schema: str, height: int, block_hash: str) -> None:
        self.connection, self.schema, self.height = connection, _name(schema), height
        self.allowed_tables = {row[0] for row in connection.execute(f"SELECT table_name FROM {self.schema}.undo_tables")}
        self.sequence = 0
        connection.execute(f"INSERT INTO {self.schema}.block_undo(height,hash) VALUES(?,?)", (height, block_hash))

    def _record(self, action: str, table: str, primary_key: Mapping[str, Any], previous: Mapping[str, Any] | None,
                expected: Mapping[str, Any] | None) -> None:
        if table not in self.allowed_tables:
            raise RuntimeError(f"table {table!r} is not registered for undo")
        self.connection.execute(
            f"INSERT INTO {self.schema}.undo_mutations VALUES(?,?,?,?,?,?,?)",
            (self.height, self.sequence, action, _name(table), _encode(primary_key), _encode(previous), _encode(expected)),
        )
        self.sequence += 1
        changed = self.connection.execute(
            f"UPDATE {self.schema}.block_undo SET mutation_count=? WHERE height=?",
            (self.sequence, self.height),
        ).rowcount
        if changed != 1:
            raise RuntimeError("undo block metadata disappeared")

    def insert(self, table: str, row: Mapping[str, Any], primary_key: Mapping[str, Any]) -> None:
        columns = tuple(row)
        placeholders = ",".join("?" for _ in columns)
        self.connection.execute(
            f"INSERT INTO {self.schema}.{_name(table)} ({','.join(_name(column) for column in columns)}) VALUES ({placeholders})",
            tuple(row[column] for column in columns),
        )
        inserted = _one(self.connection, self.schema, table, primary_key)
        if inserted is None:
            raise RuntimeError("inserted undo target is missing")
        self._record("delete", table, primary_key, None, inserted)

    def update(self, table: str, primary_key: Mapping[str, Any], changes: Mapping[str, Any]) -> bool:
        previous = _one(self.connection, self.schema, table, primary_key)
        if previous is None:
            raise RuntimeError(f"cannot update missing {table} row")
        actual_changes = {key: value for key, value in changes.items() if previous.get(key) != value}
        if not actual_changes:
            return False
        where, values = _where(primary_key)
        assignments = ",".join(f"{_name(column)}=?" for column in actual_changes)
        changed = self.connection.execute(
            f"UPDATE {self.schema}.{_name(table)} SET {assignments} WHERE {where}",
            tuple(actual_changes.values()) + values,
        ).rowcount
        if changed != 1:
            raise RuntimeError("undo update did not affect exactly one row")
        expected = _one(self.connection, self.schema, table, primary_key)
        self._record("update", table, primary_key, previous, expected)
        return True

    def delete(self, table: str, primary_key: Mapping[str, Any]) -> None:
        previous = _one(self.connection, self.schema, table, primary_key)
        if previous is None:
            raise RuntimeError(f"cannot delete missing {table} row")
        where, values = _where(primary_key)
        if self.connection.execute(f"DELETE FROM {self.schema}.{_name(table)} WHERE {where}", values).rowcount != 1:
            raise RuntimeError("undo delete did not affect exactly one row")
        self._record("insert", table, primary_key, previous, None)


def apply_undo(connection: sqlite3.Connection, schema: str, height: int, block_hash: str) -> None:
    schema = _name(schema)
    allowed_tables = {row[0] for row in connection.execute(f"SELECT table_name FROM {schema}.undo_tables")}
    block = connection.execute(
        f"SELECT hash,mutation_count FROM {schema}.block_undo WHERE height=?", (height,)
    ).fetchone()
    if block is None or block["hash"] != block_hash:
        raise RuntimeError("undo record does not match the current tip")
    rows = connection.execute(
        f"SELECT * FROM {schema}.undo_mutations WHERE height=? ORDER BY sequence", (height,)
    ).fetchall()
    if len(rows) != block["mutation_count"] or [row["sequence"] for row in rows] != list(range(len(rows))):
        raise RuntimeError("undo mutation sequence is missing, duplicated, or out of order")
    for mutation in reversed(rows):
        table = _name(mutation["table_name"])
        if table not in allowed_tables:
            raise RuntimeError("undo mutation targets an unregistered table")
        primary_key = _decode(mutation["primary_key"])
        previous = _decode(mutation["previous_row"])
        expected = _decode(mutation["expected_row"])
        assert primary_key is not None
        current = _one(connection, schema, table, primary_key)
        if mutation["action"] == "delete":
            if current != expected:
                raise RuntimeError("inserted row no longer matches its inverse delete")
            where, values = _where(primary_key)
            if connection.execute(f"DELETE FROM {schema}.{table} WHERE {where}", values).rowcount != 1:
                raise RuntimeError("inverse delete failed")
        elif mutation["action"] == "update":
            if current != expected or previous is None:
                raise RuntimeError("updated row no longer matches recorded undo state")
            columns = tuple(previous)
            where, values = _where(primary_key)
            assignments = ",".join(f"{_name(column)}=?" for column in columns)
            if connection.execute(
                f"UPDATE {schema}.{table} SET {assignments} WHERE {where}",
                tuple(previous[column] for column in columns) + values,
            ).rowcount != 1:
                raise RuntimeError("inverse update failed")
        elif mutation["action"] == "insert":
            if current is not None or previous is None:
                raise RuntimeError("deleted row cannot be restored safely")
            columns = tuple(previous)
            connection.execute(
                f"INSERT INTO {schema}.{table} ({','.join(_name(column) for column in columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})",
                tuple(previous[column] for column in columns),
            )
        else:
            raise RuntimeError("unknown undo mutation action")
    connection.execute(f"DELETE FROM {schema}.undo_mutations WHERE height=?", (height,))
    if connection.execute(f"DELETE FROM {schema}.block_undo WHERE height=? AND hash=?", (height, block_hash)).rowcount != 1:
        raise RuntimeError("consumed undo block metadata is missing")
