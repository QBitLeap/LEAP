# Quantum guides

These commands run from the repository root on the current public Qbit
**testnet4**. After the [Quick start](../../README.md#quick-start) installation,
set the existing testnet4 node, wallet, and synchronized Leap index paths:

```bash
export PATH="$PWD/.venv/bin:$PATH"
export LEAP_WALLET=leap-testnet4
export LEAP_QBIT_CLI="/path/to/qbit-cli"
export LEAP_DATADIR="/path/to/qbit/testnet4"
export LEAP_INDEX_DIR="/path/to/leap/index/testnet4"
```

`--fee 1` is a fee **rate** of 1 bit/vB; adjust it to current node policy.

## One Quantum: direct CLI or YAML

The [parent.txt](content/parent.txt) file contains `FIRST PARENT!`. The
single-inscription CLI takes it directly:

```bash
leap inscribe --chain testnet4 --wallet "$LEAP_WALLET" --qbit-cli "$LEAP_QBIT_CLI" --datadir "$LEAP_DATADIR" --index-data-dir "$LEAP_INDEX_DIR" --fee 1 --name parent.txt Guides/Quantums/content/parent.txt
```

To deploy one Quantum from YAML, use [single.yaml](single.yaml), which contains
one entry pointing to `content/first.txt`:

```bash
leap batch --chain testnet4 --wallet "$LEAP_WALLET" --qbit-cli "$LEAP_QBIT_CLI" --datadir "$LEAP_DATADIR" --index-data-dir "$LEAP_INDEX_DIR" --batch Guides/Quantums/single.yaml --fee 1 --report quantum-single.quantum-report.json
```

Both commands broadcast by default. The direct command creates the parent;
the one-item YAML command independently creates a single Quantum and writes a
resumable report.

## YAML batch of Quantums

[batch-parentless.yaml](batch-parentless.yaml) is an executable two-Quantum
manifest. Its `file` paths are relative to the YAML file. Broadcast it with:

```bash
leap batch --chain testnet4 --wallet "$LEAP_WALLET" --qbit-cli "$LEAP_QBIT_CLI" --datadir "$LEAP_DATADIR" --index-data-dir "$LEAP_INDEX_DIR" --batch Guides/Quantums/batch-parentless.yaml --fee 1 --report quantum-parentless.quantum-report.json
```

To make the two Quantums children of the `FIRST PARENT!` Quantum, use
[batch-parented.yaml](batch-parented.yaml). Its `parent:` value must be the
current wallet-controlled testnet4 ownership outpoint of that confirmed
Quantum. After a parented broadcast, replace the value with the parent
successor outpoint from the batch report before running it again:

```bash
leap batch --chain testnet4 --wallet "$LEAP_WALLET" --qbit-cli "$LEAP_QBIT_CLI" --datadir "$LEAP_DATADIR" --index-data-dir "$LEAP_INDEX_DIR" --batch Guides/Quantums/batch-parented.yaml --fee 1 --report quantum-parented.quantum-report.json
```

The commented `parent:` line in the parentless YAML shows where the optional
field goes. A filled `parent:` requires `mode: parented`; omit it with
`mode: parentless`.

Each batch broadcasts by default and writes its resumable JSON report at
the path passed to `--report`, even for a dry run. Use a fresh report path for a
publication run, then use `--resume` with that report after an interrupted
publication. The batch output gives each confirmed Quantum ID as
`<txid>:<ownership-vout>`.

## YAML Gallery

[gallery.yaml](gallery.yaml) creates an empty Gallery. It is
valid without preexisting Quantum IDs; add confirmed IDs later. Broadcast it with:

```bash
leap gallery create --chain testnet4 --wallet "$LEAP_WALLET" --qbit-cli "$LEAP_QBIT_CLI" --datadir "$LEAP_DATADIR" --index-data-dir "$LEAP_INDEX_DIR" --file Guides/Quantums/gallery.yaml --fee 1
```

After confirmation, set `LEAP_GALLERY_ID` to
the current Gallery ID and `LEAP_QUANTUM_ID` to a confirmed member ID. Then add
the Quantum using:

```bash
leap gallery update --chain testnet4 --wallet "$LEAP_WALLET" --qbit-cli "$LEAP_QBIT_CLI" --datadir "$LEAP_DATADIR" --index-data-dir "$LEAP_INDEX_DIR" --gallery "$LEAP_GALLERY_ID" --add "$LEAP_QUANTUM_ID" --fee 1
```

