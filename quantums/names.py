"""Network-scoped creation-name policy shared by LEAP clients.

This is a builder/publication policy, not a consensus rule. Historical records
and raw transactions remain decodable even when they contain duplicate names.
"""
from contextlib import closing
from pathlib import Path
import sqlite3
import json
import os
import time

from .envelope import MAX_NAME_BYTES
from .networks import NETWORKS


def validate_name(name: str) -> None:
    if not isinstance(name, str) or not name.strip() or any(c in name for c in "\r\n\x00"):
        raise ValueError("Quantum Name must be non-blank text")
    try:
        size = len(name.encode("utf-8", errors="strict"))
    except UnicodeEncodeError as exc:
        raise ValueError("Quantum Name must be valid UTF-8 text") from exc
    if size > MAX_NAME_BYTES:
        raise ValueError(f"Quantum Name must fit within {MAX_NAME_BYTES} UTF-8 bytes")


def next_name(requested: str, occupied: set[str]) -> str:
    """Keep an unused exact name, otherwise choose the first free suffix >= 2."""
    validate_name(requested)
    if requested not in occupied:
        return requested
    number = 2
    while f"{requested} {number}" in occupied:
        number += 1
    candidate = f"{requested} {number}"
    try:
        validate_name(candidate)
    except ValueError as exc:
        raise ValueError("This name is already used and its numbered version is too long. Choose a shorter Quantum Name") from exc
    return candidate


def used_names(index_dir: Path, network: str, requested: list[str], *, exclude_txids=()) -> set[str]:
    if network not in NETWORKS:
        raise ValueError("Unknown naming network")
    for name in requested:
        validate_name(name)
    directory = Path(index_dir).expanduser().resolve()
    excluded = set(exclude_txids)
    with closing(sqlite3.connect((directory / "leap-index.sqlite3").as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
        db.execute("ATTACH DATABASE ? AS quantum", ((directory / "quantum.sqlite3").as_uri() + "?mode=ro",))
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        configured = db.execute("SELECT network FROM index_config WHERE singleton=1").fetchone()
        tip = db.execute("SELECT height,hash FROM blocks ORDER BY height DESC LIMIT 1").fetchone()
        module_tip = db.execute("SELECT height,hash FROM quantum.index_tip WHERE singleton=1").fetchone()
        if configured != (network,) or not tip or tip != module_tip:
            raise ValueError("LEAP naming index has the wrong network or its tips do not match")
        occupied = set()
        for name in dict.fromkeys(requested):
            prefix = name + " "
            rows = db.execute("SELECT quantum_id,name FROM quantum.quantums WHERE name=? OR substr(name,1,?)=?", (name, len(prefix), prefix))
            for quantum_id, existing in rows:
                if quantum_id.rsplit(":", 1)[0] not in excluded:
                    occupied.add(existing)
        return occupied


def require_available(index_dir: Path, network: str, names: list[str], *, exclude_txids=(), rpc=None, maximum=None, cache_dir=None) -> None:
    occupied = used_names(index_dir, network, names, exclude_txids=exclude_txids)
    if rpc is not None:
        occupied.update(pending_names(rpc, names, network=network, exclude_txids=exclude_txids, maximum=maximum, cache_dir=cache_dir))
    require_unused(names, occupied, network)


def require_unused(names, occupied, network):
    occupied = set(occupied)
    seen = set()
    for name in names:
        candidate = next_name(name, occupied)
        if candidate != name:
            candidate = next_name(name, occupied | set(names))
            location = "in this creation plan" if name in seen else f"on {network}"
            raise ValueError(f'Quantum Name "{name}" is already used {location}. Next available name: "{candidate}". Review the new name before signing or publishing')
        occupied.add(name)
        seen.add(name)


class PendingNameCatalog:
    """Rebuildable advisory cache, separate from canonical index databases."""
    def __init__(self, directory):
        self.directory = Path(directory)

    def connect(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self.directory / "pending-names.sqlite3"
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        db = sqlite3.connect(path, timeout=5)
        db.execute("CREATE TABLE IF NOT EXISTS pending_names(network TEXT NOT NULL,txid TEXT NOT NULL,names TEXT NOT NULL,first_seen INTEGER NOT NULL,PRIMARY KEY(network,txid))")
        db.commit()
        return db

    def read(self, network):
        with closing(self.connect()) as db:
            return {txid: json.loads(names) for txid, names in db.execute("SELECT txid,names FROM pending_names WHERE network=?", (network,))}

    def remember(self, network, txid, names):
        """Register an accepted local publication without fetching it again."""
        if network not in NETWORKS:
            raise ValueError("Unknown naming network")
        for name in names:
            validate_name(name)
        with closing(self.connect()) as db:
            db.execute("INSERT OR IGNORE INTO pending_names VALUES(?,?,?,?)", (network, txid, json.dumps(list(names)), int(time.time())))
            db.commit()

    def refresh(self, network, current, newly_seen):
        with closing(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = {row[0] for row in db.execute("SELECT txid FROM pending_names WHERE network=?", (network,))}
            db.executemany("DELETE FROM pending_names WHERE network=? AND txid=?", [(network, txid) for txid in previous - current])
            db.executemany("INSERT OR IGNORE INTO pending_names VALUES(?,?,?,?)", [(network, txid, json.dumps(names), int(time.time())) for txid, names in newly_seen.items()])
            db.commit()


def pending_names(rpc, requested: list[str], *, network: str, exclude_txids=(), maximum=None, cache_dir=None) -> set[str]:
    """Refresh mempool IDs; decode only unseen transactions in the catalog."""
    from indexer.discovery import discover_transaction
    if network not in NETWORKS:
        raise ValueError("Unknown naming network")
    txids = rpc.call("getrawmempool")
    if not isinstance(txids, list) or (maximum is not None and len(txids) > maximum):
        raise ValueError("The mempool is too busy for the naming check; retry later")
    catalog = PendingNameCatalog(cache_dir) if cache_dir is not None else None
    cached = catalog.read(network) if catalog else {}
    excluded = set(exclude_txids)
    occupied, newly_seen = set(), {}
    for txid in txids:
        if txid in excluded:
            continue
        if txid in cached:
            names = cached[txid]
        else:
            transaction = rpc.call("getrawtransaction", txid, True)
            names = [item.artifact.name for item in discover_transaction(transaction, height=0, transaction_index=0)]
            newly_seen[txid] = names
        for name in names:
            if any(name == base or name.startswith(base + " ") for base in requested):
                occupied.add(name)
    if set(rpc.call("getrawmempool")) != set(txids):
        raise ValueError("The mempool changed during the naming check; review again")
    if catalog:
        catalog.refresh(network, set(txids), newly_seen)
    return occupied
