# Changelog

All notable changes to FarmNotary will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
FarmNotary uses [Semantic Versioning](https://semver.org/).

---

## [0.1.0] — 2026-08-28

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
  placeholder) into a fresh temp directory, byte-compares artifacts, writes
  `reproduction.json` (rerunner environment, per-file results, original
  manifest hash).
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
