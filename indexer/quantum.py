"""Sparse, deterministic location index for Leap Quantums."""

from __future__ import annotations

from dataclasses import dataclass


class QuantumIndexError(ValueError):
    """Block data cannot produce deterministic Quantum state."""


@dataclass(frozen=True, order=True)
class OutPoint:
    txid: str
    vout: int

    def __post_init__(self) -> None:
        if len(self.txid) != 64:
            raise QuantumIndexError("transaction id must contain 64 hexadecimal characters")
        try:
            bytes.fromhex(self.txid)
        except ValueError as exc:
            raise QuantumIndexError("transaction id must be hexadecimal") from exc
        if self.vout < 0:
            raise QuantumIndexError("output number must be non-negative")


@dataclass(frozen=True)
class Location:
    outpoint: OutPoint
    offset: int

    def __post_init__(self) -> None:
        if self.offset < 0:
            raise QuantumIndexError("bit offset must be non-negative")


@dataclass(frozen=True)
class TxInput:
    previous: OutPoint
    value: int


@dataclass(frozen=True)
class TxOutput:
    value: int
    script: bytes = b""
    address: str | None = None


@dataclass(frozen=True)
class Transaction:
    txid: str
    inputs: tuple[TxInput, ...]
    outputs: tuple[TxOutput, ...]


@dataclass(frozen=True)
class Block:
    height: int
    block_hash: str
    previous_hash: str | None
    subsidy: int
    coinbase: Transaction
    transactions: tuple[Transaction, ...]


def _validate_value(value: int, label: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise QuantumIndexError(f"{label} must be a non-negative integer number of bits")


def _output_location(tx: Transaction, position: int) -> Location | None:
    cursor = 0
    for vout, output in enumerate(tx.outputs):
        _validate_value(output.value, "output value")
        end = cursor + output.value
        if position < end:
            return Location(OutPoint(tx.txid, vout), position - cursor)
        cursor = end
    return None


class QuantumIndexer:
    """Track known Quantum IDs without constructing a global bit index."""

    def __init__(self) -> None:
        self.tip_height = -1
        self.tip_hash: str | None = None
        self.locations: dict[str, Location] = {}
        self.burned: set[str] = set()
        self._at_outpoint: dict[OutPoint, dict[str, int]] = {}
        self.addresses: dict[str, str | None] = {}
        self._undo: list[tuple[int, str | None, dict[str, Location], set[str], dict[str, str | None]]] = []

    def location(self, quantum_id: str) -> Location | None:
        return self.locations.get(quantum_id)

    def is_burned(self, quantum_id: str) -> bool:
        return quantum_id in self.burned

    def at_outpoint(self, outpoint: OutPoint) -> tuple[str, ...]:
        entries = self._at_outpoint.get(outpoint, {})
        return tuple(sorted(entries, key=lambda quantum_id: (entries[quantum_id], quantum_id)))

    def at_address(self, address: str) -> tuple[str, ...]:
        """Return every Quantum currently resting at outputs for ``address``."""

        return tuple(sorted(quantum_id for quantum_id, owner in self.addresses.items() if owner == address))

    def address_groups(self, address: str, galleries: dict[str, str | None]) -> tuple[tuple[str | None, tuple[str, ...]], ...]:
        """Group an address view by Gallery without hiding individual IDs."""

        grouped: dict[str | None, list[str]] = {}
        for quantum_id in self.at_address(address):
            grouped.setdefault(galleries.get(quantum_id), []).append(quantum_id)
        return tuple(
            (gallery, tuple(ids))
            for gallery, ids in sorted(grouped.items(), key=lambda item: (item[0] is None, item[0] or ""))
        )

    def add(self, quantum_id: str, location: Location, *, address: str | None = None) -> None:
        if quantum_id in self.locations or quantum_id in self.burned:
            raise QuantumIndexError("Quantum ID is already indexed")
        self._set_location(quantum_id, location, address)

    def _set_location(self, quantum_id: str, location: Location, address: str | None = None) -> None:
        previous = self.locations.get(quantum_id)
        if previous is not None:
            bucket = self._at_outpoint[previous.outpoint]
            del bucket[quantum_id]
            if not bucket:
                del self._at_outpoint[previous.outpoint]
        self.locations[quantum_id] = location
        self.burned.discard(quantum_id)
        self.addresses[quantum_id] = address
        self._at_outpoint.setdefault(location.outpoint, {})[quantum_id] = location.offset

    def _burn(self, quantum_id: str) -> None:
        previous = self.locations.pop(quantum_id)
        bucket = self._at_outpoint[previous.outpoint]
        del bucket[quantum_id]
        if not bucket:
            del self._at_outpoint[previous.outpoint]
        self.addresses.pop(quantum_id, None)
        self.burned.add(quantum_id)

    def _restore(self, locations: dict[str, Location], burned: set[str],
                 addresses: dict[str, str | None]) -> None:
        self.locations = dict(locations)
        self.burned = set(burned)
        self.addresses = dict(addresses)
        self._at_outpoint = {}
        for quantum_id, location in self.locations.items():
            self._at_outpoint.setdefault(location.outpoint, {})[quantum_id] = location.offset

    def connect(self, block: Block) -> None:
        if block.height != self.tip_height + 1:
            raise QuantumIndexError("block height does not extend the current tip")
        if self.tip_hash is not None and block.previous_hash != self.tip_hash:
            raise QuantumIndexError("block does not extend the current tip hash")
        _validate_value(block.subsidy, "block subsidy")

        before = dict(self.locations)
        before_burned = set(self.burned)
        before_addresses = dict(self.addresses)
        old_height, old_hash = self.tip_height, self.tip_hash
        fee_quantums: list[tuple[str, int]] = []
        fees_before = 0
        try:
            for tx in block.transactions:
                input_total = 0
                tracked_positions: list[tuple[str, int]] = []
                for txin in tx.inputs:
                    _validate_value(txin.value, "input value")
                    # Copy because moving the first Quantum may remove or mutate
                    # this reverse-index bucket. Coinbase outputs and accidental
                    # wallet merges can legitimately contain several Quantums.
                    for quantum_id, offset in tuple(self._at_outpoint.get(txin.previous, {}).items()):
                        if offset >= txin.value:
                            raise QuantumIndexError("indexed Quantum offset exceeds spent output value")
                        tracked_positions.append((quantum_id, input_total + offset))
                    input_total += txin.value

                for output in tx.outputs:
                    _validate_value(output.value, "output value")
                output_total = sum(output.value for output in tx.outputs)
                if output_total > input_total:
                    raise QuantumIndexError("transaction outputs exceed inputs")

                for quantum_id, position in tracked_positions:
                    destination = _output_location(tx, position)
                    if destination is None:
                        fee_quantums.append((quantum_id, fees_before + position - output_total))
                    else:
                        self._set_location(quantum_id, destination, tx.outputs[destination.outpoint.vout].address)
                fees_before += input_total - output_total

            for output in block.coinbase.outputs:
                _validate_value(output.value, "coinbase output value")
            coinbase_total = sum(output.value for output in block.coinbase.outputs)
            if coinbase_total > block.subsidy + fees_before:
                raise QuantumIndexError("coinbase outputs exceed subsidy and transaction fees")

            for quantum_id, fee_offset in fee_quantums:
                destination = _output_location(block.coinbase, block.subsidy + fee_offset)
                if destination is None:
                    self._burn(quantum_id)
                else:
                    self._set_location(quantum_id, destination, block.coinbase.outputs[destination.outpoint.vout].address)
        except Exception:
            self._restore(before, before_burned, before_addresses)
            raise

        self._undo.append((old_height, old_hash, before, before_burned, before_addresses))
        self.tip_height = block.height
        self.tip_hash = block.block_hash

    def disconnect(self) -> None:
        if not self._undo:
            raise QuantumIndexError("there is no connected block to disconnect")
        height, block_hash, locations, burned, addresses = self._undo.pop()
        self._restore(locations, burned, addresses)
        self.tip_height = height
        self.tip_hash = block_hash
