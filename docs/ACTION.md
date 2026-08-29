# Reusable GitHub Action (`dooders/farm-notary-action`)

Other labs adopt FarmNotary without reading DESIGN.md: drop this action into a
workflow. It precommits at job start, notarizes and optionally pins on
success, uploads `manifest.json` + `manifest.ots`, and **fails the job if
verify fails**.

This repository hosts the action at the root (`action.yml`). Consume it as:

```yaml
uses: dooders/FarmNotary@v0.2
```

The public name for the same contract is `dooders/farm-notary-action`.

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
        uses: dooders/FarmNotary@v0.2
        with:
          phase: precommit
          run-dir: results
          config: experiments/consensus/config.json
          command: "python run_experiment.py --seed 0 --out {run_dir}"
          lockfile: requirements.lock

      - name: Run the experiment
        run: python run_experiment.py --seed 0 --out results

      - name: Notarize, pin, verify
        uses: dooders/FarmNotary@v0.2
        with:
          phase: notarize
          run-dir: results
          config: experiments/consensus/config.json
          command: "python run_experiment.py --seed 0 --out {run_dir}"
          publish: "*.csv,*.png,REPORT.md"
          lockfile: requirements.lock
          pin-remote: pinata
          backend: ots
```

`phase: notarize` binds `results/precommit.json` when it is present, writes
the manifest, anchors (`ots` by default), runs `farm-notary verify`, and
uploads the notary files. A verify failure fails the job; artifacts are still
uploaded so a reviewer can inspect the mismatch.

## Already-finished run

```yaml
- uses: dooders/FarmNotary@v0.2
  with:
    phase: all
    run-dir: results
    publish: "*.csv,REPORT.md"
    backend: dry-run
```

`phase: all` is notarize-only: use it when the experiment has already written
artifacts. It does not invent a precommit after the fact.

## Inputs

| Input | Default | Purpose |
|---|---|---|
| `phase` | `notarize` | `precommit`, `notarize`, or `all` |
| `run-dir` | required | Artifact directory |
| `config` | | Experiment profile JSON (`notary.publish`, `notary.derived_from`) |
| `command` | | Recorded command with `{run_dir}` |
| `publish` | | Comma-separated allowlist globs |
| `lockfile` | | Hashed into the environment fingerprint |
| `backend` | `ots` | `ots` (recommended), `dry-run`, or `eas` (experimental) |
| `pin-remote` | | Kubo remote pin service; implies pin |
| `pin` | `false` | Pin to a local Kubo daemon |
| `identity-key` | | Optional minisign / SSH key; still no protocol token |
| `identity-scheme` | `ssh` | `ssh` or `minisign` |

Pinning needs a reachable Kubo daemon (`FARM_NOTARY_IPFS_API`) with the remote
service already registered. OpenTimestamps needs outbound HTTPS to the public
calendars. Use `backend: dry-run` in jobs that must not leave the runner.

## Outputs

- `content-hash` — canonical manifest hash (excludes CID, anchor, identity)
- `cid` — set when pinning succeeded
- `precommit-hash` — set on the precommit phase, or when a precommit was bound

## What this action will not do

- Score, rank, or reputation-weight a lab
- Run EAS unless you set `backend: eas` (experimental, keyed, gas)
- Claim the science is correct — only that these bytes were specified,
  produced, and (if verify passed) match the recorded hashes / derivations
