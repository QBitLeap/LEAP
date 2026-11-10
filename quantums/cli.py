from __future__ import annotations

import argparse
from dataclasses import replace
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import signal
from threading import Event
from urllib.parse import urlsplit

from .envelope import FORMAT_VERSION, EnvelopeError, artifact_from_path, decode, encode, encode_batch, parent_ids
from .rpc import HttpQbitRpc, NetworkBoundRpc, QbitRpc, Rpc, RpcError, require_network
from .networks import NETWORKS
from .transaction import (
    DEFAULT_OWNERSHIP_BITS,
    prepare_inscription,
    prepare_batch,
    prepare_gallery_successor,
    recover_from_transaction,
    unlock_prepared_inputs,
)


class LeapArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: bad command: error: {message}\n")


def _index_compatibility_status(profile: str) -> dict[str, object]:
    """Retain the schema-4 status contract without enabling legacy processing."""
    if profile != "off":
        raise ValueError("only the disabled legacy profile is supported")
    return {
        "pebble_profile": "off", "pebble_enabled": False,
        "custody": "disabled", "consensus_enforced": False,
        "custody_warning": None,
    }


def command_index_relationships(args: argparse.Namespace) -> None:
    from indexer.relationships import QuantumRelationshipQuery
    from indexer.compatibility import resolve_index_profile

    if resolve_index_profile(args.chain, getattr(args, "pebble_profile", "off")) != "off":
        raise ValueError("Quantum relationship discovery requires the disabled legacy profile")
    query = QuantumRelationshipQuery(args.data_dir, args.chain, rpc_from_args(args, public=True))
    print(json.dumps(query.discover(), ensure_ascii=True, indent=2))


def command_index_status(args: argparse.Namespace) -> None:
    from indexer.materializers import operational_modules
    from indexer.unified import MODULE_DATABASE_NAMES, UnifiedIndex
    from indexer.compatibility import resolve_index_profile

    rpc = rpc_from_args(args, public=True)
    info = rpc.call("getblockchaininfo")
    require_network(info, args.chain)
    network = str(info.get("chain", args.chain))
    profile = resolve_index_profile(network, getattr(args, "pebble_profile", "auto"))
    with UnifiedIndex(
        args.data_dir, operational_modules(network, pebble_profile=profile)
    ) as index:
        index.bind_network(network, profile)
        status = index.status(int(info["blocks"]))
        remaining = max(0, status.node_height - status.index_height)
        print(json.dumps({
            "status": "synchronized" if remaining == 0 else "catching-up",
            "network": info.get("chain", args.chain),
            **_index_compatibility_status(profile),
            "node_tip": status.node_height,
            "index_tip": status.index_height,
            "index_hash": status.index_hash,
            "blocks_remaining": remaining,
            "active_modules": sorted(name for name, health in status.modules.items() if health == "ok"),
            "inactive_modules": sorted(name for name, health in status.modules.items() if health == "inactive"),
            "module_health": status.modules,
            "runtime_database_directory": str(index.data_directory),
        }, indent=2, sort_keys=True))


def command_index_run(args: argparse.Namespace) -> None:
    from indexer.materializers import operational_modules
    from indexer.unified import UnifiedIndex, follow
    from indexer.compatibility import resolve_index_profile

    if args.poll_seconds <= 0 or args.commit_batch <= 0:
        raise ValueError("index poll interval and commit batch must be positive")
    rpc = rpc_from_args(args, public=True)
    info = rpc.call("getblockchaininfo")
    require_network(info, args.chain)
    network = str(info.get("chain", args.chain))
    profile = resolve_index_profile(network, getattr(args, "pebble_profile", "auto"))
    stop = Event()
    previous = {}
    last_status_line: str | None = None

    def request_stop(signum, frame):
        del signum, frame
        stop.set()

    def report_status(status) -> None:
        nonlocal last_status_line
        line = json.dumps({
            "status": "synchronized" if status.node_height == status.index_height else "catching-up",
            "network": network,
            **_index_compatibility_status(profile),
            "node_tip": status.node_height,
            "index_tip": status.index_height,
            "index_hash": getattr(status, "index_hash", None),
            "blocks_remaining": max(0, status.node_height - status.index_height),
            "modules": status.modules,
        }, sort_keys=True)
        if line != last_status_line:
            print(line, flush=True)
            last_status_line = line

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.signal(signum, request_stop)
    try:
        with UnifiedIndex(
            args.data_dir, operational_modules(network, pebble_profile=profile)
        ) as index:
            index.bind_network(network, profile)
            follow(
                rpc, index, stop, poll_seconds=args.poll_seconds,
                commit_batch=args.commit_batch,
                on_status=report_status,
            )
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def decimal_fee_rate(value: str) -> Decimal:
    try:
        rate = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("fee rate must be a decimal number") from exc
    if not rate.is_finite() or rate <= 0:
        raise argparse.ArgumentTypeError("fee rate must be positive and finite")
    return rate


def positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed






def quantum_name(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("Quantum Name must not be blank")
    return value


def add_content_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("content", type=Path)
    parser.add_argument("--name", required=True, type=quantum_name, help="required non-blank Quantum Name")
    parser.add_argument("--content-type")
    parser.add_argument("--collection")


def add_rpc_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--qbit-cli", "--cli", dest="cli", default="qbit-cli")
    parser.add_argument("--chain", choices=NETWORKS, default="main")
    parser.add_argument("--datadir")
    parser.add_argument("--wallet")
    parser.add_argument("--rpc-url", default=os.environ.get("LEAP_RPC_URL"))
    parser.add_argument("--rpc-user", default=os.environ.get("LEAP_RPC_USER"))
    parser.add_argument("--rpc-password", default=os.environ.get("LEAP_RPC_PASSWORD"))
    parser.add_argument("--rpc-cookie", type=Path)


def add_publication_arguments(parser: argparse.ArgumentParser) -> None:
    publication = parser.add_mutually_exclusive_group()
    publication.add_argument(
        "--dry-run", dest="broadcast", action="store_false",
        help="build, sign, and validate without broadcasting",
    )
    # Retained so older explicit-broadcast scripts continue to work. Publishing
    # is now the default, so this flag has no additional effect.
    publication.add_argument("--broadcast", dest="broadcast", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--index-data-dir", type=Path,
        default=os.environ.get("LEAP_INDEX_DATA_DIR"),
        help="network-bound Leap index directory; defaults to LEAP_INDEX_DATA_DIR",
    )
    parser.set_defaults(broadcast=True)


def rpc_from_args(args: argparse.Namespace, *, wallet: str | None = None, public: bool = False) -> Rpc:
    selected_wallet = None if public else (wallet if wallet is not None else getattr(args, "wallet", None))
    if getattr(args, "rpc_url", None):
        if "/wallet/" in urlsplit(args.rpc_url).path:
            raise RpcError("--rpc-url must name the node endpoint; select a wallet with --wallet")
        transport = HttpQbitRpc(
            args.rpc_url,
            args.rpc_user,
            args.rpc_password,
            args.rpc_cookie,
            selected_wallet,
        )
    else:
        transport = QbitRpc(args.cli, args.chain, args.datadir, selected_wallet)
    return NetworkBoundRpc(transport, args.chain)


def require_wallet(args: argparse.Namespace) -> str:
    wallet = getattr(args, "wallet", None)
    if not wallet:
        raise ValueError("wallet command requires --wallet <name>")
    return wallet


def require_synchronized_index(args: argparse.Namespace, rpc: Rpc) -> None:
    from indexer.materializers import operational_modules
    from indexer.unified import COORDINATOR_DATABASE_NAME, MODULE_DATABASE_NAMES, UnifiedIndex
    from indexer.compatibility import resolve_index_profile

    configured = getattr(args, "index_data_dir", None)
    if not configured:
        raise RuntimeError(
            "transaction command requires --index-data-dir <directory> "
            "or LEAP_INDEX_DATA_DIR"
        )
    directory = Path(configured).expanduser().resolve()
    required_files = (COORDINATOR_DATABASE_NAME, *MODULE_DATABASE_NAMES.values())
    if not directory.is_dir() or any(not (directory / name).is_file() for name in required_files):
        raise RuntimeError(f"Leap index does not exist: {directory}")

    info = rpc.call("getblockchaininfo")
    require_network(info, getattr(args, "chain", "main"))
    network = str(info.get("chain", getattr(args, "chain", "main")))
    profile = resolve_index_profile(network, getattr(args, "pebble_profile", "auto"))
    node_height = int(info["blocks"])
    node_hash = str(info["bestblockhash"])
    if bool(info.get("initialblockdownload")) or int(info.get("headers", node_height)) != node_height:
        raise RuntimeError(
            f"Qbit node is not synchronized: blocks={node_height}, "
            f"headers={int(info.get('headers', node_height))}"
        )

    modules = operational_modules(network, pebble_profile=profile)
    with UnifiedIndex(directory, modules) as index:
        index.bind_network(network, profile)
        bound_network, bound_profile = index.connection.execute(
            "SELECT network,pebble_profile FROM index_config WHERE singleton=1"
        ).fetchone()
        if bound_network != network:
            raise RuntimeError(
                f"Leap index network mismatch: node={network}, index={bound_network or 'unbound'}"
            )
        if bound_profile != profile:
            raise RuntimeError(
                f"Leap index Pebble profile mismatch: requested={profile}, "
                f"index={bound_profile or 'unbound'}"
            )
        status = index.status(node_height)
        unhealthy = {
            name: status.modules.get(name, "missing")
            for name in modules
            if status.modules.get(name) != "ok"
        }
        if unhealthy:
            details = ", ".join(f"{name}={health}" for name, health in sorted(unhealthy.items()))
            raise RuntimeError(f"Leap index module is unhealthy: {details}")
        if status.index_height != node_height or status.index_hash != node_hash:
            raise RuntimeError(
                f"Leap index is not synchronized: node={node_height}:{node_hash}, "
                f"index={status.index_height}:{status.index_hash}"
            )


def print_artifact(artifact, envelope_length: int) -> None:
    print("protocol: Leap")
    print(f"format-version: {artifact.format_version}")
    print(f"name: {artifact.name}")
    print(f"content-type: {artifact.content_type}")
    print(f"content-length: {len(artifact.content)}")
    print(f"content-sha256: {artifact.content_sha256}")
    print(f"ownership-output: {artifact.ownership_output}")
    if artifact.collection is not None:
        print(f"collection: {artifact.collection}")
    for parent in parent_ids(artifact.parent):
        print(f"parent: {parent}")
    if artifact.source_utxo is not None:
        print(f"source-utxo: {artifact.source_utxo}:0")
    if artifact.metadata is not None:
        print(f"metadata: {json.dumps(artifact.metadata, ensure_ascii=False, sort_keys=True)}")
    print(f"envelope-length: {envelope_length}")


def command_build(args: argparse.Namespace) -> None:
    artifact = artifact_from_path(
        args.content,
        name=args.name,
        content_type=args.content_type,
        collection=args.collection,
    )
    envelope = encode(artifact)
    args.out.write_bytes(envelope)
    print_artifact(artifact, len(envelope))
    print(f"wrote: {args.out}")


def command_inspect(args: argparse.Namespace) -> None:
    envelope = args.envelope.read_bytes()
    print_artifact(decode(envelope), len(envelope))


def require_available_names(args: argparse.Namespace, names: list[str], *, exclude_txids=(), rpc=None) -> None:
    from .names import require_available
    directory = Path(args.index_data_dir)
    cache = Path(os.environ.get("LEAP_NAME_CACHE_DIR", str(directory.parent / (directory.name + "-name-cache"))))
    require_available(directory, args.chain, names, exclude_txids=exclude_txids, rpc=rpc, cache_dir=cache)


def remember_pending_names(args, txid, names):
    from .names import PendingNameCatalog
    import sqlite3
    directory = Path(args.index_data_dir)
    cache = Path(os.environ.get("LEAP_NAME_CACHE_DIR", str(directory.parent / (directory.name + "-name-cache"))))
    try:
        PendingNameCatalog(cache).remember(args.chain, txid, names)
    except (OSError, sqlite3.Error):
        # Publication is already accepted. A cache failure cannot turn it into
        # a reported payment failure; the next check can rebuild from Core.
        print(f"pending name catalog update unavailable; transaction accepted: {txid}", file=sys.stderr)


def command_inscribe(args: argparse.Namespace) -> None:
    artifact = artifact_from_path(
        args.content,
        name=args.name,
        content_type=args.content_type,
        collection=args.collection,
    )
    rpc = rpc_from_args(args)
    require_synchronized_index(args, rpc)
    require_available_names(args, [artifact.name], rpc=rpc)
    prepared = prepare_inscription(
        rpc,
        artifact,
        owner_address=args.owner,
        ownership_bits=args.ownership_bits,
        fee_rate=args.fee_rate,
    )
    print(f"owner: {prepared.owner_address}")
    print(f"ownership-value: {prepared.ownership_bits} bits")
    print(f"envelope-length: {prepared.envelope_bytes}")
    print(f"vsize: {prepared.vsize}")
    print(f"fee: {prepared.fee_qbt} QBT")
    print(f"testmempoolaccept: {'allowed' if prepared.allowed else 'rejected'}")
    if not prepared.allowed:
        raise RuntimeError(prepared.reject_reason or "transaction rejected")
    if not args.broadcast:
        unlock_prepared_inputs(rpc, prepared)
        print("clean command: success: dry run passed; signed transaction was not broadcast")
        return
    try:
        require_synchronized_index(args, rpc)
        require_available_names(args, [artifact.name], exclude_txids=[prepared.txid] if prepared.txid else [], rpc=rpc)
    except Exception:
        unlock_prepared_inputs(rpc, prepared)
        raise
    txid = rpc.call("sendrawtransaction", prepared.hex)
    remember_pending_names(args, txid, [artifact.name])
    rpc.call("lockunspent", False, [{"txid": txid, "vout": 0}], True)
    print(f"inscription: {txid}:0")


def command_recover(args: argparse.Namespace) -> None:
    match = OUTPOINT_RE.fullmatch(args.inscription)
    if not match:
        raise ValueError("inscription must be <64-character txid>:<output>")
    txid, output_text = match.groups()
    rpc = rpc_from_args(args)
    try:
        transaction = rpc.call("getrawtransaction", txid, True)
    except RpcError:
        wallet_transaction = rpc.call("gettransaction", txid)
        transaction = rpc.call("decoderawtransaction", wallet_transaction["hex"])
    artifact = recover_from_transaction(transaction, int(output_text))
    target = args.out or Path(artifact.name).name
    target = Path(target)
    target.write_bytes(artifact.content)
    print_artifact(artifact, len(encode_batch((artifact,))) if artifact.format_version == 2 else len(encode(artifact)))
    print(f"wrote verified content: {target}")


def command_node_status(args: argparse.Namespace) -> None:
    rpc = rpc_from_args(args, public=True)
    chain = rpc.call("getblockchaininfo")
    require_network(chain, args.chain)
    network = rpc.call("getnetworkinfo")
    print(f"chain: {chain['chain']}")
    print(f"blocks: {chain['blocks']}")
    print(f"headers: {chain['headers']}")
    print(f"verification-progress: {chain['verificationprogress']}")
    print(f"initial-block-download: {str(chain['initialblockdownload']).lower()}")
    print(f"size-on-disk: {chain['size_on_disk']}")
    print(f"connections: {network['connections']}")
    print(f"qbit-version: {network['subversion']}")


def command_wallet_list(args: argparse.Namespace) -> None:
    loaded = set(rpc_from_args(args).call("listwallets"))
    entries = rpc_from_args(args).call("listwalletdir").get("wallets", [])
    for entry in entries:
        name = entry["name"] if isinstance(entry, dict) else str(entry)
        print(f"{name}\t{'loaded' if name in loaded else 'unloaded'}")


def command_wallet_create(args: argparse.Namespace) -> None:
    result = rpc_from_args(args).call("createwallet", args.name)
    print(f"wallet: {result['name']}")


def command_wallet_balance(args: argparse.Namespace) -> None:
    result = rpc_from_args(args, wallet=require_wallet(args)).call("getbalances")
    print(json.dumps(result, indent=2, sort_keys=True))


def command_wallet_receive(args: argparse.Namespace) -> None:
    address = rpc_from_args(args, wallet=require_wallet(args)).call("getnewaddress", "", "p2mr")
    print(address)


def command_wallet_inscribe(args: argparse.Namespace) -> None:
    args.content = args.file
    args.owner = args.destination
    command_inscribe(args)


def _transaction_for_recovery(rpc: Rpc, txid: str) -> dict:
    try:
        return rpc.call("getrawtransaction", txid, True)
    except RpcError:
        wallet_transaction = rpc.call("gettransaction", txid)
        return rpc.call("decoderawtransaction", wallet_transaction["hex"])


def _gallery_artifact(rpc: Rpc, quantum_id: str):
    from .gallery import MIME_TYPE, decode as decode_gallery, normalize_id

    normalized = normalize_id(quantum_id)
    txid, vout = normalized.split(":")
    artifact = recover_from_transaction(_transaction_for_recovery(rpc, txid), int(vout))
    if artifact.content_type != MIME_TYPE:
        raise EnvelopeError(f"Quantum is not a Leap gallery: {normalized}")
    return artifact, decode_gallery(artifact.content)


def _gallery_state(rpc: Rpc, current: str):
    from .gallery import GalleryRoot, GalleryUpdate, apply, normalize_id

    cursor = normalize_id(current)
    updates = []
    while True:
        _, record = _gallery_artifact(rpc, cursor)
        if isinstance(record, GalleryRoot):
            root_id, root = cursor, record
            break
        if not isinstance(record, GalleryUpdate) or normalize_id(record.previous) == cursor:
            raise EnvelopeError("invalid gallery predecessor chain")
        updates.append((cursor, record))
        cursor = normalize_id(record.previous)
        if len(updates) > 100_000:
            raise EnvelopeError("gallery successor chain exceeds limit")
    members = tuple(root.members)
    previous = root_id
    for successor_id, update in reversed(updates):
        if normalize_id(update.root) != root_id or normalize_id(update.previous) != previous:
            raise EnvelopeError("gallery successor does not extend its declared history")
        members = apply(members, update)
        previous = successor_id
    return root_id, root, members


def _finish_gallery(args, rpc: Rpc, prepared, *, label: str) -> str | None:
    print(f"envelope-length: {prepared.envelope_bytes}")
    print(f"vsize: {prepared.vsize}")
    print(f"fee: {prepared.fee_qbt} QBT")
    print(f"testmempoolaccept: {'allowed' if prepared.allowed else 'rejected'}")
    if not prepared.allowed:
        unlock_prepared_inputs(rpc, prepared)
        raise RuntimeError(prepared.reject_reason or "gallery transaction rejected")
    if not args.broadcast:
        unlock_prepared_inputs(rpc, prepared)
        print("clean command: success: dry run passed; signed gallery transaction was not broadcast")
        return None
    txid = rpc.call("sendrawtransaction", prepared.hex)
    rpc.call("lockunspent", False, [{"txid": txid, "vout": 0}], True)
    print(f"{label}: {txid}:0")
    return f"{txid}:0"


def command_gallery_create(args: argparse.Namespace) -> None:
    from .envelope import create_artifact
    from .gallery import GalleryRoot, MIME_TYPE, encode as encode_gallery, normalize_id
    from .gallery_yaml import load_create

    rpc = rpc_from_args(args, wallet=require_wallet(args))
    require_synchronized_index(args, rpc)
    manifest = load_create(args.file)
    members = tuple(normalize_id(value) for value in manifest.members)
    record = GalleryRoot(manifest.name, members)
    artifact = create_artifact(
        encode_gallery(record), content_type=MIME_TYPE,
        name=f"{manifest.name} Gallery", collection=manifest.name,
    )
    prepared = prepare_inscription(
        rpc, artifact, owner_address=manifest.destination,
        ownership_bits=manifest.ownership_bits, fee_rate=args.fee_rate,
    )
    print(f"gallery-file: {args.file}")
    print(f"gallery-name: {manifest.name}")
    print(f"members: {len(members)}")
    _finish_gallery(args, rpc, prepared, label="gallery")


def command_gallery_update(args: argparse.Namespace) -> None:
    from .envelope import create_artifact
    from .gallery import GalleryUpdate, MIME_TYPE, apply, encode as encode_gallery, normalize_id
    from .gallery_yaml import load_update

    rpc = rpc_from_args(args, wallet=require_wallet(args))
    require_synchronized_index(args, rpc)
    if args.file is not None:
        if args.gallery is not None or args.add or args.remove:
            raise ValueError("gallery update --file cannot be mixed with --gallery, --add, or --remove")
        manifest = load_update(args.file)
        current, additions, removals = manifest.gallery, manifest.add, manifest.remove
    else:
        if args.gallery is None:
            raise ValueError("gallery update requires --file or --gallery")
        current = normalize_id(args.gallery)
        additions = tuple(normalize_id(value) for value in args.add)
        removals = tuple(normalize_id(value) for value in args.remove)
    root_id, root, members = _gallery_state(rpc, current)
    update = GalleryUpdate(root_id, current, additions, removals)
    resulting = apply(members, update)
    artifact = create_artifact(
        encode_gallery(update), content_type=MIME_TYPE,
        name=f"{root.name} Gallery Update", collection=root.name,
    )
    prepared = prepare_gallery_successor(rpc, artifact, current=current, fee_rate=args.fee_rate)
    print(f"gallery-root: {root_id}")
    print(f"previous: {current}")
    print(f"add: {len(additions)}")
    print(f"remove: {len(removals)}")
    print(f"members-after: {len(resulting)}")
    _finish_gallery(args, rpc, prepared, label="gallery-successor")


def command_gallery_inspect(args: argparse.Namespace) -> None:
    rpc = rpc_from_args(args, wallet=require_wallet(args))
    root_id, root, members = _gallery_state(rpc, args.gallery)
    print(f"gallery-root: {root_id}")
    print(f"gallery-current: {args.gallery}")
    print(f"gallery-name: {root.name}")
    print(f"members: {len(members)}")
    for member in members:
        print(member)


def command_wallet_batch(args: argparse.Namespace) -> None:
    from .batch import load_batch, split_batch_plan

    batch = load_batch(args.batch)
    wallet = require_wallet(args)
    rpc = rpc_from_args(args, wallet=wallet)
    require_synchronized_index(args, rpc)
    pick_remainders = None
    if batch.mode == "pick-utxo":
        available = {(row["txid"].lower(), int(row["vout"])): row for row in rpc.call("listunspent", 0, 9_999_999, [], True)}
        remainder_flags = []
        for item in batch.items:
            assert item.utxo is not None
            txid, vout = item.utxo.split(":", 1)
            row = available.get((txid.lower(), int(vout)))
            if row is None:
                raise RuntimeError(f"selected UTXO is not spendable: {item.utxo}")
            value = int(Decimal(str(row["amount"])) * Decimal(100_000_000))
            remainder_flags.append(value > item.ownership_bits)
        pick_remainders = tuple(remainder_flags)
    chunks = split_batch_plan(batch, pick_remainders=pick_remainders)
    fingerprint_source = {
        "mode": batch.mode,
        "parent": batch.parent,
        "fee": format(args.fee_rate, "f"),
        "items": [
            {
                "content_sha256": item.artifact.content_sha256,
                "metadata": item.artifact.metadata,
                "collection": item.artifact.collection,
                "destination": item.destination,
                "ownership_bits": item.ownership_bits,
                "utxo": item.utxo,
            }
            for item in batch.items
        ],
        "chunks": [len(chunk.items) for chunk in chunks],
    }
    fingerprint = hashlib.sha256(json.dumps(fingerprint_source, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    print(f"batch: {args.batch}")
    print(f"mode: {batch.mode}")
    print(f"inscriptions: {len(batch.items)}")
    print(f"transactions: {len(chunks)}")
    report_path = args.report or args.batch.with_suffix(".report.json")
    saved = None
    if args.resume:
        if not report_path.exists():
            raise RuntimeError("cannot resume: report does not exist")
        saved = json.loads(report_path.read_text(encoding="utf-8"))
        if "names" in saved and saved["names"] != [item.artifact.name for item in batch.items]:
            raise RuntimeError("cannot resume: reviewed Quantum Names changed")
        if saved.get("fingerprint") != fingerprint:
            raise RuntimeError("cannot resume: batch content, conditions, fee, or chunk plan changed")
        if all(chunk.get("status") == "broadcast" for chunk in saved.get("chunks", [])) and len(saved.get("chunks", [])) == len(chunks):
            print("already broadcast: unchanged batch")
            print(f"report: {report_path}")
            return

    names = [item.artifact.name for item in batch.items]
    ignored = [entry["transaction"] for entry in (saved or {}).get("chunks", []) if entry.get("transaction")]
    require_available_names(args, names, exclude_txids=ignored, rpc=rpc)

    report = saved or {"version": 4, "names": names, "mode": batch.mode, "fingerprint": fingerprint, "fee_bits_vb": format(args.fee_rate, "f"), "chunks": [], "results": []}

    def persist() -> None:
        temporary = report_path.with_name(report_path.name + ".tmp")
        temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        temporary.replace(report_path)

    def send_recoverable(raw: str, expected_txid: str | None, published_names: list[str]) -> str:
        require_synchronized_index(args, rpc)
        ignored = [entry["transaction"] for entry in report["chunks"] if entry.get("transaction")]
        if expected_txid:
            ignored.append(expected_txid)
        require_available_names(args, names, exclude_txids=ignored, rpc=rpc)
        try:
            txid = rpc.call("sendrawtransaction", raw)
        except RpcError:
            txid = expected_txid or rpc.call("decoderawtransaction", raw).get("txid")
            if not txid:
                raise
            try:
                rpc.call("getmempoolentry", txid)
            except RpcError:
                try:
                    rpc.call("gettransaction", txid)
                except RpcError:
                    raise
        remember_pending_names(args, txid, published_names)
        return txid

    def record_chunk(chunk_index, chunk, prepared, txid, status, item_offset):
        successor_ids = (
            [f"{txid}:{position}" for position in range(len(parent_ids(chunk.parent)))]
            if txid and batch.mode == "parented" else []
        )
        entry = {
            "index": chunk_index, "status": status, "transaction": txid or prepared.txid,
            "envelope_bytes": prepared.envelope_bytes, "vsize": prepared.vsize,
            "fee_qbt": prepared.fee_qbt, "allowed": prepared.allowed,
            "parent_successors": successor_ids,
            "signed_hex": prepared.hex,
        }
        while len(report["chunks"]) <= chunk_index:
            report["chunks"].append({"index": len(report["chunks"]), "status": "planned"})
        report["chunks"][chunk_index] = entry
        for local, (item, artifact, address, bits) in enumerate(zip(chunk.items, prepared.artifacts, prepared.owner_addresses, prepared.ownership_bits)):
            result = {
                "index": item_offset + local, "chunk": chunk_index, "file": str(item.file),
                "content_sha256": artifact.content_sha256, "owner": address,
                "ownership_bits": bits, "ownership_output": artifact.ownership_output,
                "transaction": txid or prepared.txid, "allowed": prepared.allowed, "status": status,
            }
            if txid:
                result["quantum"] = f"{txid}:{artifact.ownership_output}"
            report["results"] = [row for row in report["results"] if row.get("index") != item_offset + local]
            report["results"].append(result)
            print(f"[{item_offset + local + 1}/{len(batch.items)}] {item.file.name}: {status} chunk {chunk_index + 1} vout {artifact.ownership_output}")
        report["results"].sort(key=lambda row: row["index"])
        persist()

    if not saved:
        report["chunks"] = [{"index": index, "status": "planned", "inscriptions": len(chunk.items)} for index, chunk in enumerate(chunks)]
        persist()

    # A signed report is written before submission. Resume broadcasts that exact
    # transaction rather than rebuilding it with different inputs or addresses.
    if args.resume and args.broadcast:
        for chunk_index, entry in enumerate(report["chunks"]):
            if entry.get("status") not in ("signed", "awaiting-confirmation") or not entry.get("signed_hex"):
                continue
            raw = entry.get("signed_hex")
            assert raw is not None
            txid = send_recoverable(raw, entry.get("transaction"), [item.artifact.name for item in chunks[chunk_index].items])
            rows = [row for row in report["results"] if row.get("chunk") == chunk_index]
            protected = {int(row["ownership_output"]) for row in rows}
            if batch.mode == "parented":
                protected.update(range(len(parent_ids(batch.parent))))
            rpc.call("lockunspent", False, [{"txid": txid, "vout": vout} for vout in sorted(protected)], True)
            entry["status"] = "broadcast"
            entry["transaction"] = txid
            if batch.mode == "parented":
                entry["parent_successors"] = [
                    f"{txid}:{position}" for position in range(len(parent_ids(batch.parent)))
                ]
            for row in rows:
                row["status"] = "broadcast"
                row["transaction"] = txid
                row["quantum"] = f"{txid}:{row['ownership_output']}"
            persist()
        prior_chunks = report.get("chunks", [])

    prior_chunks = report.get("chunks", [])
    prepared_chunks = []
    if batch.mode == "pick-utxo":
        try:
            for chunk_index, chunk in enumerate(chunks):
                if chunk_index < len(prior_chunks) and prior_chunks[chunk_index].get("status") == "broadcast":
                    prepared_chunks.append(None)
                    continue
                prepared = prepare_batch(rpc, chunk, fee_rate_override=args.fee_rate)
                if not prepared.allowed:
                    raise RuntimeError(prepared.reject_reason or f"transaction chunk {chunk_index + 1} rejected")
                prepared_chunks.append(prepared)
        except Exception:
            for prepared in prepared_chunks:
                if prepared is not None:
                    unlock_prepared_inputs(rpc, prepared)
            raise
        if args.broadcast:
            offset = 0
            for chunk_index, (chunk, prepared) in enumerate(zip(chunks, prepared_chunks)):
                if prepared is not None:
                    record_chunk(chunk_index, chunk, prepared, None, "signed", offset)
                offset += len(chunk.items)
        offset = 0
        for chunk_index, (chunk, prepared) in enumerate(zip(chunks, prepared_chunks)):
            if prepared is None:
                offset += len(chunk.items)
                continue
            txid = None
            status = "dry-run"
            if args.broadcast:
                txid = send_recoverable(prepared.hex, prepared.txid, [artifact.name for artifact in prepared.artifacts])
                protected = sorted(artifact.ownership_output for artifact in prepared.artifacts)
                rpc.call("lockunspent", False, [{"txid": txid, "vout": vout} for vout in protected], True)
                status = "broadcast"
            else:
                unlock_prepared_inputs(rpc, prepared)
            record_chunk(chunk_index, chunk, prepared, txid, status, offset)
            offset += len(chunk.items)
    else:
        if args.broadcast:
            balances = rpc.call("getbalances").get("mine", {})
            available_qbt = sum(Decimal(str(balances.get(key, 0))) for key in ("trusted", "untrusted_pending"))
            available_bits = int(available_qbt * Decimal(100_000_000))
            remaining_chunks = sum(1 for index in range(len(chunks)) if index >= len(prior_chunks) or prior_chunks[index].get("status") != "broadcast")
            # 400,000 vB is the current standard-transaction ceiling. Using it
            # for every remaining chunk is a conservative whole-batch funding
            # preflight before the first transaction is broadcast.
            maximum_fee_bits = int((args.fee_rate * Decimal(400_000)).to_integral_value(rounding=ROUND_CEILING)) * remaining_chunks
            remaining_ownership_bits = sum(
                item.ownership_bits
                for index, chunk in enumerate(chunks)
                if index >= len(prior_chunks) or prior_chunks[index].get("status") != "broadcast"
                for item in chunk.items
            )
            required_bits = remaining_ownership_bits + maximum_fee_bits
            if available_bits < required_bits:
                raise RuntimeError(f"insufficient QBT for complete batch: need at most {required_bits} bits, wallet has {available_bits}")
        current_parent = batch.parent
        offset = 0
        for chunk_index, chunk in enumerate(chunks):
            if chunk_index < len(prior_chunks) and prior_chunks[chunk_index].get("status") == "broadcast":
                current_parent = prior_chunks[chunk_index].get("parent_successor")
                if not current_parent:
                    successors = prior_chunks[chunk_index].get("parent_successors")
                    if successors:
                        current_parent = tuple(successors) if len(successors) > 1 else successors[0]
                if not current_parent:
                    raise RuntimeError("cannot resume: missing parent successor")
                offset += len(chunk.items)
                continue
            runtime_chunk = replace(chunk, parent=current_parent) if batch.mode == "parented" else chunk
            try:
                prepared = prepare_batch(rpc, runtime_chunk, fee_rate_override=args.fee_rate)
            except RpcError as exc:
                reason = str(exc)
                package_limited = any(text in reason for text in (
                    "descendant size limit", "ancestor size limit", "too-long-mempool-chain",
                    "too many unconfirmed ancestors", "too many descendants",
                ))
                if package_limited and args.broadcast:
                    report["chunks"][chunk_index] = {
                        "index": chunk_index,
                        "status": "awaiting-confirmation",
                        "reason": reason,
                        "depends_on": current_parent if batch.mode == "parented" else None,
                        "inscriptions": len(chunk.items),
                    }
                    persist()
                    print(f"chunk {chunk_index + 1} is waiting for its funding or parent chain to confirm; rerun with --resume")
                    break
                raise
            if not prepared.allowed:
                reason = prepared.reject_reason or f"transaction chunk {chunk_index + 1} rejected"
                if "too-long-mempool-chain" in reason or "too many unconfirmed ancestors" in reason:
                    record_chunk(chunk_index, chunk, prepared, None, "awaiting-confirmation", offset)
                    print(f"chunk {chunk_index + 1} is waiting for its funding or parent chain to confirm; rerun with --resume")
                    break
                unlock_prepared_inputs(rpc, prepared)
                raise RuntimeError(reason)
            if not args.broadcast:
                unlock_prepared_inputs(rpc, prepared)
                record_chunk(chunk_index, chunk, prepared, None, "dry-run", offset)
                offset += len(chunk.items)
                if batch.mode == "parented" and chunk_index + 1 < len(chunks):
                    print("remaining parented chunks: planned; signing requires the preceding parent successor")
                    break
                continue
            record_chunk(chunk_index, chunk, prepared, None, "signed", offset)
            txid = send_recoverable(prepared.hex, prepared.txid, [artifact.name for artifact in prepared.artifacts])
            protected = {artifact.ownership_output for artifact in prepared.artifacts}
            if batch.mode == "parented":
                protected.update(range(len(parent_ids(current_parent))))
            rpc.call("lockunspent", False, [{"txid": txid, "vout": vout} for vout in sorted(protected)], True)
            record_chunk(chunk_index, chunk, prepared, txid, "broadcast", offset)
            if batch.mode == "parented":
                successors = tuple(f"{txid}:{position}" for position in range(len(parent_ids(current_parent))))
                current_parent = successors[0] if len(successors) == 1 else successors
            offset += len(chunk.items)
    print(f"report: {report_path}")
    if not args.broadcast:
        print("clean command: success: dry run passed; no batch transaction was broadcast")


def parser() -> argparse.ArgumentParser:
    root = LeapArgumentParser(prog="leap")
    commands = root.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build", help="build a canonical envelope")
    add_content_arguments(build)
    build.add_argument("--out", type=Path, required=True)
    build.set_defaults(function=command_build)

    inspect = commands.add_parser("inspect", help="validate and inspect an envelope")
    inspect.add_argument("envelope", type=Path)
    inspect.set_defaults(function=command_inspect)

    inscribe = commands.add_parser("inscribe", help="prepare or broadcast an inscription")
    add_content_arguments(inscribe)
    add_rpc_arguments(inscribe)
    inscribe.add_argument("--owner", help="existing P2MR destination; default creates one")
    inscribe.add_argument("--ownership-bits", type=int, default=DEFAULT_OWNERSHIP_BITS)
    inscribe.add_argument("--fee", dest="fee_rate", type=decimal_fee_rate, required=True, help="fee rate in bits/vB; decimals accepted")
    add_publication_arguments(inscribe)
    inscribe.set_defaults(function=command_inscribe)

    recover = commands.add_parser("recover", help="recover verified content from a node")
    recover.add_argument("inscription")
    add_rpc_arguments(recover)
    recover.add_argument("--out", type=Path)
    recover.set_defaults(function=command_recover)

    node = commands.add_parser("node", help="inspect the connected Qbit node")
    node_commands = node.add_subparsers(dest="node_command", required=True)
    node_status = node_commands.add_parser("status")
    add_rpc_arguments(node_status)
    node_status.set_defaults(function=command_node_status)

    index = commands.add_parser("index", help="run or inspect the unified Leap index")
    index_commands = index.add_subparsers(dest="index_command", required=True)
    index_status = index_commands.add_parser("status")
    add_rpc_arguments(index_status)
    index_status.add_argument("--data-dir", required=True, type=Path)
    index_status.set_defaults(function=command_index_status)
    index_relationships = index_commands.add_parser("relationships", help="read verified Quantum parents and families without index writes")
    add_rpc_arguments(index_relationships)
    index_relationships.add_argument("--data-dir", required=True, type=Path)
    index_relationships.set_defaults(function=command_index_relationships)
    index_run = index_commands.add_parser("run")
    add_rpc_arguments(index_run)
    index_run.add_argument("--data-dir", required=True, type=Path)
    index_run.add_argument("--poll-seconds", type=float, default=1.0)
    index_run.add_argument("--commit-batch", type=int, default=100)
    index_run.set_defaults(function=command_index_run)

    # Concise, canonical wallet-backed commands.  The older ``leap wallet ...``
    # spellings below remain compatibility aliases for existing scripts.
    balance = commands.add_parser("balance", help="show a selected wallet's balances")
    add_rpc_arguments(balance)
    balance.set_defaults(function=command_wallet_balance)

    receive = commands.add_parser("receive", help="create a receive address for a selected wallet")
    add_rpc_arguments(receive)
    receive.set_defaults(function=command_wallet_receive)

    top_gallery = commands.add_parser("gallery", help="create, update, and inspect Quantum galleries")
    top_gallery_commands = top_gallery.add_subparsers(dest="gallery_command", required=True)

    top_gallery_create = top_gallery_commands.add_parser("create")
    top_gallery_create.add_argument("--file", type=Path, required=True, help="gallery creation YAML")
    top_gallery_create.add_argument("--fee", dest="fee_rate", type=decimal_fee_rate, required=True)
    add_publication_arguments(top_gallery_create)
    add_rpc_arguments(top_gallery_create)
    top_gallery_create.set_defaults(function=command_gallery_create)

    top_gallery_update = top_gallery_commands.add_parser("update")
    top_gallery_update.add_argument("--file", type=Path, help="optional gallery update YAML")
    top_gallery_update.add_argument("--gallery", help="current gallery Quantum ID")
    top_gallery_update.add_argument("--add", action="append", default=[], help="Quantum ID to append; repeat as needed")
    top_gallery_update.add_argument("--remove", action="append", default=[], help="Quantum ID to remove; repeat as needed")
    top_gallery_update.add_argument("--fee", dest="fee_rate", type=decimal_fee_rate, required=True)
    add_publication_arguments(top_gallery_update)
    add_rpc_arguments(top_gallery_update)
    top_gallery_update.set_defaults(function=command_gallery_update)

    top_gallery_inspect = top_gallery_commands.add_parser("inspect")
    top_gallery_inspect.add_argument("--gallery", required=True, help="current gallery Quantum ID")
    add_rpc_arguments(top_gallery_inspect)
    top_gallery_inspect.set_defaults(function=command_gallery_inspect)

    batch = commands.add_parser("batch", help="broadcast or dry-run a multi-Quantum batch")
    batch.add_argument("--batch", type=Path, required=True)
    batch.add_argument("--fee", dest="fee_rate", type=decimal_fee_rate, required=True, help="fee rate in bits/vB; decimals accepted")
    batch.add_argument("--report", type=Path)
    batch.add_argument("--resume", action="store_true")
    add_publication_arguments(batch)
    add_rpc_arguments(batch)
    batch.set_defaults(function=command_wallet_batch)

    wallet = commands.add_parser("wallet", help="manage Qbit wallets and Quantums")
    wallet_commands = wallet.add_subparsers(dest="wallet_command", required=True)

    wallet_list = wallet_commands.add_parser("list")
    add_rpc_arguments(wallet_list)
    wallet_list.set_defaults(function=command_wallet_list)

    wallet_create = wallet_commands.add_parser("create")
    wallet_create.add_argument("name", nargs="?", default="leap")
    add_rpc_arguments(wallet_create)
    wallet_create.set_defaults(function=command_wallet_create)

    wallet_balance = wallet_commands.add_parser("balance")
    add_rpc_arguments(wallet_balance)
    wallet_balance.set_defaults(function=command_wallet_balance)

    wallet_receive = wallet_commands.add_parser("receive")
    add_rpc_arguments(wallet_receive)
    wallet_receive.set_defaults(function=command_wallet_receive)

    wallet_inscribe = wallet_commands.add_parser("inscribe")
    wallet_inscribe.add_argument("--file", type=Path, required=True)
    wallet_inscribe.add_argument("--name", required=True, type=quantum_name, help="required non-blank Quantum Name")
    wallet_inscribe.add_argument("--content-type")
    wallet_inscribe.add_argument("--collection")
    add_rpc_arguments(wallet_inscribe)
    wallet_inscribe.add_argument("--destination")
    wallet_inscribe.add_argument("--ownership-bits", type=int, default=DEFAULT_OWNERSHIP_BITS)
    wallet_inscribe.add_argument("--fee", dest="fee_rate", type=decimal_fee_rate, required=True, help="fee rate in bits/vB; decimals accepted")
    add_publication_arguments(wallet_inscribe)
    wallet_inscribe.set_defaults(function=command_wallet_inscribe)

    gallery = wallet_commands.add_parser("gallery", help="create, update, and inspect Quantum galleries")
    gallery_commands = gallery.add_subparsers(dest="gallery_command", required=True)

    gallery_create = gallery_commands.add_parser("create")
    gallery_create.add_argument("--file", type=Path, required=True, help="gallery creation YAML")
    gallery_create.add_argument("--fee", dest="fee_rate", type=decimal_fee_rate, required=True)
    add_publication_arguments(gallery_create)
    add_rpc_arguments(gallery_create)
    gallery_create.set_defaults(function=command_gallery_create)

    gallery_update = gallery_commands.add_parser("update")
    gallery_update.add_argument("--file", type=Path, help="optional gallery update YAML")
    gallery_update.add_argument("--gallery", help="current gallery Quantum ID")
    gallery_update.add_argument("--add", action="append", default=[], help="Quantum ID to append; repeat as needed")
    gallery_update.add_argument("--remove", action="append", default=[], help="Quantum ID to remove; repeat as needed")
    gallery_update.add_argument("--fee", dest="fee_rate", type=decimal_fee_rate, required=True)
    add_publication_arguments(gallery_update)
    add_rpc_arguments(gallery_update)
    gallery_update.set_defaults(function=command_gallery_update)

    gallery_inspect = gallery_commands.add_parser("inspect")
    gallery_inspect.add_argument("--gallery", required=True, help="current gallery Quantum ID")
    add_rpc_arguments(gallery_inspect)
    gallery_inspect.set_defaults(function=command_gallery_inspect)

    wallet_batch = wallet_commands.add_parser("batch")
    wallet_batch.add_argument("--batch", type=Path, required=True)
    wallet_batch.add_argument("--fee", dest="fee_rate", type=decimal_fee_rate, required=True, help="fee rate in bits/vB; decimals accepted")
    wallet_batch.add_argument("--report", type=Path)
    wallet_batch.add_argument("--resume", action="store_true")
    add_publication_arguments(wallet_batch)
    add_rpc_arguments(wallet_batch)
    wallet_batch.set_defaults(function=command_wallet_batch)
    return root


def main() -> None:
    args = None
    try:
        args = parser().parse_args()
        args.function(args)
    except (EnvelopeError, RpcError, RuntimeError, ValueError, OSError) as exc:
        print(f"bad command: error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
