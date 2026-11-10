# Gallery YAML examples

Start with the empty Gallery YAML and CLI commands in
[Guides/Quantums](../Guides/Quantums/README.md). These files show additional
member and update forms.

`gallery-create.yaml` is the required reproducible manifest for creating a
gallery root. Its `members` list may be empty for a gallery populated later by
successors. `destination` and `ownership_bits` are optional. Fee rate is always
provided at execution time with `--fee` and is never stored in YAML.

```bash
leap gallery create --wallet <name> --file gallery-examples/gallery-create.yaml --fee 0.33 --index-data-dir <index-data-dir> --dry-run
```

Updates may use `gallery-update.yaml` for an auditable change set:

```bash
leap gallery update --wallet <name> --file gallery-examples/gallery-update.yaml --fee 0.33 --index-data-dir <index-data-dir> --dry-run
```

Gallery transaction commands broadcast automatically when `--dry-run` is
omitted. Inspect and validate with `--dry-run` first when publication is not
intended.

For a quick update, omit `--file` and use `--gallery`, repeated `--add`, and
repeated `--remove` flags. Leap rejects mixing the two forms.
