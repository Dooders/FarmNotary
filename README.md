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
pip install -e .
```

Optional extras later: `farm-notary[ipfs]`, `farm-notary[chain]`.

## Quick start

```bash
python -m farm_notary.cli manifest --run-dir path/to/run
python -m farm_notary.cli verify --manifest path/to/run/manifest.json
```

Anchor is opt-in and needs network config. Dry-run is the default.

## AgentFarm inheritance

AgentFarm should depend on a version pin (`farm-notary>=0.1,<0.2`) via an extra such as `farm[notary]`, then call `anchor_run(run_dir)` after the runner writes artifacts. Do not submodule unless the API is still thrashing.

## Status

Scaffold. Schema and local hashing land first. Chain adapters second.
