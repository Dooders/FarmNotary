# Reusable GitHub Action (`farm-notary-action`)

Other labs adopt FarmNotary without reading DESIGN.md: drop this action into a
workflow. It precommits at job start, notarizes and optionally pins on
success, uploads `manifest.json` + `manifest.ots`, and **fails the job if
verify fails**.

This repository hosts the action at the root (`action.yml`). Consume it as:

```yaml
uses: dooders/FarmNotary@dev
```

The last tagged release is `v0.1.0`. The 0.2 line (profiles, claim-card verify,
campaigns) lives on `dev`. Pin that branch or a commit SHA until `v0.2.0` is
tagged. `uses: dooders/FarmNotary@v0.2` will 404 until that tag exists.

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
        uses: dooders/FarmNotary@dev
        with:
          phase: precommit
          run-dir: results
          config: experiments/consensus/config.json
          command: "python run_experiment.py --seed 0 --out {run_dir}"
          lockfile: requirements.lock

      - name: Run the experiment
        run: python run_experiment.py --seed 0 --out results

      - name: Notarize, pin, verify
        uses: dooders/FarmNotary@dev
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
- uses: dooders/FarmNotary@dev
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
| `artifact-name` | `farm-notary` | Uploaded Actions artifact name |

`manifest` still requires a profile, `publish` globs, or `notary.profile` /
`notary.publish` in the config. Prefer `profile`.

Pinning needs a reachable Kubo daemon (`FARM_NOTARY_IPFS_API`) with the remote
service already registered. OpenTimestamps needs outbound HTTPS to the public
calendars. Use `backend: dry-run` in jobs that must not leave the runner.

The action installs `${{ github.action_path }}[ots]` (the checkout of this
repo, not the PyPI 0.1.0 wheel).

## Outputs

- `content-hash` — canonical manifest (or campaign) hash; excludes CID, anchor, identity
- `cid` — set when pinning succeeded
- `precommit-hash` — set on the precommit phase, or when a precommit was bound

## What this action will not do

- Score, rank, or reputation-weight a lab
- Run EAS unless you set `backend: eas` (experimental, keyed, gas)
- Execute `derived_from` commands (`--verify-derived` is not passed)
- Claim the science is correct — only that these bytes were specified,
  produced, and (if verify passed) match the recorded hashes
