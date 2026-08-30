# FarmNotary design

Constraints that settle design arguments live in [PRINCIPLES.md](PRINCIPLES.md).

## Problem

AgentFarm runs need an immutable *official* record: what code, what config, what aggregate outputs. That record should be checkable without trusting a laptop folder. The simulation itself must stay off-chain.

## Record vs execution

```
AgentFarm runner
    -> writes artifacts (metrics, summary, official allocations)
FarmNotary
    -> hashes artifacts, writes manifest.json
    -> optional: pin directory, get CID
    -> optional: anchor manifest hash via OpenTimestamps (manifest.ots)
Verifier
    -> fetch CID, rehash, check the proof commits to the manifest hash
    -> separately: re-execute from seed to test claims
```

The anchor does not know if the science is right. It knows which bytes were published, and when.

## Outsourced anchoring

FarmNotary runs **no anchoring infrastructure of its own**. Earlier revisions carried a custom `SimulationRegistry` Solidity contract plus an Ethereum client; that layer was removed. Anchoring a hash publicly is a solved problem.

The recommended backend is [OpenTimestamps](https://opentimestamps.org/): public calendar servers aggregate submitted digests into Merkle trees and commit the roots into Bitcoin. Submitting is free and keyless; the proof is an ordinary `.ots` file that anyone can verify against Bitcoin headers, with or without FarmNotary.

An experimental `eas` backend attests `(manifestHash, cid)` on Base / Base Sepolia. It needs a funded key, costs gas, and ties verification to an attester address. See [EAS.md](EAS.md).

FarmNotary's job is domain-specific: deciding *what* gets anchored (the canonical manifest content hash) and *what never leaves the machine* (private artifacts).

CLI `anchor` and `precommit` default to `dry-run` (no network). The GitHub Action defaults to `ots`.

## Manifest v1

See `farm_notary/schema.py` and `farm_notary/manifest.py`.

**Required:** `schema` (`farmnotary.manifest.v1`), `created_utc`, `git_sha`, `config`, `artifacts`, `artifact_hashes`, `publish_patterns`, `unmatched_count`.

**Usually present:** `farm_notary_version` (`0.2.0`), `git_dirty`, `runner`, `command`, `environment`, `official_record`.

**Optional (omitted when empty so older bodies keep a stable hash):** `derived_from`, `publish_profile`, `precommit_hash`, `beacon`, `identity`.

**Stamp fields** (written after the content hash; excluded from it): `cid`, `cid_reachable`, `cid_reachable_checked_utc`, `pin_service`, `anchor`, `identity`.

Artifact discovery is recursive; paths are POSIX-style relative to the run directory. Hidden files and notary metadata (`manifest.json`, `reproduction.json`, `precommit.json`, `campaign.json`, `appendix.md`, `*.ots`) are skipped. `Manifest.validate()` enforces the schema id, artifact list / hash map agreement, and the privacy filter.

`content_hash` is SHA-256 of the canonical JSON body (sorted keys, no whitespace) after stripping stamp fields. You can pin, stamp, and sign without circular hashing.

## Campaign / sweep manifests

A parent record (`campaign.json`, schema `farmnotary.campaign.v1`) lists child run CIDs, seeds, and a seed-excluded config hash. When children share a precommit `seed_plan`, the parent copies it and child entries may record `seed_index`. A reviewer of a paper figure (100 trials, seed 0…N) verifies the parent instead of one folder.

```
farm-notary campaign --name consensus-sweep \
  --run-dir runs/seed-0 --run-dir runs/seed-1 \
  --out sweep/
farm-notary verify --campaign sweep/
```

Each child entry records `seed`, `content_hash`, `config_hash` (config minus `seed` / `rng_seed` / `random_seed`), optional `cid`, `claim_level`, and a relative `path` when resolvable. The parent `config_hash` is set only when every child shares it.

`verify --campaign` is a structural check (shared config hash, child content hashes, optional local rehash). It does **not** print a single-run claim card. Pass `--require-local` to fail when a child directory is absent. When children share a `seed_plan`, verify prints how many committed indices were published and lists missing members. Publishing a subset does not fail the check.

## Beacon-derived seeds (L2)

`precommit --seed-count N --inclusion {all_in_campaign|primary_endpoint|other:…}` records a `seed_plan` (drand `chain_hash`, `genesis_time`, `period`, `min_round = latest + delay_rounds`, derivation `sha256-v1`). The plan config must not contain a concrete seed. `derive-seeds` refuses until `precommit.ots` exists and commits to the plan, then fetches **exactly** `min_round` and writes `seeds.json`. Each run manifest records a `beacon` block (`round`, `randomness`, `seed_index`, `derived_seed`) inside the content hash.

`seed_i = uint64_be(SHA256(canonical_json({chain_hash, round, index, config_hash}) || randomness)[:8])` where `config_hash` excludes seed keys. `verify` recomputes that function. Randomness is compared to a `BeaconClient` only when one is supplied (`--beacon-fixture`, `--live-beacon`, or `--beacon-url`); the HTTP client uses the plan's `chain_hash`. That comparison is TLS to the drand REST API, not a BLS signature check. L2 also requires L0, L1, a bound plan, a passing `precommit.ots`, and `created_utc` not after the round's scheduled unix time (the plan JSON's clock, not a calendar time). Calendar attestation on the plan is enough for the proof check; Bitcoin on the plan is not required. Bitcoin on the **run** remains L0. A failed or skipped beacon fetch leaves L2 unearned; it does not fail verify.

## Derivation claims

Byte-identity of a PNG is a renderer claim. Reviewers usually care that summary statistics recompute from raw trials. The experiment profile declares that:

```json
{
  "notary": {
    "profile": "consensus",
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

`mode: recompute` (default) copies sources to a temp directory, runs `command`, and byte-compares outputs. `mode: verify` runs a check that exits 0 when derived artifacts match (the AgentFarm `verify-report` pattern).

`farm-notary verify` does **not** execute those commands. Pass `--verify-derived` only for manifests you trust (commands run via the shell). Without the flag, rules stay on the record, verify exits 0, and the CLI notes that derivation was not run — missing is not failure. With the flag, a failing rule fails verify; on success the CLI may print `claim: statistics recompute exactly from recorded sources` even when a figure is renderer-dependent.

## Environment fingerprint

`environment` is first-class, not just a lockfile hash:

- `os`, `arch`, `python`, `python_implementation`
- `system`, `machine` — used by the claim card to decide whether it may emit the scoped sentence in [CLAIMS.md](CLAIMS.md)
- `platform` — `platform.platform()` string (legacy parse fallback)
- `packages_hash` / `package_count`
- optional `lockfile` + `lockfile_sha256`
- optional `numpy` (`version`, `blas`, `blas_version`, `blas_config`, `lapack`, `numpy_host_cpu`) when numpy is installed

This is how “bitwise on x86-64 Linux, pinned env” stays honest when someone reproduces on Apple Silicon and gets a 1-ulp diff. The paper-pack sentence names the machine class.

## Optional identity

`farm-notary sign --scheme ssh|minisign --key PATH` signs the content hash and records `{scheme, public_key, signature, principal}` on the manifest. Reviewers who know the lab’s key get “this lab published this”; everyone else still has OTS. No protocol token. EAS remains experimental and is not used here. `ssh-keygen` / `minisign` must be on `PATH`.

## Paper pack

`farm-notary paper-pack` writes `appendix.md`: CID, content hash, Bitcoin attestation (or pending / experimental EAS), publish allowlist, unmatched count, precommit hash, artifact label (`bytes` / `bitwise` / …), environment, and a scoped reproducibility sentence. `Reader ladder` is `—`: do not cite `Ln` from the appendix (FarmNotary does not verify Bitcoin headers; a campaign has no single-run ladder). Campaigns also list child seeds and CIDs. Pass `--verify-derived` to confirm statistics before the sentence claims they recompute.

## Public index

`farm-notary index --registry PATH` maintains a static directory (Markdown + `registry.json` sidecar, schema `farmnotary.registry.v1`) of published manifests: experiment name, seed, CID, claim level, date. It is a directory, not a chain, and it never writes scores or rankings. The generator **replaces** the Markdown file; do not keep how-to text in the registry path. See [registry.md](registry.md).

Claim levels (`farm_notary.claims`) are labels, never scores: `bytes`, `derived_declared`, `bitwise` / `bitwise_declared`, `bitwise+derived` / `bitwise+derived_declared`. A `_declared` suffix means the artefact exists but has not been validated against this record. They are not the L0–L3 reader ladder printed by `verify`.

## Reusable GitHub Action

`dooders/FarmNotary` (action name `farm-notary-action`): precommit on workflow start, notarize + optional pin-remote on success, upload `manifest.json` + `manifest.ots`, fail the job if verify fails. Pin `@dev` or a commit until `v0.2.0` is tagged. See [ACTION.md](ACTION.md).

## Privacy

Allowlist-first: nothing is hashed unless it matches a declared publish pattern. Named experiment-type profiles (`consensus`, `rl-sweep`, `evolution-run` in `farm_notary/profiles.py`) are the checked-in official artifact lists; `--publish` and `notary.publish` append extras. Resolution order: named profile (`--profile` / `publish_profile`, else `notary.profile`), then `notary.publish`, then `--publish`. The resolved allowlist is recorded as `publish_patterns` (and `publish_profile` when a profile was used).

If no source supplies patterns, `build_manifest()` raises `ValueError`.

Relative paths containing `ballot`, `vote`, `voter`, `individual_choice`, or `private` (any path component) are skipped by discovery, rejected by validation, and never uploaded — including after a profile or `--publish` glob would otherwise admit them. Do not put agent-level or citizen-level choices in `official_record`.

Simple `*.ext` patterns match files in subdirectories (filename fallback), not only the run-dir root.

## AgentFarm hook

After `ExperimentRunner` (or a dedicated consensus runner) flushes a run directory:

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

Without a backend, `notarize_run` is dry-run. A dirty tree is refused unless `allow_dirty=True`.

`notarize_run` builds and writes the manifest, optionally pins the directory (manifest included), anchors, persists any proof file, and rewrites `manifest.json` with the CID and anchor receipt. Pinning inside `notarize_run` does not run the public-gateway check (that lives on the CLI `anchor` path).

## IPFS

`farm_notary.ipfs.IpfsClient` posts a multipart upload to a Kubo daemon's `/api/v0/add` with `wrap-with-directory=true&cid-version=1&pin=true` and takes the wrapping directory's CID as the run's content address. Standard library only; the endpoint comes from `FARM_NOTARY_IPFS_API` (default `http://127.0.0.1:5001`).

Local Kubo is a lab convenience, not archival. The published path is `--pin-remote`, which then calls Kubo's `/api/v0/pin/remote/add` for a registered pinning service (Pinata, web3.storage, or any Pinning Service API). The manifest records `pin_service` (`"local"` or the service name).

The pinned tree includes `manifest.json`. Because `content_hash` excludes stamp fields, the copy inside the pinned tree hashes to the same value that gets anchored, even though the local copy is later stamped.

## Anchoring flow

`farm_notary.ots` (requires `farm-notary[ots]`):

1. **Stamp** (`anchor --backend ots`): submit the manifest content hash to the configured calendars (`FARM_NOTARY_CALENDARS` or the public pools), merge their responses, and write the proof to `manifest.ots`. The proof commits to the *content hash digest directly* — no privacy nonce, because the manifest hash is meant to be public.
2. **Upgrade** (`upgrade`): calendars batch digests into Bitcoin on their own schedule (typically hours). The upgrade command asks each pending calendar for the completed path to a Bitcoin block header attestation and rewrites the proof. Exit code 1 means still pending; run it again later.
3. **Verify** (`verify`): rehash artifacts, recompute the content hash, check the proof commits to it, and print a CLAIMS.md claim card — stacked ladder (`none` / L0–L3), tamper-evident record, existed by time T (Bitcoin height, pending on known public pools, or pending only on user-supplied calendars), pre-specified design, bitwise reproducible (scoped), and an explicit non-claim of scientific correctness. Exit 0 means the attempted checks passed; for OTS it means the proof commits to the content hash, not that Bitcoin attestation exists. Only a Bitcoin-height attestation earns L0; pending calendars do not. L0 means these bytes existed by time T; Bitcoin headers not verified by this tool (commitment plus attestation type). Checking the Bitcoin merkle path against a local node is left to `ots verify`. L1 requires recorded `command`, `git_sha`, and environment fingerprint; `verify` does not run that command. L2 requires a beacon-derived seed after the plan is anchored (`seed_plan` + bound `precommit.ots` + `created_utc` not after the round + exact `min_round` + recomputed seed + HTTP or fixture randomness match). Default `verify` does not contact drand; pass `--live-beacon` or `--beacon-url`. A failed beacon fetch leaves L2 unearned; it does not fail verify. L3 requires a Sigstore keyless signature on a reproduction receipt whose full verification (manifest hash match and all receipt checks) passes; the receipt must have been signed with `farm-notary reproduce --sign` (`cosign` `v2.5.3` on PATH). L3 is **not** independently reproduced: identity is not constrained. Inspect `sigstore identity:` / `sigstore issuer:` when present; a missing note means the cert could not be parsed.

Because `manifest.ots` commits to the content hash rather than the raw file bytes, stamping `manifest.json` with `cid`/`anchor` after anchoring never invalidates the proof.

## Provenance and reproduction

The manifest records everything a stranger needs to re-derive the run: `command` (with a `{run_dir}` placeholder), `config`, `git_sha` plus a `git_dirty` flag (a dirty tree means the sha does not identify the code that ran, so it is recorded, not hidden — and `precommit` / `anchor` refuse it unless `--allow-dirty`. Omitting `git_dirty` still inspects the working tree; a supplied SHA is not a bypass), and `environment` as above. `system` + `machine` are what the claim card uses to decide whether it may emit the scoped sentence in [CLAIMS.md](CLAIMS.md) (`byte-identical on x86-64 Linux in a pinned environment`). Other hardware is reported, not claimed.

`farm-notary reproduce` turns reproducibility from a claim into a procedure: re-run the recorded command into a fresh directory (`--cwd` for the experiment repo), rehash, byte-compare against the manifest. A mismatch is classified (`embedded_absolute_path`, `timestamp`, `float_print_format`, `video_encoder`) so a packaging bug is not read as a failed result — the consensus `{run_dir}` fix (7/7 → 8/8) is the type specimen. Known-nondeterministic artifacts (videos, databases) are excluded per-run with `--ignore` globs and recorded as excluded — the claim is scoped, never blanket. A successful reproduction writes `reproduction.json` (rerunner environment, per-file results, diagnostics, the original manifest hash) whose own hash can be anchored via OpenTimestamps (`reproduction.ots`). Passing `--sign` to `farm-notary reproduce` signs the receipt with Sigstore keyless signing (`cosign sign-blob`) and embeds the bundle in the receipt; tokens are passed via `SIGSTORE_ID_TOKEN` (never argv). `farm-notary verify` then verifies the bundle with `cosign verify-blob` (`--offline` only when the bundle has a Rekor inclusion proof) and, when full verification passes (no receipt problems), awards L3 as a signed receipt, not as independence. Install identity-extraction extras with `pip install farm-notary[sigstore]`.

Notary metadata (`manifest.json`, `reproduction.json`, `campaign.json`, `appendix.md`, `precommit.json`, `*.ots`) is never treated as an artifact: discovery skips it, so stamping and receipts don't perturb the anchored content hash.

## Backends

1. **Dry-run** (CLI default) — returns the payload that would be submitted
2. **`ots`** — OpenTimestamps calendars into Bitcoin (`farm_notary/ots.py`, extra `[ots]`). Action default.
3. **`eas`** — EAS attestation on Base / Base Sepolia (`farm_notary/eas.py`, extra `[chain]`, experimental). EAS is an OP-stack predeploy (`0x4200...0021`; SchemaRegistry `0x4200...0020`), so the addresses are identical on Base and Base Sepolia. Schema: `bytes32 manifestHash,string cid`, non-revocable. `web3` is a lazy import. `anchor_run` writes the receipt (backend, tx hash, attestation UID, chain id) into `manifest.anchor`. `content_hash` excludes stamp fields, so the attested hash is stable across stamping.

`precommit --backend` accepts only `dry-run` and `ots` (not `eas`).

## Consensus experiment note

A companion experiment (individual vs party vs score vs latent_match allocation) can live in AgentFarm and use FarmNotary only at the end of a sweep. FarmNotary is not the experiment. See [../integration/agentfarm/README.md](../integration/agentfarm/README.md).
