# Canonical Quantum relationships

LEAP owns read-only discovery of verified parent/child links and connected
families. The same implementation supports public Mainnet (`main`) and
Testnet4 (`testnet4`). Clients decide how to present these relationships;
prices, sale ordering, captions and card layouts are outside this query.

## CLI

Mainnet is the normal CLI default:

```sh
leap index relationships --data-dir <mainnet-index-directory>
```

For Testnet4:

```sh
leap index relationships --chain testnet4 --data-dir <testnet4-index-directory>
```

Use the selected node's usual CLI/HTTP RPC overrides if required. No wallet,
fee, signature or publication option is needed. The query uses only chain,
block and transaction reads and never initializes or writes index databases.
The existing schema-4 index must be bound to the selected network with the legacy profile
disabled. A stopped node or unreadable/inconsistent index is an error, not proof
that relationships are absent.

## Python contract

```python
from indexer.relationships import QuantumRelationshipQuery

query = QuantumRelationshipQuery(index_directory, "main", rpc)
report = query.discover()
```

`rpc` supplies `call(method, *params)`. The report contains:

- `network`, `indexHeight`, `indexHash`, `nodeHeight`, `synchronized`;
- `quantums`, in block/transaction/data-output/envelope order, with stable
  `id`, 1-based order `number`, canonical name/collection/type/content hash,
  creation height/hash, `declaredParents`, verified stable `parents`, and
  `parentStatus` (`none`, `verified` or `unresolved`);
- `families`, with the earliest member's stable `id`, `firstNumber`, ordered
  `members` and `roots`. Singletons are retained and each Quantum belongs to
  exactly one connected family.

`number` is query ordering, not a replacement for the permanent Quantum ID.
`unresolved` retains a declaration without treating it as verified provenance.
Custom metadata and matching collection labels never establish links. Full
content and custom metadata are not included in this JSON result.

A valid committed prefix may be queried while the follower catches up, after
checking its genesis and tip hash against Core. `synchronized=false` makes
that scope explicit; it never promises discovery of records beyond the index.
Wrong networks, profiles, schemas or diverging chain tips fail closed. Queries
recheck the index and chain after recovery and reject changes affecting the
result. Reusing a query object caches only bounded immutable creation data,
keyed by block hash; canonical membership and ownership history are reread.

## Discovery and successor resolution

1. Open the coordinator and Quantum databases read-only, checking schema,
   network/profile and matching committed tips. Check Core's network, genesis
   and hash at that committed height.
2. Recover each indexed creation from its indexed block. Use the existing
   canonical discovery rules and the exact data-output/envelope/ownership
   positions, then match name, type and content SHA-256 against the index.
3. Read the canonical envelope's ordered parent declarations. A reference may
   be a creation ID or a previous ownership successor. Resolve required
   outpoints using `quantum_locations` and the existing `undo_mutations`
   previous/expected location rows, retaining stable Quantum identities.
4. Require parents to precede the child. Verify all declared parents together:
   the child's required leading inputs must spend their tracked ownership
   outputs, and corresponding leading outputs must preserve each complete
   value and controlling script. An ambiguous successor reference is not
   resolved by arbitrarily selecting an identity. Missing or invalid control
   evidence does not create a partial parent set.
5. This supports later batch chunks whose header names an older successor
   while their input spends a newer successor of the same permanent parent.
   Merge verified links into connected families, including descendants and
   multiple-parent families, without duplicating members.

This is query behavior over existing public Qbit history. It introduces no
Qbit consensus rule, no new inscription encoding, no automatic name changes,
and no index-schema migration. Existing records remain valid and visible even
when a parent declaration cannot be verified. Generic transfers, Galleries
and Pebble rules are unchanged. Do not replace the follower's existing writer
or use this read API to claim an unrelated signing/publication check passed.

`tests/test_relationships.py` builds real LEAP materialized toy chains for both
networks and checks old successors, later chunks, descendants, ordered multiple
parents, forged/ambiguous declarations, altered return values/scripts,
canonical carriers, network/profile/tip admission, reorgs, caching and no
database writes. Actual network/runtime checks remain separate evidence.
