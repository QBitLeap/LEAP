# LEAP

Pre-alpha Quantum inscriptions, Galleries and a persistent Qbit indexer.
LEAP validates canonical envelopes, tracks permanent Quantum IDs through
ordinary UTXO spends and supplies CLI transaction preparation/recovery.
A Quantum is identified by its creation transaction and ownership output.
Rendering untrusted content is outside protocol validity and requires an
isolated viewer.

## Requirements

Python 3.11 or newer, PyYAML 6 or newer and Qbit Core. Node-backed commands
can use qbit-cli or HTTP RPC. Wallet operations require a separately managed,
loaded P2MR wallet; the read-only index follower does not need wallet access.
macOS, Windows and Linux release artifacts and the separate Umbrel installer
are planned; native release acceptance is still pending.

## Quick start

Create a Python virtual environment with your platform's Python command:

```sh
python -m venv .venv
```

Activate the environment, then install this checkout:

```sh
python -m pip install .
leap --help
```

Activate the environment or use its interpreter/executables explicitly. On
Windows these are in .venv\Scripts; on macOS/Linux they are in .venv/bin.
Run node and index commands with paths for your own selected network:

```sh
leap node status --chain testnet4
leap index run --chain testnet4 --data-dir /path/to/leap/index/testnet4
```

Mainnet is the default when --chain is omitted; testnet4 and regtest are
explicit alternatives. The format is pre-alpha: production use and release
readiness require further protocol and native platform review.

Read [Quantum examples](Guides/Quantums/README.md), the
[protocol specification](PROTOCOL.md), [index layout](docs/INDEXER.md) and
[read-only relationships](docs/RELATIONSHIPS.md). Every command has contextual
help, including leap inscribe --help and leap gallery update --help.
Transaction commands publish by default; --dry-run prepares, signs and
preflights without broadcasting. Every new Quantum requires a non-blank name.
Names are checked against the selected chain and pending transaction catalog;
collisions are rejected with a suggestion, never silently renamed.

## Public distribution scope

This distribution includes Quantums, Galleries and required shared indexing.
It excludes experimental fungible-asset implementations and private consensus
forks. Legacy disabled-only schema slots remain for compatibility, as described
in the index layout. No private runtime data, wallet, credentials or previous
local Git history belongs in this source distribution.

## Tests

```sh
python -m unittest discover -s tests
```

Tests use generated fixtures and mock RPC, not live signing or publication.
Native platform packages and Umbrel install/lifecycle checks are separate gates.
See LICENSE for the preserved project license.
