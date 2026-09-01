# Migrating FarmNotary releases

Schema id stays `farmnotary.manifest.v1` through 0.x. New fields are
optional and omitted when empty so an older body keeps its content hash.
`verify` ignores unknown keys. Breaking changes (new required fields,
renames, removals) are reserved for 1.0.

JSON Schema files live in [`schemas/`](../schemas/).

## 1.0 freeze

As of the 1.0 tag, `farmnotary.manifest.v1`, the public `farm_notary.__all__`
symbols, and the documented CLI subcommands are the frozen 1.0 surface. No
pre-tag cleanup was needed: optional content-hashed fields (`derived_from`,
`publish_profile`, `precommit_hash`, `beacon`, `ci_provenance`,
`withheld_salt`, `withheld_root`, `withheld_classes`) are already omitted
from `to_dict()` when empty/`None`, so older manifest bodies keep a stable
`content_hash`. The schema id is not renamed by this change.

After 1.0, new required manifest fields, renames, and removals to
`farmnotary.manifest.v1`, `farm_notary.__all__`, or the CLI wait for 2.0.
New optional, content-hashed fields may still ship in 1.x as long as they
are omitted when empty.

## 0.1.0 → 0.2.0

These already shipped on the 0.2 line. They are breaking relative to 0.1.0.

### Allowlist is required

`farm-notary manifest` no longer hashes the whole run directory minus a
denylist. Pass `--profile <name>`, `--publish <glob>`, or set
`notary.profile` / `notary.publish` in the run config. Prefer a named
profile (`consensus`, `rl-sweep`, `evolution-run`).

`publish_patterns` and `unmatched_count` are required on every v1 body.

### Dirty trees cannot be precommitted or anchored

`precommit`, `anchor`, `build_precommit()`, `anchor_run()`, and
`notarize_run()` fail on a dirty git tree unless `--allow-dirty` /
`allow_dirty=True`. A supplied `--git-sha` is not a bypass.

### `verify` prints a claim card

Exit 0 means attempted checks passed, not that every claim was earned.
Missing is not failure. Read the card, not the exit code.

### Action and pin defaults

CLI `anchor` / `precommit` default to `dry-run`. The GitHub Action
defaults to `ots`. `--pin-remote` is the published pin path.

## 0.2 withheld commitment (this change)

Optional, content-hashed, omitted when nothing was withheld:

| Field | Meaning |
|---|---|
| `withheld_salt` | 32-byte hex per-run salt |
| `withheld_root` | SHA-256 Merkle root over salted path+bytes |
| `withheld_classes` | `{denylist\|unmatched: {count, reason}}` |

`unmatched_count` is unchanged: it is the number of candidate files left
out of the official record (denylist + unmatched). Class counts sum to
that number when the commitment is present.

Older manifests without these fields still load and verify. Names of
withheld files are never printed. To open a subset later:

```bash
farm-notary reveal-withheld --run-dir path/to/run --path extra.json --out reveal.json
farm-notary reveal-withheld --run-dir path/to/run --verify --reveal reveal.json
```

Unsalted hashes of ballots are not stored. The leaf is
`SHA-256(salt || 0x00 || path_utf8 || 0x00 || content)`. Parents are
`SHA-256(0x01 || left || right)`; an odd leftover leaf is promoted.

## Stamp vs content-hashed fields

`identity` (minisign / SSH) is a **stamp field**, like `cid` and
`anchor`. It is written after `content_hash` and excluded from it.
`ci_provenance` is content-hashed when present.

## Python API

`notarize_run()` / `build_manifest()` take `publish_patterns` and
`publish_profile`. AgentFarm should pin `farm-notary>=0.2,<0.3` until
the next minor that adds required fields.
