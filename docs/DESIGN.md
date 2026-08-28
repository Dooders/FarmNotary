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

## Backends

1. Dry-run (default)
2. IPFS pin + print CID (later)
3. EAS attestation on Base / Base Sepolia (implemented, `farm_notary/eas.py`)
4. `SimulationRegistry` if a dedicated contract is wanted (later)

## EAS backend

EAS is an OP-stack predeploy (`0x4200...0021`; SchemaRegistry `0x4200...0020`), so the addresses are identical on Base and Base Sepolia.

- Schema: `bytes32 manifestHash,string cid`, non-revocable, no resolver. A notary record must not be revocable: unrevocability is the product.
- The schema UID is `keccak256(schema ++ resolver ++ revocable)`, computed locally rather than parsed from registration events, so it is known before registering and identical on every chain.
- Attestations carry no recipient. The audience is the open public, not a subject address; the attester address is the trust anchor and should be published next to results.
- `EASBackend` implements the same `AnchorBackend` protocol as dry-run. `web3` is a lazy import behind the `[chain]` extra, so the base package stays dependency-free.
- `anchor_run` writes the receipt (backend, tx hash, attestation UID, chain id) into `manifest.chain`. `content_hash` excludes `cid` and `chain`, so the attested hash is stable across stamping.

## Consensus experiment note

A companion experiment (individual vs party vs score vs latent_match allocation) can live in AgentFarm and use FarmNotary only at the end of a sweep. FarmNotary is not the experiment.
