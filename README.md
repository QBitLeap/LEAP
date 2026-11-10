# LEAP for Umbrel

Independent LEAP app source for umbrelOS 2.0 or newer. This is an amd64
prerelease. Published images have passed isolated Mainnet/Testnet4 integration;
managed Umbrel installation and real-chain operation acceptance remain pending.

This indexer depends on a selected Qbit Core app instance and owns its own index. Its app-local transport preserves loopback RPC while authenticating the separate Core connection.

The browser UI shows status. Each app instance has explicit Mainnet or
Testnet4 identity and separate persistent data. Existing unmarked data and
wrong-network directories are refused. Wallets, signing and broadcasting are
outside the read API. App credentials are derived per installation and are
never included in source or container images.

## Installation

Install a published release through the QBitLeap community app store:
https://github.com/QBitLeap/umbrel-app-store

The store is not ready until its image lock contains published registry
manifest digests and the complete Mainnet/Testnet4 integration checks pass.
Local image configuration IDs are not downloadable registry references.
Indexing starts automatically when the matching Core instance is available.

The dashboard accepts a refresh interval in whole minutes, with a one-minute
default and minimum. The choice is saved in this browser, hidden pages stop
refreshing, and requests never overlap. Paused indexing is shown explicitly;
enabled indexing reports its current block and the selected node's block height.

## Inscribe from the Umbrel terminal

Wait for Core and the LEAP indexer to reach the same chain tip. In the Umbrel host
terminal, run the launcher installed by Qbit Core with the directory that holds
your inscription files.

```sh
sudo sh /home/umbrel/umbrel/app-data/qbitleap-core-main/qbit-terminal.sh leap main /path/to/inscription-files
```

Use `testnet4` for both network names with the Testnet4 apps. In the LEAP shell, create a wallet
once and generate a funding address.

```sh
leap wallet create leap --chain "$QBIT_CHAIN" --rpc-cookie /auth/.cookie
leap receive --wallet leap --chain "$QBIT_CHAIN" --rpc-cookie /auth/.cookie
```

Once the wallet has confirmed funds, check the inscription with a dry run.
Choose the fee rate in bits/vB. Remove `--dry-run` to broadcast through Core.

```sh
leap inscribe artwork.png --name "Quantum name" --fee 1 --wallet leap \
  --chain "$QBIT_CHAIN" --rpc-cookie /auth/.cookie --dry-run
```

The wallet is stored by Qbit Core. The operator shell reaches Core through
loopback and uses the existing Core cookie. The LEAP dashboard and indexer
keep their read-only access to Core. Inscribing this way pays the Qbit
network fee directly.

## Source checks

```sh
python3 -m unittest discover -s tests
```

## Image releases

On a Linux amd64 Docker host, from clean committed source:

```sh
python3 scripts/release_images.py 0.1.0-alpha.1
```

That command builds and smoke-checks images. Add `--publish` only after
logging in to GitHub Container Registry with package-write permission. The
manual Image release workflow performs that login with the repository's
GITHUB_TOKEN and emits a receipt containing the actual registry digest.
It has no automatic publication trigger.

Sources, container bases and language dependency locks are pinned. Debian
package resolution is performed at build time; bit-for-bit reproducibility is
not claimed. A rebuilt image must pass the complete store integration checks
before its digest is added to the catalog. Upstream changes require a reviewed
lock/patch update; apps do not adopt upstream changes while running.

## Instance packaging

`scripts/prepare_app_package.py --help` describes the package generator. Choose
an app ID, network and unused UI port; indexers also select a Core dependency.
Use published `ghcr.io/...@sha256:...` image references. Multiple independent
instances reuse an image but need distinct app IDs and writable data folders.
Do not share a writable database or chain directory.

The Core gateway shares its Core service's network namespace. Lifecycle
operations must restart that dependency together; all package services use an
init process so shutdown signals reach the application.

## Licenses

Wrapper code is MIT. See LICENSE, NOTICE and retained upstream notices.
Protocol source, artwork and dependencies keep their respective upstream
licenses. Source releases and image publication are separate from native
macOS/Windows/Linux protocol release artifacts.
