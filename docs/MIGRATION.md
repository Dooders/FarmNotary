# Migrating FarmNotary releases

Schema id is `farmnotary.manifest.v1`. New fields are optional and omitted
when empty so an older body keeps its content hash. `verify` ignores unknown
keys. As of 1.0 the schema is frozen: new required fields, renames, and
removals wait for 2.0.

JSON Schema files live in [`schemas/`](../schemas/) and ship in the wheel
under `farm_notary/schemas/`.

## 0.2.0 → 1.0.0

1.0 adds no manifest fields and no claim types. A 0.2 manifest verifies
unchanged under 1.0, and its `content_hash` is unaffected. Two behaviour
changes can alter what a *new* run publishes or reports, and both are
visible rather than silent.

### What 1.0 freezes

The stability promise covers `farmnotary.manifest.v1`, the public
`farm_notary.__all__`, and the commands listed as stable by
`farm-notary --help`:

```
stable        anchor, campaign, check, derive-seeds, index, manifest,
              paper-pack, precommit, reproduce, reveal-withheld, sign,
              upgrade, verify
experimental  archive, chain, emit-interop, register-schema
```

Experimental commands are first cuts. They may change or be removed in a
minor release and none of them is a claim-ladder step. `anchor --backend
eas` is experimental for the same reason: it needs a funded key, costs gas,
and moves trust to an attester address. Use `--backend ots`.

### Breaking: `*` no longer crosses `/` in publish patterns

Publish patterns are now matched component-wise, which is what the docs
always described. Under 0.2 they went through `fnmatch`, where `*` crossed
directory separators.

| Pattern | 0.2 | 1.0 |
|---|---|---|
| `*.csv` | any depth | any depth (unchanged) |
| `results/*` | `results/a.csv` **and** `results/sub/a.csv` | `results/a.csv` only |
| `results/**` | same as `results/*` | `results/` at any depth |

Named profiles (`consensus`, `rl-sweep`, `evolution-run`) are unaffected:
their patterns are bare filenames plus `*.png`, and a pattern with no `/`
still matches at any depth.

A hand-written `dir/*.ext` pattern is affected. Measured with AgentFarm's
`OFFICIAL_PUBLISH_PATTERNS`, which includes `figures/*.png`, against the
same run directory:

| Release | `figures/chart.png` | `figures/nested/deep.png` | `unmatched_count` |
|---|---|---|---|
| 0.2.0 | published | **published** | 2 |
| 1.0.0 | published | withheld | 3 |

The change only ever withholds more, so it cannot leak a file that used to
be private — but the published set and therefore the `content_hash` differ
between releases for the same directory. **Check `unmatched_count` after
upgrading**, and add `**` where you meant to cross directories:

```bash
farm-notary manifest --run-dir path/to/run --publish 'figures/**/*.png'
```

### Behaviour: some things that used to pass now fail

These were gaps between what the card printed and what had been checked. A
run that verified under 0.2 can legitimately fail under 1.0:

| Change | Effect |
|---|---|
| Paths escaping the run directory are rejected | A manifest naming `../secret` fails instead of reading it |
| Dirty trees are re-detected | `git_dirty=False` from a caller no longer bypasses the check; pass `--allow-dirty` to make the exception explicit |
| `identity` is verified in `evaluate_claims` | A bad signature now fails the card instead of being ignored |
| `verify --campaign` counts children | No children checked reports incomplete instead of `OK` |
| `seed_plan` is compared field by field | A `min_round` or beacon-chain change against the anchored precommit now fails |
| `reproduce` CI auto-trust requires repository **and** SHA | A partial `ci_provenance` match needs `--i-accept-untrusted-command` |

`infer_claim_level` can also now return `derived` and `bitwise+derived`,
which were unreachable in 0.2. Pass `derived_ok=True` only when the
derivation rules actually ran and passed.

### Pins

```toml
farm-notary>=1.0,<2.0
```

```yaml
uses: dooders/FarmNotary@v1.0.0
```

AgentFarm's `notary` extra should move to `farm-notary>=1.0,<2.0` from
PyPI. It currently pins `farm-notary @ git+https://…@v0.1.0`, which builds
from a git tag rather than a released wheel and predates every security fix
in 0.2 and 1.0.

### Reader ladder in a paper

`paper-pack` prints `Reader ladder | —` and does not cite `Ln`. Cite the
claim card from `farm-notary verify` instead. L0 means the proof commits to
the content hash and carries a Bitcoin-height attestation; it does not mean
FarmNotary verified Bitcoin headers. That check stays external:

```bash
ots verify manifest.ots
```

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

## 0.2 withheld commitment

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
`publish_profile`. From 1.0 the public API is frozen: pin
`farm-notary>=1.0,<2.0`.
