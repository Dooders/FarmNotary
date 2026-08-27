# FarmNotary

Notary for [AgentFarm](https://github.com/Dooders/AgentFarm) runs.

Simulation stays off-chain. FarmNotary writes a manifest (config, code identity, artifact hashes), optionally pins the result directory to IPFS, and anchors `manifest_hash + CID` on a public ledger.

Immutability is not correctness. Re-run from the committed seed to check the science. The chain only makes "this is the file we published" hard to walk back.

## Scope

**In**

- Canonical `manifest.json` schema (recursive artifact discovery, SHA-256 map)
- Optional IPFS upload via the Kubo HTTP API (stdlib only, no extra needed)
- On-chain register against `contracts/SimulationRegistry.sol` (`register(bytes32,string)`)
- Verify: rehash artifacts locally, then match the manifest hash and CID on chain

**Out**

- Running simulations inside the EVM
- Publishing individual voter or agent ballots
- Social scores as a product

## Official record vs private choice

If the experiment is a consensus / selection paradigm:

- Voter (or agent) choice stays private
- What gets notarized is the *official* record: config, code hash, aggregate metrics, winner allocations

Files whose paths contain `ballot`, `vote`, `voter`, `individual_choice`, or `private` are never hashed, listed, or uploaded.

## Install

```bash
pip install -e .
```

Extras:

- `farm-notary[chain]` — adds web3, needed only to *submit* transactions. Reads and verification use plain JSON-RPC over the standard library.
- IPFS upload needs no extra, just a reachable Kubo daemon.

## Quick start

```bash
# 1. Hash the run directory into manifest.json
farm-notary manifest --run-dir path/to/run --config config.json

# 2. Verify: rehash and compare
farm-notary verify --run-dir path/to/run

# 3. Anchor (dry-run by default: prints the payload that would be submitted)
farm-notary anchor --run-dir path/to/run
```

With IPFS and a deployed registry:

```bash
export FARM_NOTARY_RPC_URL=https://sepolia.base.org
export FARM_NOTARY_CONTRACT=0xYourRegistry
export FARM_NOTARY_PRIVATE_KEY=0x...

farm-notary anchor --run-dir path/to/run --pin --backend registry
farm-notary verify --run-dir path/to/run --chain
```

`--pin` uploads the run directory (manifest included) to the Kubo API at `FARM_NOTARY_IPFS_API` (default `http://127.0.0.1:5001`) and stores the root CID in the manifest. `verify --chain` recomputes the manifest hash, calls `records(bytes32)` on the registry, and cross-checks the stored CID.

## AgentFarm inheritance

AgentFarm should depend on a version pin (`farm-notary>=0.1,<0.2`) via an extra such as `farm[notary]`, then call one function after the runner writes artifacts:

```python
from farm_notary import notarize_run

manifest, receipt = notarize_run(run_dir, git_sha=sha, runner="consensus_paradigms", config=config)
```

Dry-run by default; pass `backend=` (see `farm_notary.registry.RegistryBackend`) and `pin=True` to publish for real. Do not submodule unless the API is still thrashing.

## Verifying someone else's claim

1. Fetch the CID (`ipfs get <cid>`), or obtain the run directory some other way.
2. `farm-notary verify --run-dir <dir> --chain --rpc-url <url> --contract <addr>`
3. Exit code 0 means: these exact bytes hash to a manifest that is registered on chain, and the CID on chain matches. It does not mean the science is right — re-run from the committed seed for that.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

## Status

MVP. Manifest, hashing, IPFS pinning, registry anchoring (dry-run and web3-backed), and chain verification are implemented and tested. Not yet done: EAS attestation backend, deployment tooling for `SimulationRegistry.sol`.
