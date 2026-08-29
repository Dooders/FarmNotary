# What you may claim, and what backs it

Each claim below is earned by a specific, runnable check. Do not make a claim
whose backing command you have not run.

`farm-notary verify` prints a stacked ladder (highest earned level, then the
gap that blocks the next) and the orthogonal claim-card rows. Reviewers read
the card, not the exit code. **Missing is not failure** — it means that
claim was not earned. Exit code 0 means no attempted check failed; it does
not mean every row is earned, and a printed `Ln` is not scientific
correctness. Exit codes stay for scripts.

```
claim card
level: none — no earned ladder level
next:  L0 — these bytes existed by time T; Bitcoin headers not verified by this tool (missing: Bitcoin attestation)
•  tamper-evident record           — pass
•  existed by time T               — pending
•  pre-specified design            — missing
•  bitwise reproducible (scoped)   — 6/6, ignored: *.mp4; byte-identical on x86-64 Linux in a pinned environment
•  not claimed: scientific correctness
```

## Reader ladder (L0–L3)

These names are 1:1 with `farm_notary.ladder`. Unearned names are
unprintable: do not write `FarmNotary L2` until the beacon binding checks
pass, and do not write independently reproduced for a self-run unsigned receipt.
Pending OTS (any calendar) is not L0. `bytes` / `bitwise` below are index
labels, not ladder levels.

| Level | Meaning | Evidence | Command |
|---|---|---|---|
| none | No earned ladder level | Tamper-evident is not `pass`, or there is no Bitcoin attestation | `farm-notary verify` |
| L0 | These bytes existed by time T | Tamper-evident `pass` **and** `existed by time T` is `Bitcoin height N`. Pending, dry-run, and EAS do not earn L0. FarmNotary checks that the proof commits to the content hash and contains a Bitcoin-height attestation; it does **not** check Bitcoin headers. | `farm-notary upgrade`, then `ots verify` for header verification |
| L1 | Re-execution specified (command was not run) | L0 **and** a non-empty `command` **and** `git_sha` **and** environment fingerprint (`os`, `arch`, `python`). Precommit stays on the card, not this rung. | `farm-notary manifest --command ... --lockfile ...` |
| L2 | Seed not grindable after the plan | L1 **and** a bound precommit `seed_plan` **and** a `precommit.ots` that commits to that plan **and** the plan's `created_utc` is not after the round's scheduled time **and** the run's seed equals `sha256-v1` at the recorded index for **exactly** `min_round` **and** recorded randomness matches a live or fixture beacon fetch. Publishing a subset of the committed set is allowed; missing indices are listed. Live fetch is TLS to the configured drand HTTP API; threshold signatures are not checked. L2 is not scientific correctness. | `farm-notary precommit --seed-count N --inclusion … --backend ots`, `farm-notary derive-seeds`, `farm-notary verify --live-beacon` |
| L3 | Independent identity reproduced it | L2 **and** a Sigstore keyless signature on the reproduction receipt (`receipt["sigstore"]` bundle verified by `cosign verify-blob --offline`). **A receipt count is not credibility** — ten throwaway Gmail reproductions are not equivalent to one lab CI reproduction. Inspect `sigstore identity:` / `sigstore issuer:` notes to distinguish workload-identity CI tokens from personal OIDC logins. | `farm-notary reproduce --sign` (requires `cosign` on PATH and OIDC access; for CI use `--identity-token`). Install signer identity extras: `pip install farm-notary[sigstore]` |

## Checks that earn (or do not earn) a level

Derivation is not a card row. `verify` does not run `derived_from` commands
unless you pass `--verify-derived` (trusted manifests only). Missing that
check is not a failure.

That is the difference between a hash tool and a research notary.

| Claim | Backed by | Command |
|---|---|---|
| Tamper-evident record | Artifact rehash against the manifest | `farm-notary verify` (claim card: pass/fail). Required for any ladder level. |
| Existed by time T | OpenTimestamps proof, Bitcoin attestation | `farm-notary upgrade`, then `ots verify` for full independence. Only `Bitcoin height N` earns L0; `pending` does not. |
| Verifiable provenance | Manifest records command, config, seed, git SHA, environment (packages/lockfile hash); a stranger can re-derive everything. `git_dirty` is recorded, but a dirty tree is not a code-identity claim: `precommit` and `anchor` refuse it unless `--allow-dirty`. Omitting `git_dirty` still inspects the working tree — a supplied SHA is not a bypass. | `farm-notary manifest --command ... --lockfile ...`; `farm-notary precommit` / `anchor` (fail if dirty). `command` + `git_sha` + fingerprint earn L1 once L0 is held; that is specification, not a completed re-run. |
| Pre-specified design | Precommit proof (config, command, git SHA anchored before the run); manifest's `precommit_hash` binds the two phases | `farm-notary precommit --config ... --command ... --backend ots`, then `farm-notary manifest --precommit precommit.json`; `farm-notary verify` reports `precommit bound`. Shown on the card; not a ladder level. |
| Bitwise reproducible (scoped) | A reproduction receipt produced by re-running the recorded command and comparing every listed artifact; what was excluded is noted in the receipt | `farm-notary reproduce`; with `--ignore` globs for legitimately nondeterministic artifacts. Does not earn L3 without a Sigstore signature. |
| Independently reproduced | Requires a Sigstore keyless signature on the reproduction receipt (`farm-notary reproduce --sign`). An unsigned `reproduction.json` is not this claim. **A receipt count is not credibility** — inspect `sigstore identity:` / `sigstore issuer:` notes to distinguish workload-identity CI tokens from personal OIDC logins. | `farm-notary reproduce --sign` (requires `cosign` on PATH) |
| Statistics recompute exactly | `derived_from` rules in the experiment profile recompute named artifacts from their sources. Commands are **not** run by default (a downloaded manifest is untrusted input). | `farm-notary verify --verify-derived` (prints the derivation claim when rules pass). Without the flag, verify still exits 0 and notes that rules were not executed. |
| Sweep / paper figure | Parent campaign lists child CIDs, seeds, and a shared config hash | `farm-notary campaign`, then `farm-notary verify --campaign` |
| Signed publication (optional) | minisign or SSH signature of the content hash, recorded on the manifest | `farm-notary sign --scheme ssh\|minisign --key PATH` |
| Paper appendix | CID, content hash, Bitcoin attestation or pending, allowlist, unmatched count, precommit hash, artifact label, reader-ladder placeholder (`—`), scoped sentence | `farm-notary paper-pack` (add `--verify-derived` before the sentence claims statistics recompute). Cite `verify` for `Ln`; the appendix does not print a ladder level. |

Never claimable by tooling: **correctness of the science**. Immutability is
not correctness; a manifest can perfectly notarize a wrong result. Re-run the
committed seed, interrogate the model, replicate with an independent
implementation.

> **Note on anchoring backends:** CLI `anchor` / `precommit` default to
> `--backend dry-run` (no network). The recommended *live* backend is
> `--backend ots` — no key, no gas, Bitcoin-backed. The GitHub Action
> defaults to `ots`. `--backend eas` is **experimental**: funded attester
> key, gas, and an attester-address trust problem that OTS avoids. See
> [EAS.md](EAS.md).

## Durable pin as the published path

A CID is not a citation if only a laptop holds the bytes. Local Kubo
(`--pin`) is a lab convenience. For anything you cite in a paper or academy
writeup, `--pin-remote` (Pinata, web3.storage, or a pinning-service API) is
the documented default. The manifest records `pin_service` so reviewers can
see which path was used.

## Claim levels (not scores)

`farm-notary index` and `paper-pack` label what artifacts were checked
(`bytes`, `bitwise`, …). These are never ranks and are **not** the L0–L3
reader ladder. `paper-pack` prints `Artifact label` for that vocabulary and
leaves `Reader ladder` as `—`: this appendix does not cite `Ln`. Do not
cite `FarmNotary Ln` from a PDF until Bitcoin header verification is
in-tool. Run `farm-notary verify` for the stacked card. A campaign
appendix has no single-run ladder.

| Level | Meaning |
|---|---|
| `bytes` | Tamper-evident artifact hashes only |
| `derived_declared` | Derivation rules are on the record but have not been executed |
| `bitwise` / `bitwise_declared` | A `reproduction.json` exists; `_declared` if it is unbound or not `ok` |
| `bitwise+derived_declared` | Validated receipt plus derivation rules; derivation must be confirmed with `--verify-derived` to earn the full `bitwise+derived` claim |

A `_declared` suffix means the artefact is present, not that the claim is earned.

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
