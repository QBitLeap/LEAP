"""Standalone read-only indexer runtime; no website or wallet integration."""

import argparse
import base64
from contextlib import closing
import fcntl
import hmac
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import threading
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, build_opener, ProxyHandler

from service.rpc import CoreUnavailable, GatewayClient, GENESIS, MAX_RESPONSE, NoRedirect, block_id
from service.server import BoundedServer, handler, secret

PROTOCOLS = {"leap", "pins", "qord"}


def configuration():
    protocol, chain = os.environ.get("INDEX_PROTOCOL"), os.environ.get("QBIT_CHAIN")
    expected = json.loads(Path(__file__).with_name("protocol.json").read_text())["protocol"]
    if protocol != expected or protocol not in PROTOCOLS or chain not in GENESIS:
        raise ValueError("Explicit supported protocol/network required")
    return protocol, chain


def bind_writer(directory, protocol, chain):
    """One writer and one immutable protocol/network identity per data folder."""
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Expected app-owned data directory")
    lock = directory / ".writer.lock"
    if lock.is_symlink():
        raise ValueError("Linked lock refused")
    stream = lock.open("a")
    try:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        marker = directory / ".index-app.json"
        expected = {"format": 1, "protocol": protocol, "chain": chain}
        if marker.is_symlink():
            raise ValueError("Linked identity refused")
        if marker.exists():
            if json.loads(marker.read_text()) != expected:
                raise ValueError("Data belongs to a different protocol or network")
        else:
            if any(p.name != ".writer.lock" for p in directory.iterdir()):
                raise ValueError("Existing unmarked data requires a reviewed migration")
            descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as target:
                json.dump(expected, target)
                target.flush()
                os.fsync(target.fileno())
        os.set_inheritable(stream.fileno(), True)
        return stream
    except BaseException:
        stream.close()
        raise


def sqlite_snapshot(directory, protocol, chain, *, catalog=False, offset=0, limit=100):
    """Read an existing committed snapshot without creating/migrating databases."""
    marker = directory / ".index-app.json"
    if marker.is_symlink() or not marker.is_file() or json.loads(marker.read_text()) != {
            "format": 1, "protocol": protocol, "chain": chain}:
        raise CoreUnavailable("Index identity is not available")
    name = "leap-index.sqlite3" if protocol == "leap" else "index.sqlite"
    database = directory / name
    if database.is_symlink() or not database.is_file():
        raise CoreUnavailable("Index is initializing")
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
        db.row_factory = sqlite3.Row
        if protocol == "leap":
            for module, filename in (("quantum", "quantum.sqlite3"), ("gallery", "gallery.sqlite3"),
                                     ("arbitrary", "arbitrary.sqlite3"), ("legacy_a", "pebble.sqlite3"),
                                     ("legacy_b", "pson.sqlite3")):
                path = directory / filename
                if path.is_symlink() or not path.is_file():
                    raise CoreUnavailable("Module index is initializing")
                db.execute("ATTACH DATABASE ? AS " + module, (path.resolve().as_uri() + "?mode=ro",))
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        tip = db.execute("SELECT height,hash FROM blocks ORDER BY height DESC LIMIT 1").fetchone()
        if protocol == "leap":
            config = db.execute("SELECT schema_version,network,pebble_profile FROM index_config WHERE singleton=1").fetchone()
            schema = db.execute("SELECT module,version FROM quantum.schema_info WHERE singleton=1").fetchone()
            if not config or tuple(config) != (4, chain, "off") or not schema or tuple(schema) != ("quantums", 2):
                raise CoreUnavailable("Index configuration is incompatible")
            for module in ("quantum", "gallery", "arbitrary", "legacy_a", "legacy_b"):
                module_tip = db.execute("SELECT height,hash FROM " + module + ".index_tip WHERE singleton=1").fetchone()
                if tip and (not module_tip or tuple(tip) != tuple(module_tip)):
                    raise CoreUnavailable("Index snapshot is not committed")
            count = db.execute("SELECT count(*) FROM quantum.quantums").fetchone()[0]
            rows = [] if not catalog else [dict(row) for row in db.execute(
                "SELECT q.*,l.current_txid,l.current_vout,l.current_address FROM quantum.quantums q "
                "LEFT JOIN quantum.quantum_locations l USING(quantum_id) "
                "ORDER BY q.creation_height,q.creation_txid,q.creation_vout LIMIT ? OFFSET ?", (limit, offset))]
        else:
            config = db.execute("SELECT value FROM meta WHERE key='network'").fetchone()
            schema = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
            expected = "mainnet" if chain == "main" else chain
            if not config or config[0] != expected or not schema or schema[0] != "1":
                raise CoreUnavailable("Index network is incompatible")
            count = db.execute("SELECT count(*) FROM inscriptions").fetchone()[0]
            rows = [] if not catalog else [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM inscriptions ORDER BY number LIMIT ? OFFSET ?", (limit, offset))]
        return {"height": tip[0] if tip else -1, "hash": tip[1] if tip else None,
                "items": count, "rows": rows}


def local_qord(path):
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(Request("http://127.0.0.1:8787" + path,
                            headers={"Accept": "application/json"}), timeout=5) as response:
        body = response.read(MAX_RESPONSE + 1)
    if len(body) > MAX_RESPONSE:
        raise CoreUnavailable("Indexer response exceeds read limit")
    return body


def observed_status(protocol, chain, directory, client):
    info = client.call("getblockchaininfo", [])
    if info.get("chain") != chain or client.call("getblockhash", [0]) != GENESIS[chain]:
        raise CoreUnavailable("Node identity mismatch")
    if protocol == "qord":
        upstream = json.loads(local_qord("/status"))
        expected = "mainnet" if chain == "main" else chain
        if upstream.get("chain") != expected or upstream.get("unrecoverably_reorged"):
            raise CoreUnavailable("Indexer network or reorg state is incompatible")
        height = upstream.get("height")
        index = {"height": height if type(height) is int else -1,
                 "hash": json.loads(local_qord("/r/blockhash/" + str(height))) if type(height) is int else None,
                 "items": upstream.get("inscriptions", 0)}
    else:
        index = sqlite_snapshot(directory, protocol, chain)
    height = index["height"]
    hash_matches = (type(height) is int and height >= 0 and block_id(index["hash"])
                    and client.call("getblockhash", [height]) == index["hash"])
    ready = (bool(hash_matches) and height == info.get("blocks") == info.get("headers")
             and info.get("initialblockdownload") is False)
    return {"app": protocol.upper() if protocol != "qord" else "qord", "network": chain,
            "nodeHeight": info.get("blocks"), "indexHeight": height, "indexHash": index["hash"],
            "items": index["items"], "observedSynchronized": ready,
            "transactionRoutingAccepted": False}


def authorized(headers, token):
    try:
        header = headers.get("Authorization", "")
        if not header.startswith("Basic ") or len(header) > 512:
            return False
        value = base64.b64decode(header[6:], validate=True)
        return hmac.compare_digest(value, ("reader:" + token).encode())
    except ValueError:
        return False


def front_handler(protocol, chain, directory, client, token):
    base = handler("ui", client, chain)
    class Front(base):
        def do_GET(self):
            target = urlsplit(self.path)
            path = target.path
            if path == "/api/status" or path.startswith("/v1/"):
                if target.fragment or (path.startswith("/v1/") and (
                        self.headers.get("Origin") or not authorized(self.headers, token))):
                    self.send(401, {"error": "Indexer read authentication required"})
                    return
                try:
                    if path in {"/api/status", "/v1/status"}:
                        value = observed_status(protocol, chain, directory, client)
                    elif path == "/v1/catalog" and protocol in {"leap", "pins"}:
                        query = parse_qs(target.query, strict_parsing=True)
                        if set(query) - {"offset", "limit"} or any(len(v) != 1 for v in query.values()):
                            raise ValueError("Invalid pagination")
                        offset, limit = int(query.get("offset", ["0"])[0]), int(query.get("limit", ["100"])[0])
                        if not 0 <= offset <= 10_000_000 or not 1 <= limit <= 100:
                            raise ValueError("Pagination limit exceeded")
                        value = sqlite_snapshot(directory, protocol, chain, catalog=True, offset=offset, limit=limit)
                        value.update({"protocol": protocol, "network": chain, "offset": offset, "limit": limit})
                    elif protocol == "qord" and path.startswith("/v1/upstream/") and not target.query:
                        upstream = path[len("/v1/upstream"):]
                        if not re.fullmatch(r"/(?:status|inscriptions(?:/[0-9]{1,8})?|inscription/[0-9a-f]{64}i[0-9]{1,10}|r/blockhash/[0-9]{1,10})", upstream):
                            raise ValueError("Read route refused")
                        value = json.loads(local_qord(upstream))
                    else:
                        self.send(404, {"error": "Read route not available"})
                        return
                    self.send(200, value)
                except (CoreUnavailable, OSError, ValueError, sqlite3.Error, KeyError, TypeError):
                    self.send(503, {"app": protocol, "network": chain, "available": False,
                                    "observedSynchronized": False,
                                    "message": "Waiting for a compatible committed index and verified node"})
                return
            super().do_GET()
    return Front


def start_indexer(protocol, chain):
    import time
    enabled = os.environ.get("INDEX_ENABLED", "0")
    if enabled not in {"0", "1"}:
        raise ValueError("INDEX_ENABLED must be 0 or 1")
    if enabled == "0":
        stopped = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stopped.set())
        stopped.wait()
        return
    client = GatewayClient("http://127.0.0.1:8081/", "reader", secret(os.environ.get("INDEX_LOCAL_TOKEN")))
    deadline = time.monotonic() + 120
    while True:
        try:
            if (client.call("getblockchaininfo", []).get("chain") != chain
                    or client.call("getblockhash", [0]) != GENESIS[chain]):
                raise ValueError("Node network/genesis mismatch")
            break
        except CoreUnavailable:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)
    lock = bind_writer(Path("/data"), protocol, chain)
    if protocol == "leap":
        os.environ.update(LEAP_RPC_URL="http://127.0.0.1:8081/", LEAP_RPC_USER="reader",
                          LEAP_RPC_PASSWORD=os.environ["INDEX_LOCAL_TOKEN"])
        command = ["python", "-m", "quantums.cli", "index", "run", "--chain", chain,
                   "--data-dir", "/data", "--commit-batch", "100"]
    elif protocol == "pins":
        network = "mainnet" if chain == "main" else chain
        command = ["node", "--experimental-strip-types", "/opt/protocol/scripts/indexer-run.ts",
                   "--network", network, "--confirm-network", network, "--db", "/data/index.sqlite",
                   "--rpc", "http://127.0.0.1:8081/", "--rpc-user", "reader",
                   "--rpc-password", os.environ["INDEX_LOCAL_TOKEN"], "--port", "8787", "--poll", "5"]
    else:
        # JSON is valid YAML. This file contains only app read-proxy credentials.
        config = {"chain": "mainnet" if chain == "main" else chain,
                  "qbit_rpc_url": "http://127.0.0.1:8081/", "qbit_rpc_username": "reader",
                  "qbit_rpc_password": os.environ["INDEX_LOCAL_TOKEN"], "data_dir": "/data",
                  "index_addresses": True, "index_sats": False, "index_runes": False,
                  "index_transactions": False, "index_cache_size": 134217728,
                  "qbit_rpc_limit": 2, "commit_interval": 100}
        path = Path("/data/app-qord.yaml")
        if path.is_symlink():
            raise ValueError("Linked config refused")
        with path.open("w") as stream:
            os.chmod(path, 0o600)
            json.dump(config, stream)
        command = ["/usr/local/bin/qord", "--config", str(path), "--config-dir", "/data",
                   "server", "--address", "127.0.0.1", "--http-port", "8787"]
    # Keep the single-writer advisory lock held across the exec.
    os.set_inheritable(lock.fileno(), True)
    os.execvp(command[0], command)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("indexer", "transport", "ui"))
    args = parser.parse_args()
    protocol, chain = configuration()
    local = secret(os.environ.get("INDEX_LOCAL_TOKEN"))
    if args.mode == "indexer":
        start_indexer(protocol, chain)
        return
    if args.mode == "transport":
        client = GatewayClient(os.environ["INDEX_CORE_READ_URL"], "reader",
                               secret(os.environ.get("INDEX_CORE_READ_TOKEN")), max_response=MAX_RESPONSE)
        server = BoundedServer(("0.0.0.0", 8081), handler("broker", client, chain, {"reader": local}))
    else:
        client = GatewayClient("http://127.0.0.1:8081/", "reader", local)
        server = BoundedServer(("0.0.0.0", 8080), front_handler(protocol, chain, Path("/data"), client,
                               secret(os.environ.get("INDEX_API_TOKEN"))))
    try:
        server.serve_forever(poll_interval=.25)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
