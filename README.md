# FarmNotary

[![CI](https://github.com/Dooders/FarmNotary/actions/workflows/ci.yml/badge.svg)](https://github.com/Dooders/FarmNotary/actions/workflows/ci.yml)

Notary for [AgentFarm](https://github.com/Dooders/AgentFarm) runs. Simulation stays off-chain.

FarmNotary writes a `manifest.json` (config, code identity, artifact hashes), optionally pins the run directory to IPFS, and can anchor the manifest hash via [OpenTimestamps](https://opentimestamps.org/) — free calendar servers that batch digests into Bitcoin.

Immutability is not correctness. Re-run from the committed seed to check the science. The anchor only makes “this is the file we published” hard to walk back.

**This repo is 0.2.0.** PyPI still serves `0.1.0`. Install from git for the current line (see [Install](#install)).

## Why FarmNotary

Published runs need a record that does not depend on the author's laptop. Reviewers should be able to fetch the official artifacts, rehash them, and see when that exact file was published — without trusting a zip, a Drive folder, or a local IPFS pin.

That is a narrower job than "put the simulation on a chain" and a broader one than `sha256sum`. The simulation stays off-chain. FarmNotary never hashes/uploads paths containing `ballot`, `vote`, `voter`, `individual_choice`, or `private`. What gets notarized is the official record: code identity, config, aggregate metrics, winner allocations. Anchoring a hash is already solved ([OpenTimestamps](https://opentimestamps.org/) into Bitcoin); FarmNotary's work is the domain part — allowlists, privacy, honest claims.

A hash tool will tell you the bytes match. A research notary also tells you what you may claim, and what you may not.

## In and out of scope

**In**

- Canonical `manifest.json` (`farmnotary.manifest.v1`): recursive discovery, SHA-256 map, publish allowlist
- Campaign / sweep parent manifests (`farmnotary.campaign.v1`)
- Derivation claims (`notary.derived_from`) — executed only with `--verify-derived`
- Environment fingerprint (OS, arch, Python, `system` / `machine`, optional numpy/BLAS)
- Optional minisign / SSH identity on the content hash (no protocol token)
- Paper-pack appendix snippet and a static public index (no scores)
- Reusable GitHub Action in this repo (`dooders/FarmNotary`)
- Optional IPFS upload via the Kubo HTTP API (stdlib only)
- Anchoring via OpenTimestamps: no contract, no keys, no gas
- Verify: claim card + local rehash; OTS proof commits to the content hash

**Out**

- Running anchoring infrastructure (contracts, chains, servers)
- Running simulations inside the EVM
- Publishing individual voter or agent ballots
- Social scores, rankings, or reputation
- A public index that is anything other than a directory of published manifests

## Official record vs private choice

For a consensus / selection experiment:

- Voter (or agent) choice stays private
- What gets notarized is the official record: config, code hash, aggregate metrics, winner allocations

**Nothing is hashed, listed, or uploaded by default.** Prefer a named profile so labs do not invent globs (and forget `REPORT.md` or include a path they should not):

```bash
farm-notary manifest --run-dir path/to/run --profile consensus
```

Checked-in profiles: `consensus`, `rl-sweep`, `evolution-run`. Extra `--publish` globs append. The resolved allowlist is recorded as `publish_patterns` (and `publish_profile`) so the policy is part of the claim.

The denylist still applies: any path containing `ballot`, `vote`, `voter`, `individual_choice`, or `private` is excluded even if a pattern would admit it. Files that match no pattern are counted as `unmatched_count` (names are never printed).

## Install

Python ≥ 3.9. Core (manifest, verify, IPFS, dry-run) is stdlib-only.

```bash
# 0.2 line (this repo) — campaigns, profiles, claim cards, paper-pack
pip install "farm-notary[ots] @ git+https://github.com/Dooders/FarmNotary.git@dev"

# last PyPI release (0.1.0) — first-release CLI only
pip install "farm-notary[ots]"
```

Extras:

| Extra | Adds | Needed for |
|---|---|---|
| *(none)* | — | `manifest`, `verify` (local rehash), IPFS pin |
| `[ots]` | `opentimestamps` | `anchor --backend ots`, `upgrade`, OTS proof checks |
| `[chain]` | `web3` | experimental EAS backend |
| `[dev]` | pytest + both extras | development |

IPFS upload needs a reachable Kubo daemon (`FARM_NOTARY_IPFS_API`, default `http://127.0.0.1:5001`), not a Python extra.

## Quick start

```bash
# 1. Hash the run directory (profile or --publish is required)
farm-notary manifest --run-dir path/to/run --profile consensus --config config.json

# 2. Claim card: rehash artifacts. Derivation commands are not run.
farm-notary verify --run-dir path/to/run

# 3. Optional: stamp the content hash on public calendars
farm-notary anchor --run-dir path/to/run --backend ots
```

`anchor` and `precommit` default to `--backend dry-run` (print the payload, contact nothing). Pass `--backend ots` to submit. The GitHub Action defaults to `ots`.

Published path (durable pin + calendar stamp):

```bash
ipfs pin remote service add pinata https://api.pinata.cloud/psa <jwt-token>

farm-notary anchor --run-dir path/to/run --pin-remote pinata --backend ots
# hours later, once a calendar has batched the digest into Bitcoin:
farm-notary upgrade --run-dir path/to/run
farm-notary verify --run-dir path/to/run
```

`precommit` and `anchor` refuse a dirty git tree: the recorded SHA does not identify the code. `git_dirty` is still recorded. A supplied `--git-sha` is not a bypass — the working tree is inspected. Pass `--allow-dirty` (or `allow_dirty=True`) for an explicit exception.

Beacon-derived seeds (L2): commit how many seeds and a `min_round` on the plan, stamp it (`--backend ots`), then `derive-seeds`. The used round must be exactly `min_round`. A 3s drand chain plus a delayed stamp is a race; stamp immediately after `precommit`, then derive. Default `verify` does not fetch drand; pass `--live-beacon` (or `--beacon-fixture` in tests) so L2 can compare randomness over TLS. Threshold signatures are not checked. L2 is not scientific correctness.

```bash
farm-notary precommit --config plan.json --command "python run.py --seed {seed} --out {run_dir}" \
  --seed-count 8 --inclusion all_in_campaign --backend ots
farm-notary derive-seeds --precommit precommit.json --wait --live-beacon
farm-notary manifest --run-dir runs/i-0 --precommit precommit.json --seed-index 0 --profile consensus
farm-notary verify --run-dir runs/i-0 --live-beacon
```

`--beacon-fixture` (or `FARM_NOTARY_BEACON_FIXTURE`) is for tests and offline checks. A fetch failure leaves L2 unearned; it does not fail verify.

## Commands

| Command | What it does |
|---|---|
| `manifest` | Write `manifest.json` (requires `--profile` and/or `--publish`) |
| `check` | **Zero-install reviewer check**: claim level + anchor status from `manifest.json` only; no artifact rehash (`uvx farm-notary check --manifest …`) |
| `verify` | Print a CLAIMS.md claim card (ladder + rows); rehash; check proofs if present |
| `verify --verify-derived` | Also run `derived_from` commands (trusted manifests only) |
| `verify --campaign` | Check child hashes and the shared config hash (not a claim card) |
| `precommit` | Write `precommit.json` before the run (`dry-run` or `ots`); `--seed-count` adds a beacon seed plan |
| `anchor` | Optional pin + stamp (`dry-run` default; `ots`; `eas` experimental) |
| `upgrade` | Complete a pending `manifest.ots` with a Bitcoin attestation |
| `reproduce` | Re-run the recorded command; write `reproduction.json` (**executes the recorded shell command**; `--sign` is Sigstore on the receipt, not lab SSH) |
| `sign` | Attach a minisign or SSH signature of the content hash (not Sigstore) |
| `campaign` | Build a parent `campaign.json` from child run directories |
| `paper-pack` | Write `appendix.md` for a PDF |
| `index` | Append a run or campaign to a static registry (no scores) |
| `derive-seeds` | After the plan is stamped, fetch `min_round` and write `seeds.json` |
| `register-schema` | One-time EAS schema registration |
| `emit-interop` | Dual-write interop provenance files alongside `manifest.json` (SLSA/in-toto, RO-Crate, unsigned C2PA-style summary) |
| `archive` | Deposit a run to durable storage: Zenodo (DOI) and/or Software Heritage (SWH ID) |
| `chain` | Build or verify a multi-stage provenance chain (`provenance-chain.json`) |

## What you may claim

`farm-notary verify` prints a card. Reviewers read the card, not the exit code. **Missing is not failure.** Exit 0 means the artifact hashes match and every attempted proof check passed (including that an OTS proof, if present, commits to the content hash) — not that every claim was earned, not that `existed by time T` reached Bitcoin, and not that the science is right.

```
claim card
level: none — no earned ladder level
next:  L0 — these bytes existed by time T; Bitcoin headers not verified by this tool (missing: Bitcoin attestation)
•  tamper-evident record           — pass
•  existed by time T               — pending on public OpenTimestamps calendar: https://a.pool.opentimestamps.org (not yet Bitcoin-attested)
•  pre-specified design            — missing
•  bitwise reproducible (scoped)   — 6/6, ignored: *.mp4; byte-identical on x86-64 Linux in a pinned environment
•  not claimed: scientific correctness
```

The `level:` line is the highest earned reader-ladder step (L0–L3). Pending
OTS is not L0. The `existed by time T` row distinguishes Bitcoin-attested
proofs from pending public-calendar submissions and user-supplied calendars
that remain untrusted until Bitcoin. L0 means the proof commits to the content
hash and carries a Bitcoin-height attestation; this tool does not check
Bitcoin headers (`ots verify` does). L1 needs a recorded command, git SHA,
and environment fingerprint — not a completed re-run. L2 requires a
beacon-derived seed
after the plan is anchored (not scientific correctness). L3 needs a Sigstore
keyless signature on the reproduction receipt (`farm-notary reproduce --sign`).
That is **not** independently reproduced: identity is recorded when it can be
parsed, not proven distinct from the publisher. A receipt count is **not**
credibility. Prefer `COSIGN_IDENTITY_TOKEN` / `SIGSTORE_ID_TOKEN` or
`--identity-token @PATH` (never a raw JWT on argv). Cosign pin: `v2.5.3`.
See [docs/CLAIMS.md](docs/CLAIMS.md).

The only bitwise sentence the tool may emit today is *byte-identical on x86-64 Linux in a pinned environment*. Other hardware still reports `N/M` and refuses a cross-hardware claim. See [docs/CLAIMS.md](docs/CLAIMS.md).

Derivation is a separate, opt-in check:

```bash
farm-notary verify --run-dir path/to/run --verify-derived
```

Without the flag, rules stay on the manifest and the card still exits 0. The CLI prints a note; it does not execute commands from a downloaded manifest. Only pass `--verify-derived` for manifests you trust.

## Pinning

**`--pin-remote` is the published path.** Local Kubo (`--pin`) is a lab convenience and always warns.

The CID is content-addressed, but the bytes are only reachable while some node holds them. Do not cite a local-only pin. `--pin-remote <service>` uploads via Kubo, then calls a registered [IPFS Pinning Service](https://ipfs.github.io/pinning-services-api-spec/) (Pinata, web3.storage, …). `--pin-remote` implies `--pin`. The manifest records `pin_service` (`"local"` or the service name).

After each pin, FarmNotary checks `https://ipfs.io/ipfs/<cid>` and records `cid_reachable` plus a UTC timestamp. Pass `--no-check-gateway` in CI (the Action does this by default).

## Anchoring

- **CLI default:** `dry-run` — no network.
- **Recommended live backend:** `--backend ots` — keyless, no gas, Bitcoin-backed.
- **Experimental:** `--backend eas` — funded key, gas on Base, attester-address trust. See [docs/EAS.md](docs/EAS.md).

`--backend ots` submits the *content hash* (SHA-256 of the canonical body, excluding stamp fields `cid`, `cid_reachable`, `pin_service`, `anchor`, `identity`) to calendars (`--calendar` or `FARM_NOTARY_CALENDARS`; default: public pools). The proof is `manifest.ots`. `verify` reports whether that proof is Bitcoin-attested, pending on known public pools, or pending only on user-supplied calendars. Stamping the manifest afterwards does not invalidate it.

`upgrade` asks calendars for a Bitcoin attestation (typically hours). Exit 1 means still pending. Full header checks against your own node are left to standard `ots verify` tooling.

## Reproducing a run

Record the command with `{run_dir}` marking the output directory:

```bash
farm-notary manifest --run-dir path/to/run --profile consensus \
  --command "python run_experiment.py --seed 0 --out {run_dir}" \
  --lockfile requirements.lock
farm-notary reproduce --run-dir path/to/run --cwd path/to/experiment --ignore '*.mp4' --anchor
```

`reproduce` executes the manifest's recorded command via the shell. Treat a
downloaded manifest as untrusted code execution — remote code execution by
design — not as independent verification.

By default, FarmNotary refuses to run that command unless the manifest matches
the same local checkout (`git_sha`) or the same GitHub Actions repo/SHA the
operator is already trusting. To override, pass
`--i-accept-untrusted-command` and do the run inside your own sandbox
(container or VM, no network).

`reproduce` re-runs into a fresh directory, compares every listed artifact, and writes `reproduction.json`. A byte-diff is classified (`embedded_absolute_path`, `timestamp`, `float_print_format`, `video_encoder`) and labeled **not a science failure** when that is what it is. `--cwd` is the experiment repo when it is not the run directory. `--anchor` timestamps the receipt (`reproduction.ots`).

`verify` then reports **bitwise reproducible (scoped)** — `N/M` of compared artifacts, any `--ignore` globs, and the scoped sentence above.

## Campaigns, paper pack, and the public index

```bash
farm-notary campaign --name consensus-sweep \
  --run-dir runs/seed-0 --run-dir runs/seed-1 \
  --out sweep/
farm-notary verify --campaign sweep/
farm-notary paper-pack --campaign sweep/ --out sweep/appendix.md
farm-notary index --registry docs/registry.md --campaign sweep/
```

`paper-pack` writes the appendix snippet: CID, content hash, Bitcoin attestation (or pending), allowlist, unmatched count, precommit hash, artifact label, reader-ladder placeholder (`—`), environment, scoped sentence. It does not cite `Ln`. Campaigns also list child seeds and CIDs.

`index` maintains a static directory (Markdown + `registry.json` sidecar): experiment, seed, CID, claim level, date. Not a scoreboard. Running `index` rewrites the Markdown table; how-to text in that file is not preserved. See [docs/registry.md](docs/registry.md).

Optional lab identity on the **manifest content hash** (still no protocol token).
This is not `reproduce --sign` (Sigstore on the receipt):

```bash
farm-notary sign --run-dir path/to/run --scheme ssh --key ~/.ssh/id_ed25519
```

Derivation rules live in the experiment profile so statistics can recompute when a PNG is renderer-dependent:

```json
{
  "notary": {
    "profile": "consensus",
    "publish": ["extra_table.csv"],
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

`mode: recompute` (default) copies sources to a temp directory, runs `command`, and byte-compares outputs. `mode: verify` runs a check that exits 0 when derived artifacts match.

## GitHub Action

The action lives at the repo root (`action.yml`). There is no `v0.2` tag yet; pin `dev` or a commit SHA. Last release tag is `v0.1.0`.

```yaml
- uses: dooders/FarmNotary@dev
  with:
    phase: notarize
    run-dir: results
    profile: consensus
    pin-remote: pinata
    backend: ots
```

`phase: precommit` at job start, `phase: notarize` after success (binds `precommit.json` when present, anchors, runs `verify`, uploads `manifest.json` + `manifest.ots`). `phase: all` is notarize-only for an already-finished run. Verify failure fails the job; artifacts are still uploaded. The Action does not pass `--verify-derived`. Full contract: [docs/ACTION.md](docs/ACTION.md).

### CI provenance

When `farm-notary manifest` runs inside GitHub Actions, the tool automatically reads `GITHUB_SHA`, `GITHUB_REPOSITORY`, `GITHUB_REF`, `GITHUB_WORKFLOW`, and `GITHUB_RUN_ID` and records them in a `ci_provenance` block in the manifest:

```json
"ci_provenance": {
  "kind": "github_actions",
  "sha": "<GITHUB_SHA>",
  "repository": "Dooders/FarmNotary",
  "ref": "refs/heads/main",
  "workflow": "CI",
  "run_id": "123456",
  "run_url": "https://github.com/Dooders/FarmNotary/actions/runs/123456"
}
```

`ci_provenance` is included in the content hash so the anchor commits to both the artifacts and the attested CI context.  `verify` checks that `git_sha` agrees with `ci_provenance.sha`; a disagreement is a hard failure.  Local / developer runs that have no `ci_provenance` remain valid at a lower claim level — the field is optional and additive.

**Documented path** (checkout → run → notarize with provenance attached):

```yaml
jobs:
  notarize:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4        # sets GITHUB_SHA
      - run: python run.py --out results  # produce artifacts
      - uses: dooders/FarmNotary@dev
        with:
          phase: notarize
          run-dir: results
          profile: consensus
          backend: ots
          # No git-sha override needed: farm-notary reads GITHUB_SHA
          # automatically and cross-checks it against git rev-parse HEAD.
```

The `identity` field (minisign / SSH optional signature) labels local signatures as "holder of key K asserts this" — it is never interpreted as CI provenance.

## Python API

AgentFarm should depend on a 0.2 pin (`farm-notary>=0.2,<0.3`) via an extra such as `farm[notary]`:

```python
from farm_notary import notarize_run
from farm_notary.ots import OpenTimestampsBackend

manifest, receipt = notarize_run(
    run_dir,
    git_sha=sha,
    runner="consensus_paradigms",
    config=config,
    publish_profile="consensus",
    backend=OpenTimestampsBackend(),
    pin_remote="pinata",
)
```

`notarize_run` is dry-run until a backend is passed. `pin_remote=` is the published pin path; `pin=True` is local Kubo only. Do not submodule unless the API is still thrashing.

## Schema stability

Every manifest records `farm_notary_version` (currently `0.2.0`) and `schema` (`farmnotary.manifest.v1`).

**Promise:** within the `0.x` line, schema changes are **minor-version bumps**. `verify` stays backward-compatible with older manifests: new fields are ignored when reading an older body. A newer schema emits a warning and still attempts verification. Optional fields (`derived_from`, `identity`, `ci_provenance`, `publish_profile`, …) are omitted when empty so a v1 body keeps a stable content hash.

Required on every v1 body: `schema`, `created_utc`, `git_sha`, `config`, `artifacts`, `artifact_hashes`, `publish_patterns`, `unmatched_count`.

Breaking changes (new required fields, renamed keys, removed fields) are reserved for `1.0` and will land in the changelog with a migration guide.

## Verifying someone else's claim

**Quick check (zero-install):** No venv, no extras, no account. You only need
`manifest.json` (and `manifest.ots` if you want the anchor checked).

```bash
uvx farm-notary check --manifest path/to/manifest.json
# or: pipx run farm-notary check --manifest path/to/manifest.json
```

Output:

```
content_hash: a1b2c3…
cid:          bafybeig…
claim_level:  bytes
anchor:       Bitcoin height 840000
```

Add `[ots]` to verify the OTS proof commits to the content hash:

```bash
uvx "farm-notary[ots]" check --manifest path/to/manifest.json
```

See [docs/VERIFIER.md](docs/VERIFIER.md) for the full reviewer guide.

**Full verify (artifact rehash):**

1. Fetch the CID (`ipfs get <cid>`), or obtain the run directory another way. If `ipfs get` hangs, check `pin_service` (local-only is not archival), `cid_reachable`, and try `https://ipfs.io/ipfs/<cid>`.
2. `uvx "farm-notary[ots]" verify --run-dir <dir>`
3. Read the claim card. **Missing is not failure.** The card always ends with `not claimed: scientific correctness`.
4. Only if you trust the recorded commands: `farm-notary verify --run-dir <dir> --verify-derived`

`verify` distinguishes:

- **`artifact unreachable: <name>`** — file absent; CID hint appended when known
- **`artifact hash mismatch: <name>`** — present but different bytes

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

CI runs pytest on Python 3.9–3.12.

## Documentation

| Doc | Contents |
|---|---|
| [docs/PRINCIPLES.md](docs/PRINCIPLES.md) | Constraints that settle design arguments |
| [docs/CLAIMS.md](docs/CLAIMS.md) | What each claim means, what backs it, hardware scope |
| [docs/DESIGN.md](docs/DESIGN.md) | Schema, backends, privacy, provenance flow |
| [docs/ACTION.md](docs/ACTION.md) | GitHub Action inputs, outputs, phases |
| [docs/EAS.md](docs/EAS.md) | Experimental EAS backend |
| [docs/VERIFIER.md](docs/VERIFIER.md) | Reviewer quick-check: zero-install `uvx`/`pipx run` path |
| [docs/registry.md](docs/registry.md) | Generated public index (no scores) |
| [docs/demo/](docs/demo/) | Live notebook: claim card, allowlist, scoped re-run |
| [docs/slides/](docs/slides/) | Intro deck ([PDF](docs/slides/farmnotary.pdf)) and consensus walkthrough ([PDF](docs/slides/consensus.pdf)) |
| [CHANGELOG.md](CHANGELOG.md) | Version history |
| [integration/agentfarm/README.md](integration/agentfarm/README.md) | AgentFarm provenance patch |

## Status

0.2. Manifests, campaigns, derivation claims, environment fingerprints, optional identity, paper pack, public index, hashing, IPFS pinning, OpenTimestamps (CLI dry-run default; Action `ots`), proof upgrade, claim-card verify, reusable GitHub Action, and experimental EAS on Base / Base Sepolia are implemented and tested.

The last tagged / PyPI release is **0.1.0**. This tree is **0.2.0**. The anchoring layer is outsourced — earlier revisions carried a custom `SimulationRegistry` contract, which was removed.
