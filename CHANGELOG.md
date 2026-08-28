# Changelog

All notable changes to FarmNotary will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
FarmNotary uses [Semantic Versioning](https://semver.org/).

---

## [Unreleased] — breaking changes

### Breaking

#### Privacy filter replaced with an explicit allowlist (`security`, `breaking-change`)

Previously, `farm-notary manifest` admitted every file in the run directory
whose path did not contain a private-name substring (`ballot`, `vote`, etc.).
A file named `agent_selections.csv` or `choices_raw.parquet` would be hashed
and pinned silently.

**The default is now to include nothing.**  A file is hashed, listed in the
manifest, or uploaded to IPFS only when it matches at least one declared
*publish pattern*:

```bash
# CLI
farm-notary manifest --run-dir path/to/run --config config.json \
  --publish 'summary.csv' --publish 'allocation_means.csv' --publish '*.png'
```

```json
// Run config (versioned with the experiment)
{
  "notary": {
    "publish": ["summary.csv", "allocation_means.csv", "*.png", "REPORT.md"]
  }
}
```

Patterns follow `fnmatch` semantics.  A plain `*.ext` pattern matches files in
any subdirectory (not just the run-dir root).

The substring denylist (`ballot`, `vote`, `voter`, `individual_choice`,
`private`) is retained as a belt-and-braces second pass over whatever the
allowlist admits.

**Migration:** every existing `farm-notary manifest` invocation must add at
least one `--publish <glob>` flag, or the `notary.publish` key to the run
config, or `build_manifest()` will raise `ValueError`.

#### New manifest fields

- `publish_patterns` — the allowlist globs that were active.
- `unmatched_count` — number of run-dir files that matched no pattern (visible
  omission, not a silent one).

Both fields are required by `Manifest.validate()` from this release onward.

#### New CLI output

`farm-notary manifest` now also prints `unmatched <N>` so operators can see
how many files were left out without revealing their names.

#### `notarize_run()` API

`notarize_run()` now accepts a `publish_patterns` keyword argument, forwarded
to `build_manifest()`.

### Added

#### Campaign / sweep manifests
- `farm-notary campaign` writes `campaign.json` (`farmnotary.campaign.v1`):
  child run CIDs, seeds, and a seed-excluded config hash so a reviewer can
  check a paper figure (100 trials, seed 0…N) instead of one folder.
- `farm-notary verify --campaign` checks shared config hash and, when child
  directories are present, each child's content hash.

#### Derivation claims
- Optional `notary.derived_from` rules in the experiment profile are copied
  onto the manifest. `verify` recomputes named outputs from sources (or runs
  a verify-style command) so "statistics recompute exactly" is a first-class
  claim even when a PNG is renderer-dependent.

#### Environment fingerprint
- `environment` now records `os`, `arch`, `python`, `python_implementation`,
  and — when numpy is installed — the BLAS/LAPACK build. Lockfile hash is
  unchanged. This keeps “bitwise on x86-64 Linux, pinned env” scoped when a
  reviewer reproduces on Apple Silicon and sees a 1-ulp diff.

#### Optional identity
- `farm-notary sign --scheme ssh|minisign` records a signature of the
  content hash. Stamp field: excluded from `content_hash`. No protocol token;
  EAS stays experimental.

#### Paper pack and public index
- `farm-notary paper-pack` writes an appendix snippet (CID, content hash,
  Bitcoin attestation or pending, publish allowlist, unmatched count,
  precommit hash, scoped reproducibility sentence).
- `farm-notary index` maintains a static registry (Markdown + JSON) of
  published manifests: experiment, seed, CID, claim level, date. Scores and
  rankings are rejected.

#### Reusable GitHub Action
- Root `action.yml` (`dooders/farm-notary-action`): precommit on workflow
  start, notarize + optional pin-remote on success, upload `manifest.json` +
  `manifest.ots`, fail the job if verify fails. See `docs/ACTION.md`.

---



First release.

### Added

#### Core
- `manifest.json` schema v1: recursive artifact discovery, SHA-256 map, UTC
  timestamp, git SHA + dirty flag, config object, optional `official_record`,
  `cid`, and `anchor` fields (`farm_notary/schema.py`, `farm_notary/manifest.py`).
- `content_hash` excludes `cid` and `anchor` so the manifest can be stamped
  after upload without invalidating the anchored hash.
- Privacy filter: relative paths containing `ballot`, `vote`, `voter`,
  `individual_choice`, or `private` are never hashed, listed, or uploaded.
- `Manifest.validate()` enforces schema id, artifact list / hash-map agreement,
  and the privacy filter.
- `capture_environment()` records Python version, platform, and a hash of the
  installed package set; accepts an optional lockfile hash.

#### CLI (`farm-notary`)
- `manifest` — build and write `manifest.json` for a run directory.
- `verify` — rehash artifacts, check OTS proof, check reproduction receipt.
- `anchor` — dry-run (default) or live anchor via a configured backend; `--pin`
  uploads the run directory to IPFS first.
- `upgrade` — complete a pending OpenTimestamps proof with a Bitcoin attestation.
- `reproduce` — re-run the recorded command into a fresh directory and
  byte-compare every listed artifact; writes `reproduction.json`.
- `register-schema` — one-time EAS schema registration on Base / Base Sepolia.

#### Backends
- **Dry-run** (default): returns the payload that would be submitted without
  contacting any external service.
- **OpenTimestamps** (`farm-notary[ots]`): submits the manifest content hash to
  public calendar servers; calendars batch digests into Bitcoin. Proof written
  to `manifest.ots`. Upgrade and verify supported.
- **EAS** (`farm-notary[chain]`): attests `(manifestHash, cid)` on
  [EAS](https://attest.org), which is an OP-stack predeploy on Base and Base
  Sepolia (`0x4200…0021`). Schema UID is derived deterministically and is the
  same on every chain. Receipt (tx hash, attestation UID, chain id) is written
  back into `manifest.json`.

#### IPFS
- `IpfsClient`: multipart upload to a Kubo daemon's `/api/v0/add` with
  `wrap-with-directory=true&cid-version=1&pin=true`; stdlib only, no extra
  needed. Endpoint from `FARM_NOTARY_IPFS_API` (default
  `http://127.0.0.1:5001`).

#### Reproduction
- `reproduce_run`: re-executes the recorded command (with `{run_dir}`
  placeholder) into a fresh temp directory and returns per-file comparison
  results.
- The `reproduce` CLI writes `reproduction.json` with the rerunner environment,
  per-file results, and original manifest hash.
- `--anchor` timestamps the receipt itself via OpenTimestamps, making
  "independently reproduced" a timestamped, third-party-checkable statement.
- `verify_receipt`: checks the receipt against the manifest content hash and
  validates the optional `reproduction.ots` proof.

#### Public Python API
- `notarize_run(run_dir, ...)` — build manifest, optionally pin and anchor,
  return `(Manifest, AnchorReceipt)`. Dry-run by default.
- All major types and helpers exported from `farm_notary` directly.

### Dependencies
- Zero runtime dependencies for the core (manifest, verify, IPFS, dry-run
  anchor); stdlib only.
- `farm-notary[ots]` adds `opentimestamps>=0.4.5`.
- `farm-notary[chain]` adds `web3>=7,<8`.
- Requires Python ≥ 3.9.

### Empirical validation
- The AgentFarm political consensus experiment (PR #985, 100 trials, 300
  voters, 8 candidates, seed 0) was notarized and reproduced **8/8 artifacts
  bitwise** in a pinned environment with no exclusions. See
  [docs/CLAIMS.md](docs/CLAIMS.md).

[0.1.0]: https://github.com/Dooders/FarmNotary/releases/tag/v0.1.0
