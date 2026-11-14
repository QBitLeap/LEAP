# LEAP Quantum and Gallery protocol — pre-alpha

This specification covers the public Quantum envelope, ownership tracking and
Gallery records. Qbit consensus preserves bytes and enforces UTXO spending;
LEAP admission and indexing rules are interpreted by independent indexers.

## Carrier and ownership

A creation transaction contains:

1. a spendable P2MR output at `vout 0`, carrying the configured ownership
   value (4,000 bits by default, above the current 2,855-bit default dust
   threshold);
2. a zero-value `OP_RETURN` at `vout 1`, containing exactly one data push with
   the envelope; and
3. optional change outputs after `vout 1`.

The canonical inscription identifier is `<creation transaction ID>:0`.

## Envelope

The envelope begins with an eight-byte binary marker and a separate one-byte
format version, followed by a canonical UTF-8 header and an opaque body:

```text
QUANTUMS | 0x01
content-type:<media type>\n
content-length:<decimal byte length>\n
content-sha256:<lowercase SHA-256>\n
name:<UTF-8 display name>\n
ownership-output:0\n
[collection:<UTF-8 collection name>\n]
\n
<content bytes>
```

Header names are lowercase ASCII. Required fields appear in the exact order
shown. The optional `collection` field, when present, appears last. Duplicate,
unknown, reordered, empty, or malformed fields are invalid.

The blank line is the only header/body delimiter. `content-length` and
`content-sha256` must match the exact body bytes.

## Content rules

- The body is opaque to protocol validation.
- Indexing is media-neutral and must not decode content to establish protocol
  validity.
- Rendering is never consensus-critical.
- Viewers must treat `content-type` as untrusted input.
- Viewers may render browser-supported Ordinals-style media types, including
  JPEG, PNG, GIF, WebP, AVIF, audio, video, text, JSON, and PDF.
- Executable or active formats, including HTML and SVG, must be isolated with
  appropriate sandboxing and restrictive content-security policy.
- Unsupported or unknown formats remain valid opaque content and must be
  available as hash-verified raw downloads.
- Filenames must never be used as filesystem paths without sanitization.

The reference creator rejects new ZIP files and ZIP-based containers by filename,
declared MIME type and recognizable ZIP structure, including renamed archives.
This is local creation policy, not a Qbit consensus or canonical envelope rule.
Decoders, recovery and indexers continue to read existing opaque content without
extracting archives. Other applications may impose narrower upload allowlists.

## Limits

The reference implementation defaults to a 198,000-byte maximum envelope. This
is the complete encoded envelope limit, including headers and metadata, in decimal
bytes. The single OP_RETURN/PUSHDATA4 script is 198,006 bytes at that limit,
leaving 1,994 bytes below Qbit's default 200,000-byte data-carrier policy. Node operators can configure different
relay limits, so `testmempoolaccept` is authoritative for a particular node.

Transaction size and fee calculations MUST use the finalized Qbit transaction.
Wallet signature data can make otherwise similar transactions materially
different in virtual size, so an envelope-size-only fee estimate is not
authoritative.

Implementations using the older 190,000-byte ceiling must upgrade to recognize
envelopes in the newly allowed range. Existing smaller envelopes retain the same
canonical bytes and format versions; no chain/index reset is part of this update.

## Quantum transfers

Every Quantum permanently identifies one tracked bit. Its stable Quantum ID is
established by its confirming creation transaction and never changes. The
indexer stores its current location as `txid:vout:offset`; this is a sparse
Quantum-location index, not a global numbering or index of all Qbit bits.

When a transaction spends a Quantum's current outpoint, its inputs are
concatenated in transaction order and outputs consume that value in output
order. The tracked bit moves to the output and offset consuming its input-stream
position. This applies even when a non-Leap wallet merges, splits, or
accidentally spends a Quantum-bearing output. No successor envelope or repeated
Quantum ID is required, and such a spend does not burn or invalidate the
Quantum.

If the position lies after all transaction outputs, it is part of that
transaction's fee. Fee bits are concatenated by transaction order within the
block, after the block subsidy in the coinbase value stream, and are assigned
through coinbase outputs in output order. A Quantum paid as a fee therefore
moves to its resulting coinbase `txid:vout:offset`, is controlled by the miner,
and remains trackable under its original ID. Coinbase maturity affects when its
new output may be spent, not its identity or indexed ownership.

Wallet protection, not Qbit Core consensus, prevents unintended asset spends.
Leap wallets MUST exclude known known protocol-bearing outputs from ordinary
coin selection. The indexer MUST nevertheless follow a Quantum through every
consensus-valid spend regardless of which wallet created it. Leap-created
transfers SHOULD place each Quantum alone at offset zero of its destination
output; this wallet convention does not limit the indexer's general tracking
rule.

## Locked batch and collection rules

Leap batch YAML has three modes: `parentless`, `parented`, and `pick-utxo`.
The YAML schema version is optional and is not the Quantum envelope version.
Implementations pack each batch into the fewest standard Qbit transactions
permitted by transaction-weight and data-carrier policy. Every Quantum always
has its own spendable P2MR ownership UTXO; two Quantums never share one
ownership UTXO.

Batch splitting is canonical and ordered. Leap processes entries in their YAML
order and greedily appends each complete encoded Quantum to the current
transaction while the resulting version-2 batch envelope is no more than
198,000 bytes. The first Quantum that would exceed the limit begins the next
transaction, and the process repeats. Implementations MUST NOT reorder entries
to obtain a different packing. A single Quantum envelope is indivisible; if it
cannot fit by itself, preflight fails before broadcast.

The measured size is the exact output of the canonical version-2 batch encoder:
the batch preamble and count, every four-byte item length, every canonical item
header, encoded CBOR metadata, and content bytes. YAML syntax, indentation,
comments, paths, defaults, and other authoring-only bytes are not carried and do
not count. Metadata values selected from YAML do count after canonical CBOR
encoding, just as inscription content counts. OP_RETURN script framing and the
rest of the transaction remain outside the 198,000-byte envelope target and fit
within Qbit's current 200,000-byte carrier policy. The final transaction must
also meet transaction-size, fee and local relay policy; this envelope ceiling
does not override those checks.

All chunks preserve the original mode, collection commitment, per-item
metadata, content, destination, and—where applicable—parent or source-UTXO
commitment. Ownership-output numbers are calculated for each final chunk before
encoding. Funding preflight plans every chunk and its dependencies before the
first broadcast.

Version-2 creation transactions contain consecutive ownership outputs and one
zero-value OP_RETURN containing a canonical, length-prefixed collection of the
corresponding Quantum envelopes. Each envelope explicitly commits to its
ownership output number. Its Quantum identifier is
`<confirming-transaction-id>:<ownership-output>`. Change and non-Quantum outputs
must not be mistaken for Quantum ownership outputs.

`parentless` creates unrelated Quantums when the optional `collection` mapping
is absent and a parentless named collection when it is present. `parented`
requires one or more parent Quantums, proves control by spending their current
ownership outpoints in declared order, recreates every parent output in that
same order, and creates each child in its own new ownership output. Version-2
items encode the singular header name `parent` once per ordered parent ID.
Duplicate parent IDs are invalid. The precise general transfer/successor rules build on
this proof and remain subject to further adversarial testing; implementations
must not accept a metadata-only parent claim as verified provenance.

`pick-utxo` requires an exact `txid:vout` for each entry. It inscribes the
Quantum on offset zero, the first bit of that selected UTXO. The selected inputs
are ordered and their value is routed so each first bit becomes offset zero in
its corresponding new Quantum ownership output. Unused selected-input value is
returned separately. Fees must never consume a selected first bit. When the
final selected UTXO has sufficient remainder, fees are deducted from the tail
of that final remainder without shifting any selected first bit; otherwise a
separate fee input is required. This requires only deterministic transaction-local bit flow and a
Quantum location index, not global bit serial numbers.

Every entry may specify `destination`. If omitted, the wallet generates a fresh
P2MR address for that Quantum. A supplied destination must be a valid P2MR
address for the active Qbit network. Each missing destination receives a
different fresh address.

Wallets MUST exclude known Quantum ownership UTXOs from ordinary automatic coin
selection. The reference Qbit wallet integration persistently locks every new
Quantum ownership outpoint immediately after broadcast. A parent, transfer, or
other Quantum-aware operation may select such an outpoint explicitly, but a
normal funding operation must never consume it as incidental QBT.

Fee rate is a wallet execution parameter, never permanent batch metadata. Leap
batch and inscription commands require an explicit `--fee` rate in bits/vB and
accept exact positive decimal values without binary floating-point conversion.

## Version-2 metadata

Quantum envelope version 2 evolves the version-1 content, hash, name,
collection, and ownership methodology. It adds optional parent and source-UTXO
commitments and embeds per-Quantum metadata before the content bytes. Metadata
written naturally as YAML is encoded on chain as deterministic CBOR and is
committed by byte length and SHA-256.

The initial metadata subset permits null, booleans, signed 64-bit integers,
UTF-8 strings, finite arrays, and maps with unique UTF-8 string keys. Map keys
use deterministic CBOR order. Floats, byte strings, tags, indefinite lengths,
duplicate keys, and non-minimal encodings are invalid. Metadata is part of the
Quantum and must remain recoverable with its content.

Version-1 Quantums remain permanently valid, recoverable, renderable,
indexable, referenceable, and transferable. New features never reinterpret or
invalidate their original bytes.

## Quantum galleries

A gallery is a permissionless ordered collection manifest carried as the
content of a Quantum with media type `application/x-leap-gallery`. A gallery's
human-readable name is not unique and proves no authorship. Its permanent
identity is the Quantum ID of its root gallery record. Anyone may create a
gallery containing any syntactically valid Quantum IDs, so inclusion alone does
not establish provenance and MUST NOT create an authenticated backlink from a
member to the gallery.

Gallery records use a compact canonical binary grammar independent of envelope
and YAML versions:

```text
"QGAL" | version:u8=1 | kind:u8 | payload

kind 1 ROOT:
  name:text(255) | members:packed-id-list

kind 2 UPDATE:
  root:quantum-id | previous:quantum-id |
  add:packed-id-list | remove:packed-id-list
```

A Quantum ID is encoded as its 32-byte transaction ID followed by a canonical
uint64 uvarint ownership-vout. A packed ID list begins with its canonical count.
Its first item, and every item whose transaction ID differs from the preceding
item, uses tag `1`, the 32-byte transaction ID, and the vout. An immediately
following item with the same transaction ID uses tag `0` and only its vout.
This preserves member order while storing transaction IDs shared by a Leap
batch only once. Lists reject invalid tags, non-minimal integers, duplicates,
truncation, trailing bytes, and more than 100,000 IDs.

The root member order is its display order. An update's `add` list is ordered
and appends new members in that order. Its `remove` list is canonically sorted
by binary Quantum ID because removal order has no state meaning. Applying an
update removes the declared existing members while preserving every remaining
member's relative order, then appends the additions. An update is invalid if it
is empty, repeats an ID, adds an existing member, removes a nonmember, or places
the same ID in both lists. Removal changes current membership but never erases
the immutable root or successor history.

Gallery updates form a single authorized successor chain. The root Quantum ID
remains the permanent gallery identity. `previous` identifies the current
gallery Quantum whose ownership output MUST be transaction input zero. The
transaction MUST recreate that exact P2MR script and value at output zero; fee
inputs follow it. The update record MUST retain the root ID and reference the
immediately preceding successor. Its own confirming `<txid>:0` becomes the new
current gallery Quantum. A stale, parallel, or metadata-only update is invalid.
RBF may change funding and change but must preserve the gallery record, control
input, and successor output.

Gallery functionality does not add another batch mode. A client may create a
root gallery after a `parentless` batch confirms, using the resulting Quantum
IDs. `leap gallery update` requires only the current gallery Quantum ID
and one or more bare `--add ID` or `--remove ID` operations; the client replays
predecessors to derive and validate current membership.

The reference CLI requires gallery roots to be declared in a strict YAML file
containing `name`, an ordered `members` list, and optional `destination` and
`ownership_bits`. The YAML schema version is optional. Gallery updates may use
either a strict YAML file containing `gallery` plus `add` and/or `remove`, or
equivalent inline flags, but never both. YAML bytes and `--fee` are execution
inputs and are not part of the canonical `QGAL` record.

