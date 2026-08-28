# FarmNotary

Notary for [AgentFarm](https://github.com/Dooders/AgentFarm) runs.

Simulation stays off-chain. FarmNotary writes a manifest (config, code identity, artifact hashes), optionally pins the result directory, and anchors `manifest_hash + CID` on a public ledger.

Immutability is not correctness. Re-run from the committed seed to check the science. The chain only makes "this is the file we published" hard to walk back.

## Scope

**In**

- Canonical `manifest.json` schema
- SHA-256 of result artifacts
- Optional IPFS / content-addressed upload
- On-chain register (EAS or a tiny `register(bytes32,string)` contract)
- Verify: fetch CID, rehash, match chain

**Out**

- Running simulations inside the EVM
- Publishing individual voter or agent ballots
- Social scores as a product

## Official record vs private choice

If the experiment is a consensus / selection paradigm:

- Voter (or agent) choice stays private
- What gets notarized is the *official* record: config, code hash, aggregate metrics, winner allocations

## Install

```bash
pip install -e .            # manifest + verify, no chain deps
pip install -e ".[chain]"   # adds web3 for the EAS backend
```

## Quick start

```bash
python -m farm_notary.cli manifest --run-dir path/to/run
python -m farm_notary.cli verify --manifest path/to/run/manifest.json
```

Anchor is opt-in and needs network config. Dry-run is the default.

## Anchoring with EAS

The `eas` backend attests `(manifest_hash, cid)` on [EAS](https://attest.org), which is a predeploy on Base and Base Sepolia (contract `0x4200...0021` on both).

Schema (non-revocable, no resolver):

```text
bytes32 manifestHash,string cid
```

Its UID is derived deterministically and is the same on every chain:

```text
0xc3d61e7073e9dcc59f65fe1a8a4bfd0b3e2c5fd2e32ad1c1d6c473fb1274ac08
```

Configuration is via environment variables:

| Variable | Meaning | Default |
| --- | --- | --- |
| `FARM_NOTARY_CHAIN` | `base` or `base-sepolia` | `base-sepolia` |
| `FARM_NOTARY_PRIVATE_KEY` | Attester key (funded with a little ETH for gas) | required |
| `FARM_NOTARY_RPC_URL` | JSON-RPC endpoint | public RPC for the chain |
| `FARM_NOTARY_EAS_SCHEMA_UID` | Schema UID to attest against | the UID above |
| `FARM_NOTARY_EAS_ADDRESS` | EAS contract | OP-stack predeploy |

One-time per chain, register the schema, then anchor runs:

```bash
python -m farm_notary.cli register-schema
python -m farm_notary.cli anchor --run-dir path/to/run --backend eas --cid <cid>
```

A successful anchor writes the receipt (tx hash, attestation UID, chain id) back into `manifest.json` and prints an [EASScan](https://base-sepolia.easscan.org) link.

The attester address is the trust anchor: attestations only mean something to verifiers who know which address is yours. Publish your attester address (and the schema UID) wherever you publish results, and keep attesting from that address. Verifiers then check: look up the attestation on EASScan, confirm the attester, fetch the CID, rehash with `farm-notary verify`, and compare to the attested `manifestHash`.

## AgentFarm inheritance

AgentFarm should depend on a version pin (`farm-notary>=0.1,<0.2`) via an extra such as `farm[notary]`, then call `anchor_run(run_dir)` after the runner writes artifacts. Do not submodule unless the API is still thrashing.

## Status

Manifest schema, local hashing, and the EAS chain adapter are in. IPFS pinning is next.
