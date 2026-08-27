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
    -> optional: submit (manifest_hash, CID) to EAS or SimulationRegistry
Verifier
    -> fetch CID, rehash, compare to chain
    -> separately: re-execute from seed to test claims
```

The chain does not know if the science is right. It knows which bytes were published.

## Manifest v1

See `farm_notary/schema.py`. Required: schema id, UTC time, git SHA, config object, artifact list, artifact SHA-256 map. Optional: `official_record` (winner allocations, summary metrics), `cid`, `chain` receipt.

Artifact discovery is recursive; paths are stored POSIX-style relative to the run directory. Hidden files and `manifest.json` itself are skipped. `Manifest.validate()` enforces the schema id, artifact list / hash map agreement, and the privacy filter.

`content_hash` excludes `cid` and `chain` so you can stamp after upload without circular hashing. It is SHA-256 of the canonical JSON body (sorted keys, no whitespace).

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

`notarize_run` builds and writes the manifest, optionally pins the directory (manifest included), anchors, and rewrites `manifest.json` with the CID and chain receipt.

## IPFS

`farm_notary.ipfs.IpfsClient` posts a multipart upload to a Kubo daemon's `/api/v0/add` with `wrap-with-directory=true&cid-version=1&pin=true` and takes the wrapping directory's CID as the run's content address. Standard library only; the endpoint comes from `FARM_NOTARY_IPFS_API` (default `http://127.0.0.1:5001`).

The pinned tree includes `manifest.json`. Because `content_hash` excludes `cid`/`chain`, the copy inside the pinned tree hashes to the same value that gets anchored, even though the local copy is later stamped.

## Chain

Two paths, deliberately asymmetric:

- **Read / verify** (`farm_notary.registry.get_record`): raw JSON-RPC `eth_call` to `records(bytes32)` using urllib and a vendored pure-Python keccak-256 (`farm_notary/keccak.py`) for the selector. Anyone can verify with zero dependencies.
- **Write** (`farm_notary.registry.RegistryBackend`): signs and submits `register(bytes32,string)`; needs web3 via `farm-notary[chain]`. Key comes from `FARM_NOTARY_PRIVATE_KEY`.

## Backends

1. Dry-run (default; returns the payload that would be submitted)
2. `registry` — `SimulationRegistry.register(manifestHash, cid)` (implemented)
3. EAS attestation on Base/Sepolia (later)

## Consensus experiment note

A companion experiment (individual vs party vs score vs latent_match allocation) can live in AgentFarm and use FarmNotary only at the end of a sweep. FarmNotary is not the experiment.
