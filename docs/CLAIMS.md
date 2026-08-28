# What you may claim, and what backs it

Each claim below is earned by a specific, runnable check. Do not make a claim
whose backing command you have not run.

| Claim | Backed by | Command |
|---|---|---|
| Tamper-evident record | Artifact rehash against the manifest | `farm-notary verify` |
| Existed by time T | OpenTimestamps proof, Bitcoin attestation | `farm-notary upgrade`, then `ots verify` for full independence |
| Verifiable provenance | Manifest records command, config, seed, git SHA + dirty flag, environment (packages/lockfile hash); a stranger can re-derive everything | `farm-notary manifest --command ... --lockfile ...` |
| Pre-specified design | Precommit proof (config, command, git SHA anchored before the run); manifest's `precommit_hash` binds the two phases | `farm-notary precommit --config ... --command ... --backend ots`, then `farm-notary manifest --precommit precommit.json`; `farm-notary verify` reports both timestamps |
| Bitwise reproducible (scoped) | A reproduction receipt produced by re-running the recorded command and comparing every listed artifact; what was excluded is noted in the receipt | `farm-notary reproduce`; with `--ignore` globs for legitimately nondeterministic artifacts |
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
- **Mismatch (1/7):** `REPORT.md` — not nondeterminism; the report embedded
  its own output path, so a re-run into a fresh directory changed one line.
- The reproduction receipt was anchored through all four public
  OpenTimestamps calendar pools.

After fixing the report upstream to record the command with a `{run_dir}`
placeholder, a second session reproduced **8/8 artifacts bitwise** (including
`REPORT.md` and `run_config.json`) with no ignore globs, and the experiment
gained a `verify-report` check that recomputes the derived artifacts
(`summary.csv`, `allocation_means.csv`, `REPORT.md`) byte-identically from the
raw `trials.csv` — that recomputation surfaced a real subtlety: pandas'
default CSV float parser is off by one ulp, and only
`float_precision="round_trip"` recovers the written values exactly.

Valid claim as of those runs: *"the consensus experiment's artifacts are
bitwise reproducible from the committed seed in a pinned environment,
verified by re-execution, and its summary statistics recompute exactly from
the raw trial data."*

Not yet demonstrated: cross-machine reproduction (different hardware/BLAS)
outside a CI environment. Until a clean-machine receipt produced on different
hardware exists, do not claim more than same-environment reproducibility.

## Cross-machine reproduction status

CI-machine reproduction is demonstrated via AgentFarm's
`.github/workflows/reproduce-consensus.yml` (see
`integration/agentfarm/README.md`): on every change to the experiment, a
GitHub Actions runner re-runs the recorded command from the committed seed and
fails unless every artifact is byte-identical. Because GitHub-hosted runners
The successful workflow run on a GitHub-hosted x86-64 Linux runner confirms
reproducibility in a different process/filesystem than the original run; the
workflow does not establish an identical dependency set or BLAS.

What remains undemonstrated: reproduction on **different hardware** (e.g.,
Apple Silicon, AMD EPYC) or a **different BLAS** implementation. If the
experiment relies on floating-point operations whose result depends on SIMD
instruction sets or BLAS routing, bit-for-bit output is not guaranteed across
architectures. Until such a receipt exists, scope the claim to
"byte-identical on x86-64 Linux in a pinned environment."
