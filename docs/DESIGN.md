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

`content_hash` excludes `cid` and `anchor` so you can stamp after upload without circular hashing. It is SHA-256 of the canonical JSON body (sorted keys, no whitespace).

## Privacy

Relative paths containing `ballot`, `vote`, `voter`, `individual_choice`, or `private` (any path component) are skipped by discovery, rejected by validation, and never uploaded. Do not put agent-level or citizen-level choices in `official_record`.

## AgentFarm hook

After `ExperimentRunner` (or a dedicated consensus runner) flushes a run directory:

```python
from farm_notary import notarize_run

manifest, receipt = notarize_run(
    run_dir, git_sha=sha, runner="consensus_paradigms", config=config
)  # dry-run until a backend is passed; pin=True to upload to IPFS
```

`notarize_run` builds and writes the manifest, optionally pins the directory (manifest included), anchors, persists any proof file, and rewrites `manifest.json` with the CID and anchor receipt.

## IPFS

`farm_notary.ipfs.IpfsClient` posts a multipart upload to a Kubo daemon's `/api/v0/add` with `wrap-with-directory=true&cid-version=1&pin=true` and takes the wrapping directory's CID as the run's content address. Standard library only; the endpoint comes from `FARM_NOTARY_IPFS_API` (default `http://127.0.0.1:5001`).

The pinned tree includes `manifest.json`. Because `content_hash` excludes `cid`/`anchor`, the copy inside the pinned tree hashes to the same value that gets anchored, even though the local copy is later stamped.

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
ran, so it is recorded, not hidden), and `environment` (python, platform, a
hash of the installed package set, optional lockfile hash).

`farm-notary reproduce` turns reproducibility from a claim into a procedure:
re-run the recorded command into a fresh directory, rehash, byte-compare
against the manifest. Known-nondeterministic artifacts (videos, databases)
are excluded per-run with `--ignore` globs and recorded as excluded — the
claim is scoped, never blanket. A successful reproduction writes
`reproduction.json` (rerunner environment, per-file results, the original
manifest hash) whose own hash can be anchored via OpenTimestamps
(`reproduction.ots`), making "independently reproduced" a timestamped,
third-party-checkable statement.

Notary metadata (`manifest.json`, `reproduction.json`, `*.ots`) is never
treated as an artifact: discovery skips it, so stamping and receipts don't
perturb the anchored content hash.

## Backends

1. Dry-run (default; returns the payload that would be submitted)
2. `ots` — OpenTimestamps calendars into Bitcoin (implemented, `farm_notary/ots.py`)
3. `eas` — EAS attestation on Base / Base Sepolia (implemented, `farm_notary/eas.py`). EAS is an OP-stack predeploy (`0x4200...0021`; SchemaRegistry `0x4200...0020`), so the addresses are identical on Base and Base Sepolia. Schema: `bytes32 manifestHash,string cid`, non-revocable. `web3` is a lazy import behind the `[chain]` extra. `anchor_run` writes the receipt (backend, tx hash, attestation UID, chain id) into `manifest.anchor`. `content_hash` excludes `cid` and `anchor`, so the attested hash is stable across stamping.

## Consensus experiment note

A companion experiment (individual vs party vs score vs latent_match allocation) can live in AgentFarm and use FarmNotary only at the end of a sweep. FarmNotary is not the experiment.
