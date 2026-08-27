# What you may claim, and what backs it

Each claim below is earned by a specific, runnable check. Do not make a claim
whose backing command you have not run.

| Claim | Backed by | Command |
|---|---|---|
| Tamper-evident record | Artifact rehash against the manifest | `farm-notary verify` |
| Existed by time T | OpenTimestamps proof, Bitcoin attestation | `farm-notary upgrade`, then `ots verify` for full independence |
| Verifiable provenance | Manifest records command, config, seed, git SHA + dirty flag, environment (packages/lockfile hash); a stranger can re-derive everything | `farm-notary manifest --command ... --lockfile ...` |
| Bitwise reproducible (scoped) | Re-run of the recorded command produces byte-identical artifacts | `farm-notary reproduce` |
| Independently reproduced | A reproduction receipt produced on another machine, optionally timestamped | `farm-notary reproduce --anchor` |

Never claimable by tooling: **correctness of the science**. Immutability is
not correctness; a manifest can perfectly notarize a wrong result. Re-run the
committed seed, interrogate the model, replicate with an independent
implementation.

## Scoping the reproducibility claim

`reproduce` compares every artifact the manifest lists. Artifacts that are
legitimately nondeterministic (encoded videos, database files) must be
excluded with `--ignore` globs — the receipt records what was ignored, so the
claim stays honest: "bitwise reproducible except X" rather than a blanket
statement that one MP4 can falsify.

## Empirical baseline (2026-08-27)

The AgentFarm political consensus experiment
([AgentFarm PR #985](https://github.com/Dooders/AgentFarm/pull/985), 100
trials, 300 voters, 8 candidates, seed 0) was notarized and reproduced on the
same machine with `farm-notary reproduce`:

- **Bitwise identical (6/7 artifacts):** `trials.csv`, `summary.csv`,
  `allocation_means.csv`, and all three PNG figures — including matplotlib
  output, which is byte-stable within a pinned environment.
- **Mismatch (1/7):** `REPORT.md` — not nondeterminism; the report embeds its
  own output path, so a re-run into a fresh directory changes one line.
  Fixable upstream by recording the command with a placeholder.
- The reproduction receipt was anchored through all four public
  OpenTimestamps calendar pools.

Valid claim as of that run: *"the consensus experiment's data artifacts and
figures are bitwise reproducible from the committed seed in a pinned
environment, verified by re-execution; the report is identical up to its
embedded output path."*

Not yet demonstrated: cross-machine reproduction (different hardware/BLAS)
and continuous reproduction in CI. Until a clean-machine receipt exists, do
not claim more than same-environment reproducibility.
