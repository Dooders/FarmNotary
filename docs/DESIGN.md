# FarmNotary design

## Problem

AgentFarm runs need an immutable *official* record: what code, what config, what aggregate outputs. That record should be checkable without trusting a laptop folder. The simulation itself must stay off-chain.

## Record vs execution

```
AgentFarm runner
    -> writes artifacts (metrics, summary, official allocations)
FarmNotary
    -> hashes artifacts, writes manifest.json
    -> optional: pin directory, get CID
    -> optional: anchor manifest hash via OpenTimestamps (manifest.ots)
Verifier
    -> fetch CID, rehash, check the proof commits to the manifest hash
    -> separately: re-execute from seed to test claims
```

The anchor does not know if the science is right. It knows which bytes were published, and when.

## Outsourced anchoring

FarmNotary deliberately runs **no anchoring infrastructure of its own** — no
contract, no chain integration, no keys, no gas. Earlier revisions carried a
custom `SimulationRegistry` Solidity contract plus an Ethereum client; that
whole layer was removed. Anchoring a hash publicly is a solved problem with
free, battle-tested infrastructure, and competing with it adds operational
burden (deployments, key management, fees) without adding trust.

The anchor backend is [OpenTimestamps](https://opentimestamps.org/): public
calendar servers aggregate submitted digests into Merkle trees and commit the
roots into Bitcoin transactions. Submitting is free and keyless; the resulting
proof is an ordinary `.ots` file that anyone can verify against Bitcoin
headers, with or without FarmNotary.

FarmNotary's job reduces to what is actually domain-specific: deciding *what*
gets anchored (the canonical manifest content hash) and *what never leaves the
machine* (private artifacts).

## Manifest v1

See `farm_notary/schema.py`. Required: schema id, UTC time, git SHA, config object, artifact list, artifact SHA-256 map. Optional: `official_record` (winner allocations, summary metrics), `cid`, `anchor` receipt.

Artifact discovery is recursive; paths are stored POSIX-style relative to the run directory. Hidden files and `manifest.json` itself are skipped. `Manifest.validate()` enforces the schema id, artifact list / hash map agreement, and the privacy filter.

`content_hash` excludes `cid`, `anchor`, and optional `identity` so you can stamp after upload without circular hashing. It is SHA-256 of the canonical JSON body (sorted keys, no whitespace).

Optional fields (omitted when empty so older manifests keep a stable hash):

- `derived_from` — experiment-profile rules that recompute named artifacts from sources
- `identity` — minisign or SSH signature of the content hash (no protocol token; EAS stays experimental)

## Campaign / sweep manifests

A parent record (`campaign.json`, schema `farmnotary.campaign.v1`) lists child run CIDs, seeds, and a seed-excluded config hash. A reviewer of a paper figure (100 trials, seed 0…N) verifies the parent instead of one folder.

```
farm-notary campaign --name consensus-sweep \
  --run-dir runs/seed-0 --run-dir runs/seed-1 \
  --out sweep/
farm-notary verify --campaign sweep/
```

Each child entry records `seed`, `content_hash`, `config_hash` (config minus `seed` / `rng_seed` / `random_seed`), optional `cid`, and a claim level. The parent `config_hash` is set only when every child shares it.

## Derivation claims

Byte-identity of a PNG is a renderer claim. Reviewers usually care that summary statistics recompute from raw trials. The experiment profile declares that:

```json
{
  "notary": {
    "publish": ["*.csv", "*.png", "REPORT.md"],
    "derived_from": [
      {
        "outputs": ["summary.csv", "allocation_means.csv", "REPORT.md"],
        "sources": ["trials.csv"],
        "command": "python run_experiment.py verify-report --results {run_dir}",
        "mode": "verify"
      }
    ]
  }
}
```

`mode: recompute` (default) copies sources to a temp directory, runs `command`, and byte-compares outputs. `mode: verify` runs a check that exits 0 when derived artifacts match (the AgentFarm `verify-report` pattern). `farm-notary verify` fails if a rule fails, and on success may print `claim: statistics recompute exactly from recorded sources` even when a figure is renderer-dependent.

## Environment fingerprint

`environment` is first-class, not just a lockfile hash:

- `os`, `arch`, `python`, `python_implementation`
- `packages_hash` / `package_count` (unchanged)
- optional `lockfile` + `lockfile_sha256`
- optional `numpy` (`version`, `blas`, `blas_version`, `blas_config`) when numpy is installed

This is how “bitwise on x86-64 Linux, pinned env” stays honest when someone reproduces on Apple Silicon and gets a 1-ulp diff. The paper-pack sentence names the machine class.

## Optional identity

`farm-notary sign --scheme ssh|minisign --key PATH` signs the content hash and records `{scheme, public_key, signature, principal}` on the manifest. Reviewers who know the lab’s key get “this lab published this”; everyone else still has OTS. No protocol token. EAS remains experimental and is not used here.

## Paper pack

`farm-notary paper-pack` writes `appendix.md`: CID, content hash, Bitcoin attestation (or pending), publish allowlist, unmatched count, precommit hash, claim level, environment, and a scoped reproducibility sentence. Campaigns also list child seeds and CIDs.

## Public index

`farm-notary index --registry PATH` maintains a static directory (Markdown + JSON sidecar) of published manifests: experiment name, seed, CID, claim level, date. It is a directory, not a chain, and it never writes scores or rankings. See `docs/registry.md`.

## Reusable GitHub Action

`dooders/FarmNotary` (public name `dooders/farm-notary-action`): precommit on workflow start, notarize + optional pin-remote on success, upload `manifest.json` + `manifest.ots`, fail the job if verify fails. See `docs/ACTION.md`.

## Privacy

Allowlist-first: nothing is hashed unless it matches a declared publish
pattern. Named experiment-type profiles (`consensus`, `rl-sweep`,
`evolution-run` in `farm_notary/profiles.py`) are the checked-in official
artifact lists; `--publish` and `notary.publish` append extras. The resolved
allowlist is recorded as `publish_patterns` (and `publish_profile` when a
profile was used) so the policy is part of the claim.

Relative paths containing `ballot`, `vote`, `voter`, `individual_choice`, or
`private` (any path component) are skipped by discovery, rejected by
validation, and never uploaded — including after a profile or `--publish`
glob would otherwise admit them. Do not put agent-level or citizen-level
choices in `official_record`.

## AgentFarm hook

After `ExperimentRunner` (or a dedicated consensus runner) flushes a run directory:

```python
from farm_notary import notarize_run

manifest, receipt = notarize_run(
    run_dir, git_sha=sha, runner="consensus_paradigms", config=config,
    publish_profile="consensus",
)  # dry-run until a backend is passed; pin_remote="pinata" for a durable pin
```

`notarize_run` builds and writes the manifest, optionally pins the directory (manifest included), anchors, persists any proof file, and rewrites `manifest.json` with the CID and anchor receipt.

## IPFS

`farm_notary.ipfs.IpfsClient` posts a multipart upload to a Kubo daemon's `/api/v0/add` with `wrap-with-directory=true&cid-version=1&pin=true` and takes the wrapping directory's CID as the run's content address. Standard library only; the endpoint comes from `FARM_NOTARY_IPFS_API` (default `http://127.0.0.1:5001`).

Local Kubo is a lab convenience, not archival. The published path is
`--pin-remote`, which then calls Kubo's `/api/v0/pin/remote/add` for a
registered pinning service (Pinata, web3.storage, or any Pinning Service
API). The manifest records `pin_service` (`"local"` or the service name).

The pinned tree includes `manifest.json`. Because `content_hash` excludes
`cid` / `pin_service` / `anchor`, the copy inside the pinned tree hashes to
the same value that gets anchored, even though the local copy is later stamped.

## Anchoring flow

`farm_notary.ots` (requires `farm-notary[ots]` for the `opentimestamps` library):

1. **Stamp** (`anchor --backend ots`): submit the manifest content hash to the
   configured calendars (`FARM_NOTARY_CALENDARS` or the public pools), merge
   their responses, and write the proof to `manifest.ots`. The proof commits
   to the *content hash digest directly* — no privacy nonce, because the
   manifest hash is meant to be public.
2. **Upgrade** (`upgrade`): calendars batch digests into Bitcoin on their own
   schedule (typically hours). The upgrade command asks each pending calendar
   for the completed path to a Bitcoin block header attestation and rewrites
   the proof. Exit code 1 means still pending; run it again later.
3. **Verify** (`verify`): rehash artifacts, recompute the content hash, check
   the proof commits to it, and print a CLAIMS.md claim card — tamper-evident
   record, existed by time T (pending or Bitcoin height), pre-specified
   design, bitwise reproducible (scoped), and an explicit non-claim of
   scientific correctness. Checking the Bitcoin merkle path against a local
   node is left to the standard `ots verify` tooling — FarmNotary validates
   commitment integrity, not block headers.

Because `manifest.ots` commits to the content hash rather than the raw file
bytes, stamping `manifest.json` with `cid`/`anchor` after anchoring never
invalidates the proof.

## Provenance and reproduction

The manifest records everything a stranger needs to re-derive the run:
`command` (with a `{run_dir}` placeholder), `config`, `git_sha` plus a
`git_dirty` flag (a dirty tree means the sha does not identify the code that
ran, so it is recorded, not hidden — and `precommit` / `anchor` refuse it
unless `--allow-dirty`. Omitting `git_dirty` still inspects the working
tree; a supplied SHA is not a bypass), and
`environment` (python, platform, `system`, `machine`, a hash of the installed
package set, optional lockfile hash). `system` + `machine` are what the
claim card uses to decide whether it may emit the scoped sentence in
[CLAIMS.md](CLAIMS.md) (`byte-identical on x86-64 Linux in a pinned
environment`). Other hardware is reported, not claimed.

`farm-notary reproduce` turns reproducibility from a claim into a procedure:
re-run the recorded command into a fresh directory, rehash, byte-compare
against the manifest. A mismatch is classified (`embedded_absolute_path`,
`timestamp`, `float_print_format`, `video_encoder`) so a packaging bug is
not read as a failed result — the consensus `{run_dir}` fix (7/7 → 8/8) is
the type specimen. Known-nondeterministic artifacts (videos, databases)
are excluded per-run with `--ignore` globs and recorded as excluded — the
claim is scoped, never blanket. A successful reproduction writes
`reproduction.json` (rerunner environment, per-file results, diagnostics,
the original manifest hash) whose own hash can be anchored via
OpenTimestamps (`reproduction.ots`), making "independently reproduced" a
timestamped, third-party-checkable statement.

Notary metadata (`manifest.json`, `reproduction.json`, `campaign.json`,
`appendix.md`, `*.ots`) is never treated as an artifact: discovery skips it,
so stamping and receipts don't perturb the anchored content hash.

## Backends

1. Dry-run (default; returns the payload that would be submitted)
2. `ots` — OpenTimestamps calendars into Bitcoin (implemented, `farm_notary/ots.py`)
3. `eas` — EAS attestation on Base / Base Sepolia (implemented, `farm_notary/eas.py`). EAS is an OP-stack predeploy (`0x4200...0021`; SchemaRegistry `0x4200...0020`), so the addresses are identical on Base and Base Sepolia. Schema: `bytes32 manifestHash,string cid`, non-revocable. `web3` is a lazy import behind the `[chain]` extra. `anchor_run` writes the receipt (backend, tx hash, attestation UID, chain id) into `manifest.anchor`. `content_hash` excludes `cid` and `anchor`, so the attested hash is stable across stamping.

## Consensus experiment note

A companion experiment (individual vs party vs score vs latent_match allocation) can live in AgentFarm and use FarmNotary only at the end of a sweep. FarmNotary is not the experiment.
