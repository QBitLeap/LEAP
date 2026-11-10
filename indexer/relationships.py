"""Read-only canonical Quantum relationship queries for public Qbit networks.

No site, wallet, sale policy, index writes, or Qbit consensus changes. The
existing committed index and node history remain authoritative on each query.
"""
from contextlib import closing
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from quantums.envelope import parent_ids
from quantums.rpc import require_network
from .discovery import discover_transaction

ID = re.compile(r"[0-9a-f]{64}:(?:0|[1-9][0-9]{0,9})\Z")


class RelationshipError(ValueError):
    """Unavailable/inconsistent data cannot establish a verified relationship."""


def _check_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value) or int(value.rsplit(":", 1)[1]) > 0xFFFFFFFF:
        raise RelationshipError("Invalid Quantum ID")
    return value


def _bits(value):
    try:
        result = Decimal(str(value)) * 100_000_000
        if not result.is_finite() or result < 0 or result != result.to_integral_value():
            raise RelationshipError("Invalid transaction value")
        return int(result)
    except InvalidOperation as exc:
        raise RelationshipError("Invalid transaction value") from exc


class QuantumRelationshipQuery:
    """One network/index/node context; caches hold only immutable chain data.

    ``discover()`` returns verified parents, unresolved declarations, canonical
    order and connected families. It never infers links from custom metadata
    or collection labels. RPC needs only chain/block/transaction read methods.
    """
    def __init__(self, index_dir, network, rpc):
        if network not in {"main", "testnet4"}:
            raise RelationshipError("Quantum relationship queries support main and testnet4")
        self.index_dir, self.network, self.rpc = Path(index_dir), network, rpc

    def _snapshot(self):
        directory = self.index_dir
        paths = [directory / "leap-index.sqlite3", directory / "quantum.sqlite3"]
        if any(path.is_symlink() or not path.is_file() for path in paths):
            raise RelationshipError("The Quantum relationship index is unavailable")
        with closing(sqlite3.connect(paths[0].resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            db.row_factory = sqlite3.Row
            db.execute("ATTACH DATABASE ? AS quantum", (paths[1].resolve().as_uri() + "?mode=ro",))
            db.execute("PRAGMA query_only=ON"); db.execute("BEGIN")
            config = db.execute("SELECT schema_version,network,pebble_profile FROM index_config WHERE singleton=1").fetchone()
            tip = db.execute("SELECT height,hash FROM blocks ORDER BY height DESC LIMIT 1").fetchone()
            module = db.execute("SELECT height,hash FROM quantum.index_tip WHERE singleton=1").fetchone()
            schema = db.execute("SELECT version FROM quantum.schema_info WHERE singleton=1").fetchone()
            if schema is None or tuple(schema) != (2,):
                raise RelationshipError("Unsupported Quantum index schema")
            if not config or tuple(config) != (4, self.network, "off"):
                raise RelationshipError("Relationship index must use schema 4, the selected network and Pebbles off")
            if not tip or not module or tuple(tip) != tuple(module):
                raise RelationshipError("The Quantum relationship index is synchronizing; try again shortly")
            rows = [dict(row) for row in db.execute("SELECT q.*,b.hash AS creation_hash,l.current_txid,l.current_vout FROM quantum.quantums q JOIN blocks b ON b.height=q.creation_height LEFT JOIN quantum.quantum_locations l ON l.quantum_id=q.quantum_id")]
            total = db.execute("SELECT COUNT(*) FROM quantum.quantums").fetchone()[0]
            if total != len(rows):
                raise RelationshipError("Quantum creation blocks are missing from the relationship index")
            genesis = db.execute("SELECT hash FROM blocks WHERE height=0").fetchone()
            if not genesis:
                raise RelationshipError("Relationship index has no genesis block")
            return {"height": tip[0], "hash": tip[1], "genesis": genesis[0]}, rows

    @lru_cache(maxsize=256)
    def _block_order(self, block_hash):
        block = self.rpc.call("getblock", block_hash, 1)
        if block.get("hash") != block_hash or not isinstance(block.get("tx"), list):
            raise RelationshipError("Cannot verify Quantum inscription order")
        return {txid: number for number, txid in enumerate(block["tx"])}

    def _parent_locations(self, points):
        """Resolve historical ownership outpoints without changing LEAP's index."""
        if not points:
            return {}
        path = self.index_dir / "quantum.sqlite3"
        found = {}
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON"); db.execute("BEGIN")
            ordered = sorted(points)
            for start in range(0, len(ordered), 100):
                chunk = ordered[start:start + 100]
                placeholders = ",".join("(?,?)" for _ in chunk)
                params = tuple(value for point in chunk for value in point)
                for row in db.execute(f"SELECT * FROM quantum_locations WHERE (current_txid,current_vout) IN (VALUES {placeholders})", params):
                    found.setdefault((row["current_txid"], row["current_vout"]), {})[row["quantum_id"]] = dict(row)
                for field in ("previous_row", "expected_row"):
                    records = db.execute(f"SELECT {field} FROM undo_mutations WHERE table_name='quantum_locations' AND (json_extract({field},'$.current_txid'),json_extract({field},'$.current_vout')) IN (VALUES {placeholders})", params)
                    for record in records:
                        row = json.loads(record[0])
                        script = row.get("current_script")
                        if isinstance(script, dict) and set(script) == {"__bytes__"}:
                            row["current_script"] = bytes.fromhex(script["__bytes__"])
                        found.setdefault((row["current_txid"], row["current_vout"]), {})[row["quantum_id"]] = row
        return found

    def _relationships(self, rows):
        """Use canonical parent headers plus indexed ownership/control evidence.

        Collection labels and custom metadata alone never create a family.
        Header references can name a prior parent successor outpoint; recover
        the stable identity and verify the child spends/recreates its control.
        """
        artifacts, claims, points = {}, {}, set()
        by_id = {row["quantum_id"]: row for row in rows}
        def point(value):
            txid, vout = value.rsplit(":", 1)
            _check_id(txid + ":" + str(int(vout)))
            return txid, int(vout)
        for row in rows:
            qid = row["quantum_id"]
            artifact = self._recover(row); artifacts[qid] = artifact
            references = parent_ids(artifact.parent)
            if not references:
                continue
            tx = self._transaction(row["creation_txid"], row["creation_hash"])
            inputs = tx.get("vin", [])[:len(references)]
            if len(inputs) != len(references) or any("txid" not in vin or "vout" not in vin for vin in inputs):
                continue
            try:
                refs = tuple(point(ref) for ref in references)
            except (ValueError, TypeError):
                continue
            spends = tuple((vin["txid"], vin["vout"]) for vin in inputs)
            points.update(refs); points.update(spends)
            claims[qid] = (refs, spends, tx)
        locations = self._parent_locations(points)
        parents = {qid: [] for qid in by_id}
        for qid, (references, spends, tx) in claims.items():
            resolved = []
            for position, (reference, spend) in enumerate(zip(references, spends)):
                original = f"{reference[0]}:{reference[1]}"
                owners = locations.get(reference, {})
                parent = original if original in by_id else next(iter(owners)) if len(owners) == 1 else None
                state = locations.get(spend, {}).get(parent)
                if not parent or parent not in by_id or by_id[parent]["number"] >= by_id[qid]["number"] or not state:
                    break
                successor = tx.get("vout", [])[position] if position < len(tx.get("vout", [])) else {}
                script = successor.get("scriptPubKey", {}).get("hex")
                value = successor.get("value_bits") if "value_bits" in successor else _bits(successor.get("value", 0))
                expected_script = state.get("current_script")
                if not isinstance(expected_script, bytes) or script != expected_script.hex() or type(value) is not int or value != state.get("current_value"):
                    break
                resolved.append(parent)
            else:
                if len(set(resolved)) == len(resolved):
                    parents[qid] = resolved
        return parents, artifacts

    @staticmethod
    def _families(rows, parents):
        roots = {row["quantum_id"]: row["quantum_id"] for row in rows}
        def root(qid):
            while roots[qid] != qid:
                roots[qid] = roots[roots[qid]]; qid = roots[qid]
            return qid
        for child, linked in parents.items():
            for parent in linked:
                roots[root(child)] = root(parent)
        groups = {}
        for row in rows:
            groups.setdefault(root(row["quantum_id"]), []).append(row)
        return list(groups.values())

    @lru_cache(maxsize=64)
    def _transaction(self, txid, block_hash):
        tx = self.rpc.call("getrawtransaction", txid, True, block_hash)
        if not isinstance(tx, dict) or tx.get("txid") != txid or tx.get("blockhash") != block_hash:
            raise RelationshipError("Cannot verify the Quantum creation transaction")
        return tx

    @lru_cache(maxsize=128)
    def _artifact(self, txid, vout, block_hash, content_hash, name, content_type, data_vout, envelope_index):
        tx = self._transaction(txid, block_hash)
        candidates = discover_transaction(tx, height=0, transaction_index=0)
        item = next((item for item in candidates if item.quantum_id == f"{txid}:{vout}" and item.data_output == data_vout and item.envelope_index == envelope_index), None)
        if item is None:
            raise RelationshipError("Indexed Quantum carrier or ownership position is not canonical")
        artifact = item.artifact
        if (artifact.content_sha256, artifact.name, artifact.content_type) != (content_hash, name, content_type) or hashlib.sha256(artifact.content).hexdigest() != content_hash:
            raise RelationshipError("Recovered Quantum differs from the indexed creation")
        return artifact

    def _recover(self, row):
        return self._artifact(row["creation_txid"], row["creation_vout"], row["creation_hash"], row["content_sha256"], row["name"], row["content_type"], row["data_vout"], row["envelope_index"])

    @staticmethod
    def _identity(rows):
        return {(row["quantum_id"], row["creation_hash"], row["current_txid"], row["current_vout"]) for row in rows}

    def discover(self):
        try:
            return self._discover()
        except (sqlite3.Error, OSError, KeyError, TypeError) as exc:
            raise RelationshipError("Relationship index or chain data is unavailable or changed; retry") from exc

    def _discover(self):
        info = self.rpc.call("getblockchaininfo")
        if not isinstance(info, dict):
            raise RelationshipError("Invalid Core chain status")
        require_network(info, self.network)
        tip, rows = self._snapshot()
        if self.rpc.call("getblockhash", 0) != tip["genesis"] or self.rpc.call("getblockhash", tip["height"]) != tip["hash"]:
            raise RelationshipError("Core and relationship index do not share the committed chain")
        heights = {}
        for row in rows:
            _check_id(row["quantum_id"])
            if row["quantum_id"] != f'{row["creation_txid"]}:{row["creation_vout"]}':
                raise RelationshipError("Invalid indexed Quantum creation identity")
            heights.setdefault(row["creation_height"], set()).add(row["creation_txid"])
        orders = {height: self._block_order(next(row["creation_hash"] for row in rows if row["creation_height"] == height))
                  for height, txids in heights.items() if len(txids) > 1}
        for row in rows:
            order = orders.get(row["creation_height"])
            if order is not None and row["creation_txid"] not in order:
                raise RelationshipError("Quantum creation transaction is missing from its block")
        rows.sort(key=lambda row: (row["creation_height"], orders.get(row["creation_height"], {}).get(row["creation_txid"], 0), row["data_vout"], row["envelope_index"], row["creation_vout"]))
        for number, row in enumerate(rows, 1):
            row["number"] = number
        parents, artifacts = self._relationships(rows)
        final_tip, final_rows = self._snapshot()
        if self._identity(rows) != self._identity(final_rows) or self.rpc.call("getblockhash", tip["height"]) != tip["hash"]:
            raise RelationshipError("The relationship index or chain changed during discovery; retry")
        final_node = self.rpc.call("getblockchaininfo")
        require_network(final_node, self.network)
        synchronized = (final_tip["height"], final_tip["hash"]) == (tip["height"], tip["hash"]) == (final_node.get("blocks"), final_node.get("bestblockhash")) and final_node.get("headers") == final_node.get("blocks") and final_node.get("initialblockdownload") is False
        groups = self._families(rows, parents)
        return {"network": self.network, "indexHeight": tip["height"], "indexHash": tip["hash"], "nodeHeight": final_node.get("blocks"), "synchronized": synchronized,
                "quantums": [{"id": row["quantum_id"], "number": row["number"], "name": row["name"], "collection": artifacts[row["quantum_id"]].collection,
                              "contentType": row["content_type"], "contentSha256": row["content_sha256"], "creationHeight": row["creation_height"], "creationBlockHash": row["creation_hash"],
                              "declaredParents": list(parent_ids(artifacts[row["quantum_id"]].parent)), "parents": parents[row["quantum_id"]],
                              "parentStatus": "verified" if parents[row["quantum_id"]] else "unresolved" if artifacts[row["quantum_id"]].parent else "none"} for row in rows],
                "families": [{"id": group[0]["quantum_id"], "firstNumber": group[0]["number"], "members": [row["quantum_id"] for row in group],
                              "roots": [row["quantum_id"] for row in group if not parents[row["quantum_id"]]]} for group in groups]}
