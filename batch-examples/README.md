# Leap batch examples

Start with the executable YAML and CLI commands in
[Guides/Quantums](../Guides/Quantums/README.md). These additional manifests
show the three batch modes.

Leap has three practical batch modes. Every mode packs Quantums into the fewest
valid Qbit transactions and always creates a distinct P2MR ownership UTXO for
every Quantum.

The examples show the three modes:

1. `parentless.yaml` — unrelated Quantums, or a collection with no parent.
2. `parented.yaml` — a collection whose children share one or more verified
   parents. `collection.parent` accepts one Quantum ID or an ordered YAML list;
   every current parent output is spent and recreated to its existing owner.
3. `pick-utxo.yaml` — each Quantum uses the first bit of an exact wallet UTXO.

All paths are relative to the YAML file. The parentless and parented examples
use the confirmed Leap test content. The pick-utxo example deliberately uses
unspendable placeholder outpoints; replace them with wallet-controlled Qbit
UTXOs before use.

```bash
leap batch \
  --wallet <name> \
  --batch batch-examples/parentless.yaml \
  --chain testnet4 \
  --index-data-dir <index-data-dir> \
  --fee 0.25
```

Broadcast is the default and does not ask for confirmation. Add `--dry-run` to
build, sign, and validate without publishing.
The CLI writes a JSON report beside the YAML unless `--report` specifies a
different location. Use `--resume` with the same report to skip unchanged items
already marked as broadcast.

Supported root keys are `version`, `mode`, `collection`, `defaults`, and `inscriptions`.
Each inscription item requires `file` and an explicit, non-blank `name`. Defaults may set `ownership_bits`, `content_type`, `collection` and `destination`. Item-level `metadata` is a mapping; `utxo` is used only by pick-utxo mode.

Fee rate is intentionally not stored in YAML. The inscriber supplies an exact
decimal bits/vB rate with `--fee` for every run.
