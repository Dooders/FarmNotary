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

## IPFS persistence

**Pinning to a local Kubo daemon is not archival.**

The CID is content-addressed and immutable, but the _content_ is only reachable as long as at least one node holding it is online and responsive. A local daemon on a laptop goes offline when the lid closes — the manifest will still record the CID, but `ipfs get <cid>` will hang with no diagnosis.

After each `--pin`, FarmNotary checks whether the CID is immediately resolvable through the public IPFS gateway (`https://ipfs.io/ipfs/<cid>`) and records the result in the manifest as `cid_reachable: true|false` with a timestamp. A warning is printed when the CID is not reachable.

### Delegating to a persistent pinning service

Use `--pin-remote <service>` to delegate to a remote pinning service (Pinata, web3.storage, or any service implementing the [IPFS Pinning Service API](https://ipfs.github.io/pinning-services-api-spec/)) via Kubo's built-in remote-pin API:

```bash
# Register the service once (Kubo stores the endpoint + token):
ipfs pin remote service add pinata https://api.pinata.cloud/psa <jwt-token>

# Then pin and delegate in one step:
farm-notary anchor --run-dir path/to/run --pin --pin-remote pinata --backend ots
```

`--pin-remote` implies `--pin`; the run directory is always uploaded before the remote pin is requested.

To skip the gateway check (e.g. in CI where the CID will propagate later), pass `--no-check-gateway`.

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

AgentFarm should depend on a version pin (`farm-notary>=0.1,<0.2`) via an extra such as `farm[notary]`, then call one function after the runner writes artifacts:

```python
from farm_notary import notarize_run

manifest, receipt = notarize_run(run_dir, git_sha=sha, runner="consensus_paradigms", config=config)
```

Dry-run by default; pass `backend=OpenTimestampsBackend()` (from `farm_notary.ots`) and `pin=True` to publish for real. Do not submodule unless the API is still thrashing.

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

1. Fetch the CID (`ipfs get <cid>`), or obtain the run directory some other way. If `ipfs get` hangs, the CID may not be pinned on any reachable node — check the manifest's `cid_reachable` field and `cid_reachable_checked_utc` timestamp, and try the public gateway: `https://ipfs.io/ipfs/<cid>`.
2. `farm-notary verify --run-dir <dir>`
3. Exit code 0 means: these exact bytes hash to a manifest whose content hash the OpenTimestamps proof commits to, and the proof is attested (or pending) in Bitcoin. It does not mean the science is right — re-run from the committed seed for that.

`verify` distinguishes two failure modes:

- **`artifact unreachable: <name>`** — the file is absent from the local directory; if the manifest records a CID, the hint `(fetch with: ipfs get <cid>)` is appended.
- **`artifact hash mismatch: <name>`** — the file is present but its content differs from what was hashed at notarization time.

The proof in `manifest.ots` is a standard OpenTimestamps file: it commits to the manifest *content hash* (SHA-256 of the canonical manifest body, excluding the `cid` and `anchor` stamp fields), so it stays valid after the manifest is stamped with upload results. Full trust-minimized verification against your own Bitcoin node is possible with the standard `ots` tooling.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

## Status

MVP. Manifest, hashing, IPFS pinning, OpenTimestamps anchoring (dry-run default), proof upgrade, verification, and EAS attestation on Base/Base Sepolia are implemented and tested. The anchoring layer is deliberately outsourced — earlier revisions carried a custom `SimulationRegistry` contract, which was removed in favor of existing public infrastructure.
