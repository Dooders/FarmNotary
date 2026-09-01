# Reusable GitHub Action (`farm-notary-action`)

Other labs adopt FarmNotary without reading DESIGN.md: drop this action into a
workflow. It precommits at job start, notarizes and optionally pins on
success, uploads `manifest.json` + `manifest.ots`, and **fails the job if
verify fails**.

This repository hosts the action at the root (`action.yml`). Consume it as:

```yaml
uses: dooders/FarmNotary@v1.0.0
```

Pin `v1.0.0` (or a commit SHA). `@dev` tracks unreleased work on the
development branch.

The composite action's `name` field is `farm-notary-action`.

## Two-step flow (recommended)

```yaml
name: Notarize experiment
on: [push]
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Pre-specify the run
        uses: dooders/FarmNotary@v1.0.0
        with:
          phase: precommit
          run-dir: results
          config: experiments/consensus/config.json
          command: "python run_experiment.py --seed 0 --out {run_dir}"
          lockfile: requirements.lock

      - name: Run the experiment
        run: python run_experiment.py --seed 0 --out results

      - name: Notarize, pin, verify
        uses: dooders/FarmNotary@v1.0.0
        with:
          phase: notarize
          run-dir: results
          config: experiments/consensus/config.json
          command: "python run_experiment.py --seed 0 --out {run_dir}"
          profile: consensus
          lockfile: requirements.lock
          pin-remote: pinata
          backend: ots
```

`phase: notarize` binds `results/precommit.json` when it is present, writes
the manifest, anchors (`ots` by default — unlike the CLI, which defaults to
`dry-run`), runs `farm-notary verify` **without** `--verify-derived`, and
uploads the notary files. A verify failure fails the job; artifacts are still
uploaded so a reviewer can inspect the mismatch.

To earn the derivation claim in CI, add a follow-up step after notarize
(`farm-notary verify --run-dir results --verify-derived`) only when the
recorded commands are yours.

## Already-finished run

```yaml
- uses: dooders/FarmNotary@v1.0.0
  with:
    phase: all
    run-dir: results
    profile: consensus
    backend: dry-run
```

`phase: all` is notarize-only: use it when the experiment has already written
artifacts. It does not invent a precommit after the fact.

## Inputs

| Input | Default | Purpose |
|---|---|---|
| `phase` | `notarize` | `precommit`, `notarize`, or `all` |
| `run-dir` | required | Artifact directory |
| `config` | | Experiment profile JSON (`notary.profile`, `notary.publish`, `notary.derived_from`) |
| `command` | | Recorded command with `{run_dir}` |
| `profile` | | Named allowlist: `consensus`, `rl-sweep`, or `evolution-run` |
| `publish` | | Comma-separated extra allowlist globs |
| `lockfile` | | Hashed into the environment fingerprint |
| `git-sha` | current HEAD | Code identity |
| `runner` | | Runner name recorded on the manifest |
| `backend` | `ots` | `ots` (recommended) or `dry-run`. Precommit accepts `ots` / `dry-run` only |
| `pin-remote` | | Kubo remote pin service; implies pin |
| `pin` | `false` | Pin to a local Kubo daemon |
| `no-check-gateway` | `true` | Skip the public IPFS gateway check (typical in CI) |
| `identity-key` | | Optional minisign / SSH key; still no protocol token |
| `identity-scheme` | `ssh` | `ssh` or `minisign` |
| `identity-principal` | | Label recorded with the optional identity signature |
| `sign-receipt` | `false` | After notarize, `farm-notary reproduce --sign`. Caller must set `permissions.id-token: write`. Token never on argv. Cosign pin `v2.5.3`. Not independently reproduced |
| `reproduce-cwd` | | Working directory for `reproduce --sign` |
| `artifact-name` | `farm-notary` | Uploaded Actions artifact name |
| `allow-dirty` | `false` | Allow a dirty git working tree. The recorded SHA will not identify the exact code that ran. Use only in smoke tests or when the tree is intentionally unclean |

`manifest` still requires a profile, `publish` globs, or `notary.profile` /
`notary.publish` in the config. Prefer `profile`.

Beacon `seed_plan` / `derive-seeds` / `--seed-index` are CLI steps around
the Action's `precommit` and `notarize` phases. The Action does not fetch
drand, pass `--seed-count`, or `--live-beacon`. The default Action job
does not earn L2. Run those locally (or add a job step) if the campaign
should earn L2.

Pinning needs a reachable Kubo daemon (`FARM_NOTARY_IPFS_API`) with the remote
service already registered. OpenTimestamps needs outbound HTTPS to the public
calendars. Use `backend: dry-run` in jobs that must not leave the runner.

The action installs `${{ github.action_path }}[ots]` (the checkout of this
repo, so the Action always matches the pinned tag).

To attach a Sigstore signature to a reproduction receipt in CI (L3 evidence,
not independence), set `sign-receipt: true` and grant OIDC:

```yaml
permissions:
  contents: read
  id-token: write
```

Cosign is installed at `v2.5.3`. The identity token is never passed on argv.

## Outputs

- `content-hash` — canonical manifest (or campaign) hash; excludes CID, anchor, identity
- `cid` — set when pinning succeeded
- `precommit-hash` — set on the precommit phase, or when a precommit was bound

## AgentFarm integration example (copy-pasteable)

This is the minimal workflow to drop into any repo that runs AgentFarm
consensus experiments. Change `run-dir`, `config`, `command`, and
`lockfile` to match your repository layout; keep everything else as-is.

```yaml
# .github/workflows/notarize.yml
name: Notarize AgentFarm run
on:
  push:
    branches: [main]

permissions:
  contents: read

# Pin the released tag. @dev is a mutable ref.
# e.g.  uses: dooders/FarmNotary@v1.0.0

jobs:
  notarize:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4         # sets GITHUB_SHA automatically

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: |
          pip install --requirement requirements.lock
          pip install -e .

      # Optional: pre-specify the run before it executes
      - name: Pre-specify (precommit)
        uses: dooders/FarmNotary@v1.0.0
        with:
          phase: precommit
          run-dir: results
          config: experiments/consensus/config.json
          command: "python run_experiment.py --seed 0 --out {run_dir}"
          lockfile: requirements.lock

      # Your experiment goes here
      - name: Run experiment
        run: python run_experiment.py --seed 0 --out results

      # Notarize, anchor via OpenTimestamps, run verify, upload manifest
      - name: Notarize + verify
        uses: dooders/FarmNotary@v1.0.0
        with:
          phase: notarize
          run-dir: results
          config: experiments/consensus/config.json
          command: "python run_experiment.py --seed 0 --out {run_dir}"
          profile: consensus
          lockfile: requirements.lock
          backend: ots
          # pin-remote: pinata  # uncomment when a Kubo remote is registered
```

`GITHUB_SHA` is automatically picked up and recorded in `ci_provenance`;
no `git-sha` override is needed. The precommit step is optional — omit it
and set `phase: all` when the run has already completed.

## What this action will not do

- Score, rank, or reputation-weight a lab
- Run EAS unless you set `backend: eas` (experimental, keyed, gas)
- Execute `derived_from` commands (`--verify-derived` is not passed)
- Claim the science is correct — only that these bytes were specified,
  produced, and (if verify passed) match the recorded hashes
