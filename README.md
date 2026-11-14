# LEAP

| macOS | Windows | Linux | Umbrel |
| --- | --- | --- | --- |
| [<img alt="LEAP for macOS" src="https://raw.githubusercontent.com/QBitLeap/umbrel-app-store/f02d8c427c868dd1fbbf06bc70b1cea238bca807/assets/platform-icons/leap/macos.png" width="72">](https://github.com/QBitLeap/LEAP/releases/latest#macos) | [<img alt="LEAP for Windows" src="https://raw.githubusercontent.com/QBitLeap/umbrel-app-store/f02d8c427c868dd1fbbf06bc70b1cea238bca807/assets/platform-icons/leap/windows.png" width="72">](https://github.com/QBitLeap/LEAP/releases/latest#windows) | [<img alt="LEAP for Linux" src="https://raw.githubusercontent.com/QBitLeap/umbrel-app-store/f02d8c427c868dd1fbbf06bc70b1cea238bca807/assets/platform-icons/leap/linux.png" width="72">](https://github.com/QBitLeap/LEAP/releases/latest#linux) | [<img alt="LEAP for Umbrel" src="https://raw.githubusercontent.com/QBitLeap/umbrel-app-store/f02d8c427c868dd1fbbf06bc70b1cea238bca807/assets/platform-icons/leap/umbrel.png" width="72">](https://github.com/QBitLeap/umbrel-app-store) |

Pre-alpha Quantum inscriptions, Galleries and a persistent Qbit indexer.
LEAP validates canonical envelopes, tracks permanent Quantum IDs through
ordinary UTXO spends and supplies CLI transaction preparation/recovery.
A Quantum is identified by its creation transaction and ownership output.
Rendering untrusted content is outside protocol validity and requires an
isolated viewer.

## Requirements

Desktop downloads include the CLI, indexer and Python runtime. Extract the archive
and run `./leap --help` in its directory, or `.\leap.exe --help` in Windows
PowerShell. Install [Qbit Core](https://github.com/Qbit-Org/qbit/releases) for
node-backed commands. Source installs require Python 3.11 or newer and PyYAML 6 or
newer. Node-backed commands
can use qbit-cli or HTTP RPC. Wallet operations require a separately managed,
loaded P2MR wallet; the read-only index follower does not need wallet access.
Umbrel packaging and its image/publication workflow live in [umbrel/](umbrel/README.md)
in this repository alongside native platform builds. Install through the
[community app store](https://github.com/QBitLeap/umbrel-app-store). Existing app IDs,
networks and data paths are unchanged. The previous leap-umbrel history is retained
in this repository and in its archived source repository after acceptance.

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
