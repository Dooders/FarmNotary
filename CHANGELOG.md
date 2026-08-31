# Changelog

All notable changes to FarmNotary will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
FarmNotary uses [Semantic Versioning](https://semver.org/).

The current release is **0.2.0**.

---

## [Unreleased]

### Changed

- Docs rewritten against the 0.2 CLI: install path (PyPI is 0.1.0; current
  line is git `@dev`), `anchor` dry-run default vs Action `ots`,
  `--verify-derived`, publish profiles, claim levels, and Action pin
  (`dooders/FarmNotary@dev` — there is no `v0.2` tag yet).
- `farm-notary verify` no longer fails when `derived_from` rules are present
  but `--verify-derived` was not passed. Missing is not failure; the CLI
  notes that rules were not executed. `--verify-derived` still runs them
  and can fail the check.
- GitHub Action accepts a `profile` input (`consensus`, `rl-sweep`,
  `evolution-run`).
- Package description no longer says “on-chain attestation”.

### Added

- Packaging hygiene: `py.typed`, complete public `__all__`, PyPI classifiers
  and URLs, `[lint]` extra (`ruff`, `mypy`), and a CI lint job.
- Sigstore keyless signing for reproduction receipts (`farm-notary`
  reproduce --sign`). `verify` checks `receipt["sigstore"]` with
  `cosign verify-blob` (offline when the bundle has a Rekor inclusion
  proof). L3 means a verified signature with identity not constrained —
  not "independently reproduced." Tokens go through `SIGSTORE_ID_TOKEN`
  / `COSIGN_IDENTITY_TOKEN` or `--identity-token @PATH`. Documented
  cosign pin: `v2.5.3`. Optional Action input `sign-receipt`.
- Live demo notebook (`docs/demo/`): a tiny consensus-style experiment
  notarized with the dry-run backend so researchers can see the claim
  card, the allowlist, a scoped re-run, and a packaging mismatch labeled
  not a science failure. `tests/test_demo.py` execs the notebook cells.
- Beacon-derived seeds (issue #30): `precommit --seed-count` records a
  `seed_plan`; `derive-seeds` requires a `precommit.ots` that commits to
  the plan, then binds seeds to exactly `min_round`. The run manifest
  stores a `beacon` block so `verify` can recompute the seed. L2 also
  requires a bound plan, `created_utc` not after the round, and a
  fixture or `--live-beacon` HTTP compare (TLS to drand REST; signatures
  are not checked). Missing members of the committed set are listed.
  Tests use `FixedBeacon`; CI does not call live drand. L2 is not
  scientific correctness.
- `farm-notary verify` prints a stacked reader ladder (`none` / L0–L3)
  above the claim-card rows: highest earned level and the gap that
  blocks the next. L0 requires a Bitcoin-height attestation (pending
  OTS does not count) and does not mean Bitcoin headers were checked.
  L1 requires L0 plus a recorded `command`, `git_sha`, and environment
  fingerprint; it does not mean the command was run. L2 requires a
  beacon-derived seed after the plan is anchored. L3 is a
  Sigstore-signed receipt (identity not constrained). `paper-pack`
  prints an artifact label and leaves reader ladder as `—` (do not cite
  `Ln` from an appendix).
- `docs/PRINCIPLES.md`: constraint document for refusing features
  (existence is not correctness, reader-side checks over publisher
  decoration, cherry-picking out of scope, self-assertion as input,
  publish is one-way, omission is recorded policy, trust-assumption
  budget, outsource solved infrastructure).
- README: a "Why FarmNotary" section — official record vs laptop folder,
  hash tool vs research notary, and why the domain work is allowlists,
  privacy, and honest claims (anchoring is outsourced).
- Intro slide deck for researchers and labs (`docs/slides/`): four-act
  talk (Why / Record / Evidence / Start), claim card, scoped consensus
  showcase, solo/lab paths. Locked to CLAIMS.md by
  `test_intro_deck_stays_inside_the_claim_card`. A 16:9 PDF
  (`docs/slides/farmnotary.pdf`) is generated from the HTML deck via
  `docs/slides/export_pdf.py` (headless Chrome).
- Consensus walkthrough deck (`docs/slides/consensus.html` and
  `docs/slides/consensus.pdf`): a 15-slide worked example of the tiny
  demo experiment — official vs private files, dry-run claim card,
  tamper, scoped 7/7 re-run, and a packaging bug labeled not a science
  failure. Locked by `test_consensus_walkthrough_deck_stays_inside_the_claim_card`.
- Tests for claim levels (`infer_claim_level`, paper sentences), docs-to-CLI
  lock, paper-pack `--verify-derived`, campaign `--require-local` / artifact
  rehash, derivation rule validation, and a tighter Action contract.
---

## [0.2.0] — 2026-08-31

Published to PyPI as `farm-notary==0.2.0`. Action pin: `dooders/FarmNotary@v0.2.0`.

### Added

#### Determinism diagnostics on mismatch

When `reproduce` finds a byte-diff it classifies the cause
(`farm_notary/diagnose.py`) and writes `diagnostics` on the receipt:

- `embedded_absolute_path` — output path baked into `REPORT.md` (the
  `{run_dir}` fix that took 7/7 → 8/8)
- `timestamp` — clock / ISO text in the artifact
- `float_print_format` — same numbers, different spelling
- `video_encoder` — MP4/WebM encoder output

Classified diffs print **not a science failure** plus a fix. Unclassified
diffs print **byte-diff is not a science verdict**. `verify` repeats the
same notes under the claim card. A mismatch is still a failed bitwise
claim; it is not a science claim.

#### Scoped bitwise sentence (x86-64 Linux only)

`farm-notary verify` and `farm-notary reproduce` emit the CLAIMS.md sentence
the tool is allowed to print:

> byte-identical on x86-64 Linux in a pinned environment

That sentence is earned only by a passing receipt whose machine is in
`farm_notary.scope.DEMONSTRATED_SCOPES` (today: `x86-64 Linux`). A passing
receipt on Linux ARM or macOS ARM reports `N/M on <machine>; cross-hardware
bitwise identity is not a claim`. Failed receipts report `fail — N/M` and
do not emit the sentence. Manifests and receipts now record `system` and
`machine` so the card does not have to parse `platform.platform()`.

`.github/workflows/reproduce-consensus-matrix.yml` produces+reproduces on
one x86-64 Linux VM (the demonstrated cell: 10/10 on 2026-08-28) and then
re-runs on another x86 VM (also 10/10), Linux ARM (6/10), and macOS ARM
(2/10). ARM receipts are not `ok`; the claim stays that one sentence.
The recorded command is portable (`python run_experiment.py` plus `--cwd`);
AgentFarm is a reviewed SHA pin, not a job-output checkout.

#### Experiment-type publish profiles

Named profiles `consensus`, `rl-sweep`, and `evolution-run` are checked-in
allowlists of official artifacts (`farm_notary/profiles.py`). The consensus
profile includes `contrasts.csv` (official AgentFarm record). Prefer
`--profile consensus` (or `notary.profile` in the run config) over inventing
globs. The denylist still applies. Extra `--publish` / `notary.publish`
patterns append. The resolved allowlist is recorded as `publish_patterns`
(already in the schema); `publish_profile` is recorded when a profile was
used, so the policy is part of the claim.

#### Campaign / sweep manifests

- `farm-notary campaign` writes `campaign.json` (`farmnotary.campaign.v1`):
  child run CIDs, seeds, and a seed-excluded config hash so a reviewer can
  check a paper figure (100 trials, seed 0…N) instead of one folder.
- `farm-notary verify --campaign` checks shared config hash and, when child
  directories are present, each child's content hash. It does not print a
  single-run claim card.

#### Derivation claims

- Optional `notary.derived_from` rules in the experiment profile are copied
  onto the manifest. `verify --verify-derived` recomputes named outputs from
  sources (or runs a verify-style command) so "statistics recompute exactly"
  is a first-class claim even when a PNG is renderer-dependent. Default
  `verify` does not execute those commands.

#### Environment fingerprint

- `environment` now records `os`, `arch`, `python`, `python_implementation`,
  `system`, `machine`, and — when numpy is installed — the BLAS/LAPACK build.
  Lockfile hash is unchanged. This keeps “bitwise on x86-64 Linux, pinned env”
  scoped when a reviewer reproduces on Apple Silicon and sees a 1-ulp diff.

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

- Root `action.yml` (`farm-notary-action`): precommit on workflow start,
  notarize + optional pin-remote on success, upload `manifest.json` +
  `manifest.ots`, fail the job if verify fails. See `docs/ACTION.md`.

#### Reader ladder, beacon seeds, and Sigstore (L0–L3)

- `farm-notary verify` prints a stacked reader ladder (`none` / L0–L3)
  above the claim-card rows: highest earned level and the gap that
  blocks the next. L0 requires a Bitcoin-height attestation (pending
  OTS does not count) and does not mean Bitcoin headers were checked.
  L1 requires L0 plus a recorded `command`, `git_sha`, and environment
  fingerprint; it does not mean the command was run. L2 requires a
  beacon-derived seed after the plan is anchored. L3 is a
  Sigstore-signed receipt (identity not constrained). `paper-pack`
  prints an artifact label and leaves reader ladder as `—` (do not cite
  `Ln` from an appendix).
- Beacon-derived seeds (issue #30): `precommit --seed-count` records a
  `seed_plan`; `derive-seeds` requires a `precommit.ots` that commits to
  the plan, then binds seeds to exactly `min_round`. The run manifest
  stores a `beacon` block so `verify` can recompute the seed. L2 also
  requires a bound plan, `created_utc` not after the round, and a
  fixture or `--live-beacon` HTTP compare (TLS to drand REST; signatures
  are not checked). Missing members of the committed set are listed.
  Tests use `FixedBeacon`; CI does not call live drand. L2 is not
  scientific correctness.
- Sigstore keyless signing for reproduction receipts (`farm-notary
  reproduce --sign`). `verify` checks `receipt["sigstore"]` with
  `cosign verify-blob` (offline when the bundle has a Rekor inclusion
  proof). L3 means a verified signature with identity not constrained —
  not "independently reproduced." Tokens go through `SIGSTORE_ID_TOKEN`
  / `COSIGN_IDENTITY_TOKEN` or `--identity-token @PATH`. Documented
  cosign pin: `v2.5.3`. Optional Action input `sign-receipt`.

#### CI provenance and CID binding

- When `farm-notary manifest` runs inside GitHub Actions it records
  `ci_provenance` (`GITHUB_SHA`, repository, ref, workflow, run id)
  inside the content hash. `verify` fails if `git_sha` disagrees with
  `ci_provenance.sha` (issue #34).
- After `--pin` / `--pin-remote`, an OpenTimestamps proof of
  `H(content_hash || cid)` is written as `manifest.cid.ots` so a swapped
  CID fails verify. EAS is no longer the way to bind CID (issue #36).

#### Zero-install reviewer check

- `farm-notary check --manifest path/to/manifest.json` reports content
  hash, CID, claim label, and anchor status without rehashing artifacts.
  Intended for `uvx farm-notary check` / `pipx run farm-notary check`
  (issue #39). See `docs/VERIFIER.md`.

#### Docs, demo, and talks

- `docs/PRINCIPLES.md`: constraint document for refusing features
  (existence is not correctness, reader-side checks over publisher
  decoration, cherry-picking out of scope, self-assertion as input,
  publish is one-way, omission is recorded policy, trust-assumption
  budget, outsource solved infrastructure).
- README: a "Why FarmNotary" section — official record vs laptop folder,
  hash tool vs research notary, and why the domain work is allowlists,
  privacy, and honest claims (anchoring is outsourced).
- Live demo notebook (`docs/demo/`): a tiny consensus-style experiment
  notarized with the dry-run backend. `tests/test_demo.py` execs the
  notebook cells.
- Intro slide deck (`docs/slides/farmnotary.pdf`) and consensus
  walkthrough (`docs/slides/consensus.pdf`). Locked to CLAIMS.md.

#### Additive interop, archive, plugins, and chains (issue #40)

These do not change `farmnotary.manifest.v1` or the claim ladder. They
are first-cut helpers, not a 1.0 stability promise.

- `farm-notary emit-interop` dual-writes SLSA/in-toto (`slsa-provenance.json`,
  unsigned), RO-Crate, and a C2PA-style JSON claim summary (not a binary
  JUMBF).
- `farm-notary archive` deposits to Zenodo (optional DOI) and/or looks up
  a Software Heritage ID for the recorded git SHA. The DOI is not written
  back onto the manifest; SWH is lookup, not a save request.
- `farm_notary.plugins`: MLflow `on_run_end` hook and DVC `dvc.lock`
  output cover. Callers must pass an explicit allowlist; hashing the
  whole tree is not the documented path.
- `farm-notary chain` writes `provenance-chain.json` linking stage
  manifests. Linear only; no trusted server.

### Changed

#### Durable pin is the published path

`--pin-remote` (Pinata / web3.storage / pinning-service API) is the
documented default for anything you cite. `--pin` to local Kubo remains a
lab convenience and now always warns that it is not archival. The manifest
records `pin_service` (`"local"` or the remote service name) as a stamp
field (excluded from `content_hash`).

#### `verify` prints a CLAIMS.md claim card

`farm-notary verify` no longer leads with `OK <hash>` or requires reviewers
to translate an exit code. It always prints a claim card:

```
claim card
•  tamper-evident record           — pass
•  existed by time T               — pending | Bitcoin height N | missing | fail
•  pre-specified design            — precommit bound | missing | fail
•  bitwise reproducible (scoped)   — N/M[, ignored: globs]; byte-identical on x86-64 Linux in a pinned environment | missing | fail
•  not claimed: scientific correctness
```

Missing is not failure: a run with no timestamp, no precommit, and no
reproduction receipt still exits 0 if the artifacts rehash. Failed checks
still print `FAIL <detail>` under the card and exit 1. Reproduction
receipts now record the `--ignore` globs so the scoped claim can list them.

#### Docs and Action pin

- Docs rewritten against the 0.2 CLI: install from PyPI
  (`pip install "farm-notary[ots]"`), `anchor` dry-run default vs Action
  `ots`, `--verify-derived`, publish profiles, claim levels, and Action
  pin (`dooders/FarmNotary@v0.2.0`).
- `farm-notary verify` no longer fails when `derived_from` rules are present
  but `--verify-derived` was not passed. Missing is not failure; the CLI
  notes that rules were not executed. `--verify-derived` still runs them
  and can fail the check.
- GitHub Action accepts a `profile` input (`consensus`, `rl-sweep`,
  `evolution-run`).
- Package description no longer says “on-chain attestation”.
- Pending OpenTimestamps calendars are not a successful time claim
  (issue #32). Only a Bitcoin-height attestation earns L0.
- `reproduce` is documented as executing the recorded shell command
  (RCE by design). FarmNotary auto-trusts only the same local checkout
  or the same GitHub Actions repo/SHA; otherwise `--i-accept-untrusted-command`
  is required (issue #35).

### Security

- Artifact discovery and IPFS pin do not follow symlinks out of the run
  directory (issue #33).

### Breaking

#### Dirty trees cannot be precommitted or anchored (`breaking-change`)

`git_dirty` is still recorded, but recording a flag is not a code-identity
claim. `farm-notary precommit`, `farm-notary anchor`, `build_precommit()`,
`anchor_run()`, and `notarize_run()` now fail if the tree is dirty unless
`--allow-dirty` / `allow_dirty=True` is passed. Supplying a SHA without
`git_dirty` still inspects the working tree — an omitted flag is not a
pass. `require_clean_identity(None)` detects rather than skipping.

#### Privacy filter replaced with an explicit allowlist (`security`, `breaking-change`)

Previously, `farm-notary manifest` admitted every file in the run directory
whose path did not contain a private-name substring (`ballot`, `vote`, etc.).
A file named `agent_selections.csv` or `choices_raw.parquet` would be hashed
and pinned silently.

**The default is now to include nothing.**  A file is hashed, listed in the
manifest, or uploaded to IPFS only when it matches at least one declared
*publish pattern* (`--profile`, `--publish`, `notary.profile`, or
`notary.publish`).

Patterns follow `fnmatch` semantics.  A plain `*.ext` pattern matches files in
any subdirectory (not just the run-dir root).

The substring denylist (`ballot`, `vote`, `voter`, `individual_choice`,
`private`) is retained as a belt-and-braces second pass over whatever the
allowlist admits.

**Migration:** every existing `farm-notary manifest` invocation must add
`--profile <name>`, at least one `--publish <glob>` flag, or the
`notary.profile` / `notary.publish` key to the run config, or
`build_manifest()` will raise `ValueError`. Prefer a named profile.

#### New manifest fields

- `publish_patterns` — the allowlist globs that were active.
- `unmatched_count` — number of run-dir files that matched no pattern (visible
  omission, not a silent one).

Both fields are required by `Manifest.validate()` from this release onward.

#### New CLI output

`farm-notary manifest` now also prints `unmatched <N>` so operators can see
how many files were left out without revealing their names.

#### `notarize_run()` API

`notarize_run()` now accepts `publish_patterns` and `publish_profile`,
forwarded to `build_manifest()`.

---

## [0.1.0] — 2026-08-27

First release. Published to PyPI as `farm-notary==0.1.0`.

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

[Unreleased]: https://github.com/Dooders/FarmNotary/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Dooders/FarmNotary/releases/tag/v0.2.0
[0.1.0]: https://github.com/Dooders/FarmNotary/releases/tag/v0.1.0
