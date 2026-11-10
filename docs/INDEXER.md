# LEAP index storage and readiness

The public index follows Qbit blocks and atomically maintains Quantums,
Galleries and recognized Arbitrary records. It supports main, testnet4 and
regtest. Transaction commands require a synchronized, network-bound index with
matching node/index height and hash before construction. Initial download,
unhealthy modules and missing or inconsistent state prevent transaction work.

```sh
leap index run --chain testnet4 --data-dir /path/to/leap/index/testnet4
leap index status --chain testnet4 --data-dir /path/to/leap/index/testnet4
```

The existing schema-4 layout is retained. The coordinator is leap-index.sqlite3;
active databases are quantum.sqlite3, gallery.sqlite3 and arbitrary.sqlite3.
Two reserved legacy files (pebble.sqlite3 and pson.sqlite3), inactive module
labels and the pebble_profile=off column/status fields remain solely for
storage/client compatibility. No corresponding processors or activation
commands are shipped. Active legacy profiles and experimental networks are
rejected. Keep existing indexes backed up; this source split is not acceptance
of a live data migration.

Disconnect and replay use incremental row-level undo, not full-state snapshots.
Immutable content/identity metadata stays separate from current locations.
Empty blocks advance the common committed tip and remain disconnectable.

## SQLite transaction configuration

Leap's coordinator attaches its five module files to the file-backed
`leap-index.sqlite3` connection. It requires every file to be a regular,
non-symlink file in one resolved directory on one filesystem and configures:

- rollback-journal `DELETE` mode for the coordinator and every attachment;
- `synchronous=FULL`;
- `fullfsync=ON` where SQLite and the operating system support it;
- a 30-second busy timeout; and
- foreign-key enforcement on the connection.

WAL mode is prohibited. Under these conditions SQLite uses its rollback and
super-journal protocol for atomic commits involving attached databases. Tests
cover a final-module exception after prior writes, abrupt process termination
with an open cross-database transaction, restart recovery, mismatched startup
tips, missing database creation, disconnection, and replacement replay.

This guarantee still relies on a local filesystem correctly implementing file
locking, durable sync, and atomic file operations, and on storage hardware not
falsely reporting completed writes. Leap must not claim equivalent crash
atomicity on network, distributed, or otherwise incompatible filesystems merely
because SQLite opened the files. Corruption, media loss, or broken durability
semantics remain outside the transaction guarantee. On those storage systems a
single-database schema or a separately designed recovery protocol is required.


See [relationships](RELATIONSHIPS.md) for the read-only parent/family query.
