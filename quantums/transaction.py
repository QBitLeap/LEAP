from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from .envelope import Artifact, EnvelopeError, decode_any, encode, encode_batch, parent_ids, validate_creation_artifact
from .rpc import Rpc, RpcError
from .script import extract_single_push, quantum_op_return


BITS_PER_QBT = Decimal(100_000_000)
DEFAULT_OWNERSHIP_BITS = 4_000


@dataclass(frozen=True)
class PreparedInscription:
    owner_address: str
    ownership_bits: int
    envelope_bytes: int
    hex: str
    fee_qbt: str
    vsize: int
    allowed: bool
    reject_reason: str | None
    psbt: str | None = None
    txid: str | None = None
    inputs: tuple[tuple[str, int], ...] = ()
    protected_inputs: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class PreparedBatch:
    artifacts: tuple[Artifact, ...]
    owner_addresses: tuple[str, ...]
    ownership_bits: tuple[int, ...]
    envelope_bytes: int
    hex: str
    fee_qbt: str
    vsize: int
    allowed: bool
    reject_reason: str | None
    txid: str | None
    inputs: tuple[tuple[str, int], ...]
    protected_inputs: tuple[tuple[str, int], ...] = ()


def bits_to_qbt(bits: int) -> str:
    if bits <= 0:
        raise ValueError("ownership value must be positive")
    return format(Decimal(bits) / BITS_PER_QBT, "f")


def _value_bits(output: dict[str, Any]) -> int:
    return int(Decimal(str(output.get("value", 0))) * BITS_PER_QBT)


def verify_creation_transaction(
    transaction: dict[str, Any], artifact: Artifact, owner_address: str, ownership_bits: int
) -> None:
    outputs = transaction.get("vout")
    if not isinstance(outputs, list) or len(outputs) < 2:
        raise EnvelopeError("creation transaction requires at least two outputs")
    owner = outputs[0]
    owner_script = owner.get("scriptPubKey", {})
    if owner_script.get("type") not in ("witness_v2_mast", "witness_v2_p2mr", "v2_p2mr"):
        raise EnvelopeError("ownership output is not P2MR")
    if _value_bits(owner) != ownership_bits:
        raise EnvelopeError("ownership output value changed")
    addresses = owner_script.get("address") or owner_script.get("addresses")
    if isinstance(addresses, list):
        matches_owner = owner_address in addresses
    else:
        matches_owner = addresses == owner_address
    if not matches_owner:
        raise EnvelopeError("ownership destination changed")
    data_output = outputs[1]
    if _value_bits(data_output) != 0:
        raise EnvelopeError("Quantum data output must have zero value")
    script_hex = data_output.get("scriptPubKey", {}).get("hex")
    if not isinstance(script_hex, str):
        raise EnvelopeError("Quantum data output has no script")
    expected = quantum_op_return(encode(artifact))
    if bytes.fromhex(script_hex) != expected:
        raise EnvelopeError("Quantum envelope or output position changed")


def _address_matches(output: dict[str, Any], address: str) -> bool:
    script = output.get("scriptPubKey", {})
    addresses = script.get("address") or script.get("addresses")
    return address in addresses if isinstance(addresses, list) else addresses == address


def verify_batch_transaction(
    transaction: dict[str, Any], artifacts: tuple[Artifact, ...], addresses: tuple[str, ...], ownership: tuple[int, ...]
) -> None:
    outputs = transaction.get("vout")
    if not isinstance(outputs, list):
        raise EnvelopeError("batch transaction has no outputs")
    for artifact, address, bits in zip(artifacts, addresses, ownership):
        if artifact.ownership_output >= len(outputs):
            raise EnvelopeError("Quantum ownership output is missing")
        output = outputs[artifact.ownership_output]
        script_type = output.get("scriptPubKey", {}).get("type")
        if script_type not in ("witness_v2_mast", "witness_v2_p2mr", "v2_p2mr"):
            raise EnvelopeError("Quantum ownership output is not P2MR")
        if _value_bits(output) != bits or not _address_matches(output, address):
            raise EnvelopeError("Quantum ownership output changed")
    expected = quantum_op_return(encode_batch(artifacts))
    matches = []
    for output in outputs:
        script_hex = output.get("scriptPubKey", {}).get("hex")
        if isinstance(script_hex, str) and bytes.fromhex(script_hex) == expected:
            matches.append(output)
    if len(matches) != 1 or _value_bits(matches[0]) != 0:
        raise EnvelopeError("canonical Quantum batch data output is missing or duplicated")


def _sign_and_test(rpc: Rpc, funded: dict[str, Any], verify) -> tuple[dict[str, Any], dict[str, Any], tuple[tuple[str, int], ...]]:
    unsigned = rpc.call("decodepsbt", funded["psbt"])
    locked = tuple((item["txid"], int(item["vout"])) for item in unsigned["tx"].get("vin", []))
    try:
        verify(unsigned["tx"])
        signed = rpc.call("walletprocesspsbt", funded["psbt"], True, "ALL", True)
        if not signed.get("complete") or not signed.get("hex"):
            raise RpcError("wallet did not completely sign and finalize the PSBT")
        decoded = rpc.call("decoderawtransaction", signed["hex"])
        verify(decoded)
        result = rpc.call("testmempoolaccept", [signed["hex"]])[0]
        return signed, {**decoded, "_accept": result}, locked
    except Exception:
        if locked:
            try:
                rpc.call("lockunspent", True, [{"txid": txid, "vout": vout} for txid, vout in locked])
            except RpcError:
                pass
        raise


def _fee_rate(value: Decimal | int | str) -> str:
    try:
        rate = Decimal(str(value))
    except Exception as exc:
        raise ValueError("fee rate must be a decimal number") from exc
    if not rate.is_finite() or rate <= 0:
        raise ValueError("fee rate must be positive and finite")
    return format(rate, "f")


def prepare_batch(rpc: Rpc, batch, *, fee_rate_override: Decimal | int | str | None = None) -> PreparedBatch:
    if not batch.items:
        raise EnvelopeError("batch is empty")
    for item in batch.items:
        validate_creation_artifact(item.artifact)
    mode = batch.mode
    owner_addresses = tuple(item.destination or rpc.call("getnewaddress", "", "p2mr") for item in batch.items)
    if len(set(owner_addresses)) != len(owner_addresses):
        raise EnvelopeError("each Quantum in one transaction requires a unique destination address")
    ownership = tuple(item.ownership_bits for item in batch.items)
    if any(bits < 2_855 for bits in ownership):
        raise EnvelopeError("ownership output is below Qbit's default P2MR dust threshold")
    explicit_inputs: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    positions: list[int] = []
    parent_checks: list[tuple[str, int, str, int]] = []
    protected_inputs: tuple[tuple[str, int], ...] = ()
    pick_fee_output: int | None = None
    selected_values: list[int] = []

    if mode == "parented":
        assert batch.parent is not None
        for parent in parent_ids(batch.parent):
            parent_txid, parent_vout_text = parent.split(":", 1)
            parent_vout = int(parent_vout_text)
            parent_coin = rpc.call("gettxout", parent_txid, parent_vout, True)
            if not parent_coin:
                raise EnvelopeError("parent ownership outpoint is spent or unknown")
            parent_script = parent_coin.get("scriptPubKey", {})
            parent_address = parent_script.get("address")
            if parent_script.get("type") not in ("witness_v2_mast", "witness_v2_p2mr", "v2_p2mr") or not parent_address:
                raise EnvelopeError("parent ownership output is not addressable P2MR")
            parent_bits = int(Decimal(str(parent_coin["value"])) * BITS_PER_QBT)
            explicit_inputs.append({"txid": parent_txid, "vout": parent_vout})
            outputs.append({parent_address: bits_to_qbt(parent_bits)})
            parent_checks.append((parent_txid, parent_vout, parent_address, parent_bits))
        protected_inputs = tuple((txid, vout) for txid, vout, _, _ in parent_checks)
        for address, bits in zip(owner_addresses, ownership):
            positions.append(len(outputs))
            outputs.append({address: bits_to_qbt(bits)})
    elif mode == "pick-utxo":
        available = {(row["txid"], int(row["vout"])): row for row in rpc.call("listunspent", 0, 9_999_999, [], True)}
        seen: set[tuple[str, int]] = set()
        for item, address, bits in zip(batch.items, owner_addresses, ownership):
            assert item.utxo is not None
            txid, vout_text = item.utxo.split(":", 1)
            outpoint = (txid.lower(), int(vout_text))
            if outpoint in seen or outpoint not in available:
                raise EnvelopeError("selected UTXO is duplicated or not spendable by this wallet")
            seen.add(outpoint)
            value = int(Decimal(str(available[outpoint]["amount"])) * BITS_PER_QBT)
            if value < bits:
                raise EnvelopeError("selected UTXO is smaller than its Quantum ownership output")
            explicit_inputs.append({"txid": outpoint[0], "vout": outpoint[1]})
            selected_values.append(value)
            positions.append(len(outputs))
            outputs.append({address: bits_to_qbt(bits)})
            remainder = value - bits
            if remainder:
                remainder_address = rpc.call("getnewaddress", "", "p2mr")
                outputs.append({remainder_address: bits_to_qbt(remainder)})
                pick_fee_output = len(outputs) - 1
    else:
        for address, bits in zip(owner_addresses, ownership):
            positions.append(len(outputs))
            outputs.append({address: bits_to_qbt(bits)})

    artifacts = tuple(replace(item.artifact, ownership_output=position) for item, position in zip(batch.items, positions))
    payload = encode_batch(artifacts)
    outputs.append({"data": payload.hex()})
    if fee_rate_override is None:
        raise ValueError("batch requires an explicit fee rate")
    fee_rate = _fee_rate(fee_rate_override)
    options = {
        "add_inputs": True,
        "changePosition": len(outputs),
        "change_type": "p2mr",
        "fee_rate": fee_rate,
        "lockUnspents": True,
        "replaceable": True,
    }
    if mode == "pick-utxo" and pick_fee_output is not None:
        options["add_inputs"] = False
        options["subtractFeeFromOutputs"] = [pick_fee_output]
    funded = rpc.call("walletcreatefundedpsbt", explicit_inputs, outputs, 0, options)

    def verify(transaction: dict[str, Any]) -> None:
        verify_batch_transaction(transaction, artifacts, owner_addresses, ownership)
        vin = transaction.get("vin", [])
        expected_inputs = [(item["txid"], item["vout"]) for item in explicit_inputs]
        actual_prefix = [(item.get("txid"), int(item.get("vout", -1))) for item in vin[:len(expected_inputs)]]
        if actual_prefix != expected_inputs:
            raise EnvelopeError("wallet changed required input order")
        if mode == "pick-utxo":
            preceding_inputs = 0
            for position, selected_value in zip(positions, selected_values):
                preceding_outputs = sum(_value_bits(output) for output in transaction["vout"][:position])
                if preceding_outputs != preceding_inputs:
                    raise EnvelopeError("selected first bit was not routed to ownership offset zero")
                preceding_inputs += selected_value
        for position, (_, _, address, bits) in enumerate(parent_checks):
            successor = transaction["vout"][position]
            if _value_bits(successor) != bits or not _address_matches(successor, address):
                raise EnvelopeError("parent successor output changed")

    try:
        signed, decoded, locked = _sign_and_test(rpc, funded, verify)
    except Exception:
        if protected_inputs:
            rpc.call("lockunspent", False, [{"txid": txid, "vout": vout} for txid, vout in protected_inputs], True)
        raise
    accepted = decoded.pop("_accept")
    return PreparedBatch(
        artifacts, owner_addresses, ownership, len(payload), signed["hex"], str(funded["fee"]),
        int(accepted.get("vsize", 0)), bool(accepted.get("allowed")), accepted.get("reject-reason"),
        decoded.get("txid"), locked, protected_inputs,
    )


def prepare_inscription(
    rpc: Rpc,
    artifact: Artifact,
    *,
    owner_address: str | None = None,
    ownership_bits: int = DEFAULT_OWNERSHIP_BITS,
    fee_rate: Decimal | int | str,
) -> PreparedInscription:
    validate_creation_artifact(artifact)
    if ownership_bits < 2_855:
        raise EnvelopeError("ownership output is below Qbit's default P2MR dust threshold")
    fee_rate_text = _fee_rate(fee_rate)
    payload = encode(artifact)
    address = owner_address or rpc.call("getnewaddress", "", "p2mr")
    outputs = [
        {address: bits_to_qbt(ownership_bits)},
        {"data": payload.hex()},
    ]
    funded = rpc.call(
        "walletcreatefundedpsbt",
        [],
        outputs,
        0,
        {
            "add_inputs": True,
            "changePosition": 2,
            "change_type": "p2mr",
            "fee_rate": fee_rate_text,
            "lockUnspents": True,
            "replaceable": True,
        },
    )
    unsigned = rpc.call("decodepsbt", funded["psbt"])
    locked_inputs = tuple(
        (item["txid"], int(item["vout"])) for item in unsigned["tx"].get("vin", [])
    )
    try:
        verify_creation_transaction(unsigned["tx"], artifact, address, ownership_bits)
        signed = rpc.call("walletprocesspsbt", funded["psbt"], True, "ALL", True)
        if not signed.get("complete") or not signed.get("hex"):
            raise RpcError("wallet did not completely sign and finalize the PSBT")
        decoded = rpc.call("decoderawtransaction", signed["hex"])
        verify_creation_transaction(decoded, artifact, address, ownership_bits)
        result = rpc.call("testmempoolaccept", [signed["hex"]])[0]
    except Exception:
        if locked_inputs:
            try:
                rpc.call(
                    "lockunspent",
                    True,
                    [{"txid": txid, "vout": vout} for txid, vout in locked_inputs],
                )
            except RpcError:
                pass
        raise
    return PreparedInscription(
        owner_address=address,
        ownership_bits=ownership_bits,
        envelope_bytes=len(payload),
        hex=signed["hex"],
        fee_qbt=str(funded["fee"]),
        vsize=int(result.get("vsize", 0)),
        allowed=bool(result.get("allowed")),
        reject_reason=result.get("reject-reason"),
        psbt=signed.get("psbt", funded["psbt"]),
        txid=decoded.get("txid"),
        inputs=locked_inputs,
    )


def prepare_gallery_successor(
    rpc: Rpc,
    artifact: Artifact,
    *,
    current: str,
    fee_rate: Decimal | int | str,
) -> PreparedInscription:
    """Spend and recreate gallery control while committing one update Quantum."""
    validate_creation_artifact(artifact)
    txid, vout_text = current.split(":", 1)
    vout = int(vout_text)
    coin = rpc.call("gettxout", txid, vout, True)
    if not coin:
        raise EnvelopeError("current gallery ownership outpoint is spent or unknown")
    script = coin.get("scriptPubKey", {})
    address = script.get("address")
    if script.get("type") not in ("witness_v2_mast", "witness_v2_p2mr", "v2_p2mr") or not address:
        raise EnvelopeError("current gallery ownership output is not addressable P2MR")
    bits = int(Decimal(str(coin["value"])) * BITS_PER_QBT)
    payload = encode(artifact)
    funded = rpc.call(
        "walletcreatefundedpsbt",
        [{"txid": txid, "vout": vout}],
        [{address: bits_to_qbt(bits)}, {"data": payload.hex()}],
        0,
        {
            "add_inputs": True,
            "changePosition": 2,
            "change_type": "p2mr",
            "fee_rate": _fee_rate(fee_rate),
            "lockUnspents": True,
            "replaceable": True,
        },
    )

    def verify(transaction: dict[str, Any]) -> None:
        verify_creation_transaction(transaction, artifact, address, bits)
        vin = transaction.get("vin", [])
        if not vin or (vin[0].get("txid"), int(vin[0].get("vout", -1))) != (txid, vout):
            raise EnvelopeError("wallet changed the gallery control input")

    protected = ((txid, vout),)
    try:
        signed, decoded, locked = _sign_and_test(rpc, funded, verify)
    except Exception:
        rpc.call("lockunspent", False, [{"txid": txid, "vout": vout}], True)
        raise
    accepted = decoded.pop("_accept")
    return PreparedInscription(
        address, bits, len(payload), signed["hex"], str(funded["fee"]),
        int(accepted.get("vsize", 0)), bool(accepted.get("allowed")), accepted.get("reject-reason"),
        signed.get("psbt", funded["psbt"]), decoded.get("txid"), locked, protected,
    )


def unlock_prepared_inputs(rpc: Rpc, prepared: PreparedInscription | PreparedBatch) -> None:
    if prepared.inputs:
        rpc.call(
            "lockunspent",
            True,
            [{"txid": txid, "vout": vout} for txid, vout in prepared.inputs],
        )
    protected = getattr(prepared, "protected_inputs", ())
    if protected:
        rpc.call("lockunspent", False, [{"txid": txid, "vout": vout} for txid, vout in protected], True)


def recover_from_transaction(transaction: dict[str, Any], ownership_output: int = 0) -> Artifact:
    outputs = transaction.get("vout")
    if not isinstance(outputs, list) or ownership_output >= len(outputs):
        raise EnvelopeError("ownership output does not exist")
    owner = outputs[ownership_output]
    owner_script = owner.get("scriptPubKey", owner.get("scriptpubkey", {}))
    if isinstance(owner_script, dict):
        owner_type = owner_script.get("type")
    else:
        owner_type = owner.get("scriptpubkey_type")
    if owner_type not in ("witness_v2_mast", "witness_v2_p2mr", "v2_p2mr"):
        raise EnvelopeError("ownership output is not P2MR")
    for output in outputs:
        script_data = output.get("scriptPubKey", output.get("scriptpubkey"))
        script_hex = script_data.get("hex") if isinstance(script_data, dict) else script_data
        if not isinstance(script_hex, str):
            continue
        try:
            payload = extract_single_push(bytes.fromhex(script_hex))
            artifacts = decode_any(payload)
        except (ValueError, EnvelopeError):
            continue
        for artifact in artifacts:
            if artifact.ownership_output == ownership_output:
                return artifact
    raise EnvelopeError("no matching Quantum envelope in transaction")
