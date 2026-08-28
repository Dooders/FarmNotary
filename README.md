# FarmNotary

[![CI](https://github.com/Dooders/FarmNotary/actions/workflows/ci.yml/badge.svg)](https://github.com/Dooders/FarmNotary/actions/workflows/ci.yml)

Notary for [AgentFarm](https://github.com/Dooders/AgentFarm) runs.

Simulation stays off-chain. FarmNotary writes a manifest (config, code identity, artifact hashes), optionally pins the result directory to IPFS, and anchors the manifest hash publicly via [OpenTimestamps](https://opentimestamps.org/) — free calendar servers that batch digests into Bitcoin.

Immutability is not correctness. Re-run from the committed seed to check the science. The anchor only makes "this is the file we published" hard to walk back.

## Scope

**In**

- Canonical `manifest.json` schema (recursive artifact discovery, SHA-256 map)
- Campaign / sweep parent manifests (child CIDs, seeds, shared config hash)
- Derivation claims (`notary.derived_from` in the experiment profile)
- Environment fingerprint (OS, arch, Python, optional numpy/BLAS build)
- Optional minisign / SSH identity on the content hash (no protocol token)
- Paper-pack appendix snippet and a static public index (no scores)
- Reusable GitHub Action (`dooders/farm-notary-action`)
- Optional IPFS upload via the Kubo HTTP API (stdlib only, no extra needed)
- Anchoring via OpenTimestamps: no contract, no keys, no gas
- Verify: rehash artifacts locally, check the proof commits to the manifest hash

**Out**

- Running our own anchoring infrastructure (contracts, chains, servers) — that layer is outsourced
- Running simulations inside the EVM
- Publishing individual voter or agent ballots
- Social scores, rankings, or reputation as a product
- A public index that is anything other than a directory of published manifests

## Official record vs private choice

If the experiment is a consensus / selection paradigm:

- Voter (or agent) choice stays private
- What gets notarized is the *official* record: config, code hash, aggregate metrics, winner allocations

### Allowlist-first privacy model

**Nothing is hashed, listed, or uploaded by default.** Prefer a named
experiment-type profile so labs do not invent globs (and forget `REPORT.md`
or include a path they should not):

```bash
farm-notary manifest --run-dir path/to/run --profile consensus
```

Profiles (`consensus`, `rl-sweep`, `evolution-run`) are checked-in lists of
official artifacts. The denylist still applies. The resolved allowlist is
recorded on the manifest as `publish_patterns` (and `publish_profile`) so the
policy is part of the claim.

Or declare extra globs — appended to the profile, or used alone:

```bash
farm-notary manifest --run-dir path/to/run --profile consensus \
  --publish 'notes.md'
```

```json
{
  "notary": {
    "profile": "consensus",
    "publish": ["extra_table.csv"]
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
pip install farm-notary          # manifest + verify, no chain deps
pip install "farm-notary[chain]" # adds web3 for the EAS backend
pip install "farm-notary[ots]"   # adds opentimestamps for anchoring and proof checks
```

Extras:

- `farm-notary[chain]` — adds `web3`, needed for the EAS backend.
- `farm-notary[ots]` — adds the `opentimestamps` library, needed to anchor and to check proofs. Manifest building and local verification are stdlib-only.
- IPFS upload needs no extra, just a reachable Kubo daemon.

## Schema stability

Every manifest records the tool version that created it (`farm_notary_version`) and the schema identifier (`schema`).

**Promise:** within the `0.x` line, schema changes are **minor-version bumps** (e.g. `0.1 → 0.2`). The `verify` command stays backward-compatible with all older manifests: fields added in a later version are simply ignored when reading an older manifest. Reading a manifest produced by a *newer* tool emits a warning and still attempts verification. Optional fields (`derived_from`, `identity`) are omitted when empty so a v1 body keeps a stable content hash.

Breaking changes (new required fields, renamed keys, removed fields) are reserved for a `1.0` bump and will be communicated in the changelog with a migration guide.

## Quick start

```bash
# 1. Hash the run directory into manifest.json
farm-notary manifest --run-dir path/to/run --config config.json

# 2. Verify: print a CLAIMS.md claim card
farm-notary verify --run-dir path/to/run

# 3. Anchor to OpenTimestamps calendars and write proof to manifest.ots
farm-notary anchor --run-dir path/to/run --backend ots
```

Anchoring for real, with a durable pin (the published path):

```bash
# Register a pinning service once (Kubo stores the endpoint + token):
ipfs pin remote service add pinata https://api.pinata.cloud/psa <jwt-token>

farm-notary anchor --run-dir path/to/run --pin-remote pinata --backend ots
# ... hours later, once a calendar has batched the digest into Bitcoin:
farm-notary upgrade --run-dir path/to/run
farm-notary verify --run-dir path/to/run
```

`--backend ots` submits the manifest content hash to public OpenTimestamps calendars (override with `--calendar` or `FARM_NOTARY_CALENDARS`) and writes the proof to `manifest.ots`. `--pin-remote` uploads the run directory (manifest included) to local Kubo, then delegates the pin to a registered service (Pinata, web3.storage, or any [IPFS Pinning Service API](https://ipfs.github.io/pinning-services-api-spec/)). That durable pin is what you cite in a paper or academy writeup. `upgrade` completes the pending proof with a Bitcoin attestation; `verify` then reports **existed by time T** as a Bitcoin height.

`precommit` and `anchor` refuse a dirty git tree by default: the recorded SHA
does not identify the code, so it is not a code-identity claim. `git_dirty` is
still recorded on the manifest. Supplying `--git-sha` without a dirty flag
still inspects the working tree. Pass `--allow-dirty` (or `allow_dirty=True`)
to make an explicit exception.

## IPFS persistence

**`--pin-remote` is the published path.** Local Kubo is a lab convenience.

The CID is content-addressed and immutable, but the _content_ is only reachable as long as at least one node holding it is online and responsive. A local daemon on a laptop goes offline when the lid closes — the manifest will still record the CID, but `ipfs get <cid>` will hang with no diagnosis. Do not cite a local-only pin in a paper or academy writeup.

`--pin-remote <service>` uploads via Kubo, then delegates to a remote pinning service (Pinata, web3.storage, or any [IPFS Pinning Service API](https://ipfs.github.io/pinning-services-api-spec/)). Register the service once:

```bash
ipfs pin remote service add pinata https://api.pinata.cloud/psa <jwt-token>
farm-notary anchor --run-dir path/to/run --pin-remote pinata --backend ots
```

`--pin-remote` implies `--pin`. The manifest records `pin_service` (`"local"` or the service name) so the pin path is part of the claim.

`--pin` alone still uploads to local Kubo and warns. Use it at the bench; switch to `--pin-remote` before you publish.

After each pin, FarmNotary checks whether the CID is immediately resolvable through the public IPFS gateway (`https://ipfs.io/ipfs/<cid>`) and records `cid_reachable` with a timestamp. A warning is printed when the CID is not reachable.

To skip the gateway check (e.g. in CI where the CID will propagate later), pass `--no-check-gateway`.

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

The example above uses `OpenTimestampsBackend()` (from `farm_notary.ots`); add `pin_remote="pinata"` for a durable pin (the published path), or `pin=True` for a local Kubo pin (lab convenience). Do not submodule unless the API is still thrashing.

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
receipt (rerunner's environment, per-file results, mismatch diagnostics).
A byte-diff is classified (`embedded_absolute_path`, `timestamp`,
`float_print_format`, `video_encoder`) and labeled **not a science failure**
when that is what it is. `--anchor` timestamps the receipt itself via
OpenTimestamps, so "independently reproduced" comes with a proof. `verify` reports the receipt as **bitwise reproducible (scoped)** —
`N/M` of compared artifacts, any `--ignore` globs, and the only sentence the
tool may emit today: *byte-identical on x86-64 Linux in a pinned
environment*. A match on other hardware still reports `N/M` and refuses a
cross-hardware claim. See [docs/CLAIMS.md](docs/CLAIMS.md).

See [docs/CLAIMS.md](docs/CLAIMS.md) for exactly which claim each check earns.

## Campaigns, paper pack, and the public index

A sweep is a parent record, not a pile of folders:

```bash
farm-notary campaign --name consensus-sweep \
  --run-dir runs/seed-0 --run-dir runs/seed-1 \
  --out sweep/
farm-notary verify --campaign sweep/
farm-notary paper-pack --campaign sweep/ --out sweep/appendix.md
farm-notary index --registry docs/registry.md --campaign sweep/
```

`paper-pack` writes the appendix snippet this audience puts in a PDF: CID,
content hash, Bitcoin attestation (or pending), publish allowlist, unmatched
count, precommit hash, and a reproducibility sentence scoped to OS / arch /
Python / BLAS. The index is a static directory — experiment, seed, CID, claim
level, date — not a scoreboard. See [docs/registry.md](docs/registry.md).

Optional lab identity (still no protocol token):

```bash
farm-notary sign --run-dir path/to/run --scheme ssh --key ~/.ssh/id_ed25519
```

Reviewers who know the key can attribute the publication; everyone else still
has OpenTimestamps. EAS remains experimental.

Derivation claims live in the experiment profile so statistics can recompute
exactly when a PNG is renderer-dependent:

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

## GitHub Action

Labs adopt this without reading DESIGN.md:

```yaml
- uses: dooders/FarmNotary@v0.2
  with:
    phase: notarize
    run-dir: results
    publish: "*.csv,REPORT.md"
    pin-remote: pinata
    backend: ots
```

Precommit on workflow start (`phase: precommit`), notarize + pin-remote on
success, upload `manifest.json` + `manifest.ots`, fail the job if verify
fails. Full contract: [docs/ACTION.md](docs/ACTION.md).

## Verifying someone else's claim

1. Fetch the CID (`ipfs get <cid>`), or obtain the run directory some other way. If `ipfs get` hangs, the CID may not be pinned on any reachable node — check `pin_service` (a local-only pin is not archival), `cid_reachable`, and `cid_reachable_checked_utc`, and try the public gateway: `https://ipfs.io/ipfs/<cid>`.
2. `farm-notary verify --run-dir <dir>`
3. Read the claim card. Each line is a CLAIMS.md claim (`pass` / `fail` /
   `pending` / Bitcoin height / `precommit bound` / `N/M` / `missing`).
   **Missing is not failure.** Exit code 0 means no attempted check failed;
   it does not mean the science is right, and it does not mean every claim
   was earned. The card always ends with `not claimed: scientific correctness`.

`verify` distinguishes two artifact-check failure modes (printed after the card):

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

0.2. Manifest, campaigns, derivation claims, environment fingerprints, optional identity, paper pack, public index, hashing, IPFS pinning, OpenTimestamps anchoring (dry-run default), proof upgrade, verification, a reusable GitHub Action, and EAS attestation on Base/Base Sepolia (experimental) are implemented and tested. The anchoring layer is deliberately outsourced — earlier revisions carried a custom `SimulationRegistry` contract, which was removed in favor of existing public infrastructure.
