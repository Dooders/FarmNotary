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

`content_hash` excludes `cid` and `chain` so you can stamp after upload without circular hashing.

## Privacy

Filenames containing `ballot`, `vote`, `voter`, `individual_choice`, or `private` are skipped. Do not put agent-level or citizen-level choices in `official_record`.

## AgentFarm hook

After `ExperimentRunner` (or a dedicated consensus runner) flushes a run directory:

```python
from farm_notary import build_manifest
from farm_notary.manifest import write_manifest
from farm_notary.anchor import anchor_run

manifest = build_manifest(run_dir, git_sha=sha, runner="consensus_paradigms", config=config)
write_manifest(manifest, run_dir)
anchor_run(manifest)  # dry-run until a backend is configured
```

## Backends (later)

1. Dry-run (now)
2. IPFS pin + print CID
3. EAS attestation on Base/Sepolia
4. `SimulationRegistry` if a dedicated contract is wanted

## Consensus experiment note

A companion experiment (individual vs party vs score vs latent_match allocation) can live in AgentFarm and use FarmNotary only at the end of a sweep. FarmNotary is not the experiment.
