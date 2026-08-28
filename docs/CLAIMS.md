# What you may claim, and what backs it

Each claim below is earned by a specific, runnable check. Do not make a claim
whose backing command you have not run.

`farm-notary verify` prints these claims as a card. Reviewers read the card,
not the exit code. **Missing is not failure** — it means that claim was not
earned. Exit code 0 means no attempted check failed; it does not mean every
row is earned. Exit codes stay for scripts.

```
claim card
•  tamper-evident record           — pass
•  existed by time T               — pending
•  pre-specified design            — missing
•  bitwise reproducible (scoped)   — 6/6, ignored: *.mp4; byte-identical on x86-64 Linux in a pinned environment
•  not claimed: scientific correctness
```

That is the difference between a hash tool and a research notary.

| Claim | Backed by | Command |
|---|---|---|
| Tamper-evident record | Artifact rehash against the manifest | `farm-notary verify` (claim card: pass/fail) |
| Existed by time T | OpenTimestamps proof, Bitcoin attestation | `farm-notary upgrade`, then `ots verify` for full independence |
| Verifiable provenance | Manifest records command, config, seed, git SHA, environment (packages/lockfile hash); a stranger can re-derive everything. `git_dirty` is recorded, but a dirty tree is not a code-identity claim: `precommit` and `anchor` refuse it unless `--allow-dirty`. Omitting `git_dirty` still inspects the working tree — a supplied SHA is not a bypass. | `farm-notary manifest --command ... --lockfile ...`; `farm-notary precommit` / `anchor` (fail if dirty) |
| Pre-specified design | Precommit proof (config, command, git SHA anchored before the run); manifest's `precommit_hash` binds the two phases | `farm-notary precommit --config ... --command ... --backend ots`, then `farm-notary manifest --precommit precommit.json`; `farm-notary verify` reports `precommit bound` |
| Bitwise reproducible (scoped) | A reproduction receipt produced by re-running the recorded command and comparing every listed artifact; what was excluded is noted in the receipt | `farm-notary reproduce`; with `--ignore` globs for legitimately nondeterministic artifacts |
| Independently reproduced | A reproduction receipt produced on another machine, optionally timestamped | `farm-notary reproduce --anchor` |
| Statistics recompute exactly | `derived_from` rules in the experiment profile recompute named artifacts from their sources | `farm-notary verify` (prints the derivation claim when rules pass) |
| Sweep / paper figure | Parent campaign lists child CIDs, seeds, and a shared config hash | `farm-notary campaign`, then `farm-notary verify --campaign` |
| Signed publication (optional) | minisign or SSH signature of the content hash, recorded on the manifest | `farm-notary sign --scheme ssh\|minisign --key PATH` |
| Paper appendix | CID, content hash, Bitcoin attestation or pending, allowlist, unmatched count, precommit hash, scoped sentence | `farm-notary paper-pack` |

Never claimable by tooling: **correctness of the science**. Immutability is
not correctness; a manifest can perfectly notarize a wrong result. Re-run the
committed seed, interrogate the model, replicate with an independent
implementation.

> **Note on anchoring backends:** The commands above use `--backend ots`
> (OpenTimestamps), which is the recommended default — no key, no gas, and
> Bitcoin-backed.  The `--backend eas` option is **experimental**: it requires
> a funded attester key, costs gas, and ties verification to an attester
> address that verifiers must know in advance.  See [EAS.md](EAS.md) for
> details and a tradeoff table.

## Durable pin as the published path

A CID is not a citation if only a laptop holds the bytes. Local Kubo
(`--pin`) is a lab convenience. For anything you cite in a paper or academy
writeup, `--pin-remote` (Pinata, web3.storage, or a pinning-service API) is
the documented default. The manifest records `pin_service` so reviewers can
see which path was used.

## Official artifacts and publish profiles

The allowlist is the privacy model; a named profile is how a lab should fill
it. `consensus`, `rl-sweep`, and `evolution-run` are checked-in lists of
official artifacts — so forgetting `REPORT.md` or admitting a private path is
not a per-lab invention. The denylist still applies. The resolved
`publish_patterns` (and `publish_profile` when one was used) are recorded on
the manifest: the policy is part of the claim.

## Scoping the reproducibility claim

`reproduce` compares every artifact the manifest lists. A byte-diff is not a
science failure. The receipt's `diagnostics` name the packaging causes we
know how to spot:

- `embedded_absolute_path` — the artifact baked in the output directory
  (the `{run_dir}` fix that took the consensus experiment from 7/7 to 8/8)
- `timestamp` — a clock reading in `REPORT.md` or similar
- `float_print_format` — the same numbers, spelled differently
- `video_encoder` — MP4/WebM output that is not bit-stable

Those lines say **not a science failure** and how to fix the record
(`{run_dir}`, pin a print format, `--ignore '*.mp4'`). An unclassified
diff still says **byte-diff is not a science verdict**. Artifacts that are
legitimately nondeterministic must be excluded with `--ignore` globs — the
receipt records what was ignored, so the claim stays honest: "bitwise
reproducible except X" rather than a blanket statement that one MP4 can
falsify.

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

Not yet demonstrated: bitwise identity across hardware or BLAS. Until
Linux ARM and macOS ARM receipts are `ok` *and*
`farm_notary.scope.DEMONSTRATED_SCOPES` is expanded, do not claim more.

## Cross-machine reproduction status

Bitwise identity is shown on **x86-64 Linux** in a pinned environment (the
original lab machine, and GitHub Actions `ubuntu-latest`). That is the only
hardware class in `farm_notary.scope.DEMONSTRATED_SCOPES`.

The sentence the tool is allowed to emit — and the only sentence `verify` /
`reproduce` will print on a passing receipt from that class:

> byte-identical on x86-64 Linux in a pinned environment

A passing receipt from any other machine still reports `N/M`, then:

> on *machine*; cross-hardware bitwise identity is not a claim

A failed receipt reports `fail — N/M` and does not emit the sentence.

`.github/workflows/reproduce-consensus-matrix.yml` ran the 12×50 smoke cell
(seed 0) on 2026-08-28 (`f6053d0`). Same-job produce+reproduce on one
`ubuntu-latest` VM is the demonstrated cell: **10/10**, and the tool emitted
the sentence above. Cross-machine rows are evidence, not a claim:

| Machine | Score | What differed |
|---|---|---|
| Another `ubuntu-latest` VM | 10/10 | — |
| Linux ARM (`ubuntu-24.04-arm`) | 6/10 | `trials.csv`, `summary.csv`, `allocation_means.csv`, `contrasts.csv` (unclassified — not path/timestamp/float-print/encoder) |
| macOS ARM (`macos-14`) | 2/10 | those CSVs plus all four PNGs (unclassified) |

ARM receipts are not `ok`. `DEMONSTRATED_SCOPES` stays `{x86-64 Linux}`.
The 100×300 scientific cell was not re-run here.

AgentFarm's `.github/workflows/reproduce-consensus.yml` is same-arch x86-64
Linux CI on a different process/filesystem. It does not establish an
identical dependency set, a different BLAS, or a different ISA.

If the experiment relies on floating-point operations whose result depends
on SIMD instruction sets or BLAS routing, bit-for-bit output is not
guaranteed across architectures. Keep the claim narrow until the matrix
receipts say otherwise.
