# FarmNotary

[![CI](https://github.com/Dooders/FarmNotary/actions/workflows/ci.yml/badge.svg)](https://github.com/Dooders/FarmNotary/actions/workflows/ci.yml)

Notary for [AgentFarm](https://github.com/Dooders/AgentFarm) runs.

Simulation stays off-chain. FarmNotary writes a manifest (config, code identity, artifact hashes), optionally pins the result directory to IPFS, and anchors the manifest hash publicly via [OpenTimestamps](https://opentimestamps.org/) — free calendar servers that batch digests into Bitcoin.

Immutability is not correctness. Re-run from the committed seed to check the science. The anchor only makes "this is the file we published" hard to walk back.

## Scope

**In**

- Canonical `manifest.json` schema (recursive artifact discovery, SHA-256 map)
- Optional IPFS upload via the Kubo HTTP API (stdlib only, no extra needed)
- Anchoring via OpenTimestamps: no contract, no keys, no gas
- Verify: rehash artifacts locally, check the proof commits to the manifest hash

**Out**

- Running our own anchoring infrastructure (contracts, chains, servers) — that layer is outsourced
- Running simulations inside the EVM
- Publishing individual voter or agent ballots
- Social scores as a product

## Official record vs private choice

If the experiment is a consensus / selection paradigm:

- Voter (or agent) choice stays private
- What gets notarized is the *official* record: config, code hash, aggregate metrics, winner allocations

### Allowlist-first privacy model

**Nothing is hashed, listed, or uploaded by default.**  A file is included in
the manifest only when its path matches a declared *publish pattern*:

```bash
farm-notary manifest --run-dir path/to/run \
  --publish 'summary.csv' --publish 'allocation_means.csv' --publish '*.png'
```

Or in the run config (so the policy is versioned with the experiment):

```json
{
  "notary": {
    "publish": ["summary.csv", "allocation_means.csv", "*.png", "REPORT.md"]
  }
}
```

Files not covered by any pattern are **excluded** and counted in the manifest
(`unmatched_count`).  The CLI warns loudly about the count (never the names).

As a belt-and-braces second pass, files whose paths contain `ballot`, `vote`,
`voter`, `individual_choice`, or `private` are always excluded even if a
publish pattern would otherwise admit them.

## Install

```bash
pip install -e .            # manifest + verify, no chain deps
pip install -e ".[chain]"   # adds web3 for the EAS backend
```

Extras:

- `farm-notary[chain]` — adds `web3`, needed for the EAS backend.
- `farm-notary[ots]` — adds the `opentimestamps` library, needed to anchor and to check proofs. Manifest building and local verification are stdlib-only.
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

Anchoring for real, with IPFS pinning:

```bash
farm-notary anchor --run-dir path/to/run --pin --backend ots
# ... hours later, once a calendar has batched the digest into Bitcoin:
farm-notary upgrade --run-dir path/to/run
farm-notary verify --run-dir path/to/run
```

`--backend ots` submits the manifest content hash to public OpenTimestamps calendars (override with `--calendar` or `FARM_NOTARY_CALENDARS`) and writes the proof to `manifest.ots`. `--pin` uploads the run directory (manifest included) to the Kubo API at `FARM_NOTARY_IPFS_API` (default `http://127.0.0.1:5001`) and stores the root CID in the manifest. `upgrade` completes the pending proof with a Bitcoin attestation; `verify` then reports the block height.

## Anchoring with EAS (experimental)

> **Experimental.** The `eas` backend requires a funded private key, costs
> gas on Base, and ties verification to an attester address that verifiers
> must know in advance.  `--backend ots` is the recommended default: no key,
> no gas, Bitcoin-backed, no prior relationship with the verifier required.

See [docs/EAS.md](docs/EAS.md) for full EAS configuration, schema details,
and a tradeoff table comparing OTS and EAS.

## AgentFarm inheritance

AgentFarm should depend on a version pin (`farm-notary>=0.1,<0.2`) via an extra such as `farm[notary]`, then call one function after the runner writes artifacts:

```python
from farm_notary import notarize_run
from farm_notary.ots import OpenTimestampsBackend

manifest, receipt = notarize_run(
    run_dir,
    git_sha=sha,
    runner="consensus_paradigms",
    config=config,
    backend=OpenTimestampsBackend(),
)
```

The example above uses `OpenTimestampsBackend()` (from `farm_notary.ots`); add `pin=True` to also upload the run directory to IPFS. Do not submodule unless the API is still thrashing.

## Reproducing a run

If the manifest records the command that produced it (`--command`, with
`{run_dir}` marking the output directory), anyone can re-execute and
byte-compare:

```bash
farm-notary manifest --run-dir path/to/run \
  --command "python run_experiment.py --seed 0 --out {run_dir}" \
  --lockfile requirements.lock
farm-notary reproduce --run-dir path/to/run --ignore '*.mp4' --anchor
```

`reproduce` re-runs the command into a fresh directory, compares every listed
artifact's bytes against the manifest, and writes a `reproduction.json`
receipt (rerunner's environment, per-file results). `--anchor` timestamps the
receipt itself via OpenTimestamps, so "independently reproduced" comes with a
proof. `verify` checks the receipt against the manifest and its proof.

See [docs/CLAIMS.md](docs/CLAIMS.md) for exactly which claim each check earns.

## Verifying someone else's claim

1. Fetch the CID (`ipfs get <cid>`), or obtain the run directory some other way.
2. `farm-notary verify --run-dir <dir>`
3. Exit code 0 means: these exact bytes hash to a manifest whose content hash the OpenTimestamps proof commits to, and the proof is attested (or pending) in Bitcoin. It does not mean the science is right — re-run from the committed seed for that.

The proof in `manifest.ots` is a standard OpenTimestamps file: it commits to the manifest *content hash* (SHA-256 of the canonical manifest body, excluding the `cid` and `anchor` stamp fields), so it stays valid after the manifest is stamped with upload results. Full trust-minimized verification against your own Bitcoin node is possible with the standard `ots` tooling.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

## Status

MVP. Manifest, hashing, IPFS pinning, OpenTimestamps anchoring (dry-run default), proof upgrade, verification, and EAS attestation on Base/Base Sepolia are implemented and tested. The anchoring layer is deliberately outsourced — earlier revisions carried a custom `SimulationRegistry` contract, which was removed in favor of existing public infrastructure.
