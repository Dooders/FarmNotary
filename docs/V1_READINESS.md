# FarmNotary v1.0 readiness review

**Date:** 2026-09-01
**Tree:** `dev` @ `b947a6ced4488da2f9db67be2aaddd61bb23229a`
**Package:** `farm-notary==0.2.0` (`Development Status :: 4 - Beta`)
**Verdict:** **not passable for 1.0**

This is an implementation review, not a tag. 1.0 here means a **stability
promise**: after `v1.0.0`, new required fields, renames, and removals wait
for 2.0. The in-tree 1.0 milestone is issues
[#79](https://github.com/Dooders/FarmNotary/issues/79)–[#83](https://github.com/Dooders/FarmNotary/issues/83).

## Executive verdict

The **core notary** — allowlist-first manifests, withheld commitment, stamp
vs content hash, OpenTimestamps as the live backend, a claim card that
refuses to call immutability “correctness,” and refuse-by-default command
execution — is a real 0.2 product. Unit tests, ruff, and mypy are green on
this tree. The L0–L3 reader ladder is honest about what it does not prove.

That is not enough to freeze. `farm-notary verify` is the reader-side check
the principles exist for, and it will treat files **outside the run
directory** as official artifacts when an untrusted `manifest.json` names
them. The Python API accepts `git_dirty=False` as a dirty-tree bypass.
Index labels never award the documented `derived` / `bitwise+derived`
claims. Advertised GitHub Actions auto-trust for `reproduce` is not
implemented. The 1.0 milestone issues are still open; open
[PR #84](https://github.com/Dooders/FarmNotary/pull/84) records “no pre-tag
cleanup” without fixing the consume-path holes.

**Do not tag 1.0 on this tree.** After the blockers below, the same core
would be a **pass with caveats** (experimental surfaces carved out of the
freeze, Bitcoin-header policy recorded as option 2, soak done).

Implementation issues filed from this review (milestone [1.0](https://github.com/Dooders/FarmNotary/milestone/1)):

| Finding | Issue |
|---|---|
| C1 path escape | [#88](https://github.com/Dooders/FarmNotary/issues/88) |
| H1 `git_dirty=False` | [#89](https://github.com/Dooders/FarmNotary/issues/89) |
| H2 claim labels | [#90](https://github.com/Dooders/FarmNotary/issues/90) |
| H3 CI reproduce trust | [#91](https://github.com/Dooders/FarmNotary/issues/91) |
| H4 identity vs `evaluate_claims` | [#92](https://github.com/Dooders/FarmNotary/issues/92) |
| H5 campaign `OK` | [#93](https://github.com/Dooders/FarmNotary/issues/93) |
| M1 glob `*` | [#94](https://github.com/Dooders/FarmNotary/issues/94) |
| M2 JSON Schema / wheel | [#95](https://github.com/Dooders/FarmNotary/issues/95) |
| M4 CID binding warning | [#96](https://github.com/Dooders/FarmNotary/issues/96) |
| M5 campaign `seed_plan` | [#97](https://github.com/Dooders/FarmNotary/issues/97) |
| M6 Zenodo token argv | [#98](https://github.com/Dooders/FarmNotary/issues/98) |

Process issues already open: #79 freeze, #80 experimental, #81 headers, #82 migration, #83 tag.

## How this review was done

- Read `docs/PRINCIPLES.md`, `docs/CLAIMS.md`, `docs/DESIGN.md`,
  `docs/MIGRATION.md`, README, CHANGELOG, schemas, Action contract, and
  the 1.0 milestone issues.
- Read the `farm_notary/` implementation (core path plus CLI, Action,
  later-wave helpers).
- Ran `.venv` pytest, ruff, and mypy on this checkout.
- Reproduced the consume-path and API probes against the installed
  package (results in [Evidence](#evidence)).

Live OpenTimestamps → Bitcoin, live drand, live IPFS pin, and a live
AgentFarm install were **not** exercised. Issue #83 calls that soak out
as a tag prerequisite; this review treats it as unverified.

## What v1.0 means here

| Source | Bar |
|---|---|
| README / DESIGN | Schema id stays `farmnotary.manifest.v1` through 0.x; breaking changes reserved for 1.0 |
| PRINCIPLES | Existence is not correctness; reader-side checks; allowlist-first; OTS over keyed backends; no scoreboard |
| Issue #79 | Freeze current schema / `__all__` / CLI, **or** clean up first, then record the decision |
| Issue #80 | EAS and #40 helpers must not inherit the 1.0 stability promise |
| Issue #81 | Either in-tool Bitcoin headers, or keep paper-pack `—` as 1.0 policy |
| Issue #82 | `0.2.0 → 1.0.0` migration page and pin examples |
| Issue #83 | Version bump, Production/Stable classifier, tag, PyPI, Action pin; soak one Bitcoin-height OTS path and AgentFarm |

A freeze that includes first-cut `emit-interop` / `archive` / `chain` /
`register-schema` as 1.0 CLI surface is the wrong freeze.

## Requirement audit

| Requirement | Evidence | Status |
|---|---|---|
| Allowlist-first publish; denylist after | `build_manifest` refuses empty patterns; profiles + `PRIVATE_NAME_FRAGMENTS`; tests in `test_manifest.py` / `test_profiles.py` | Met |
| Stamp fields excluded from `content_hash` | `_STAMP_KEYS` = cid / pin / anchor / identity | Met |
| Withheld salted Merkle; names not printed | `withheld.py`; reveal rejects path escape; `test_withheld.py` | Met on **build** and **reveal**; verify does not re-check the root |
| Dirty tree refused unless `--allow-dirty` | CLI passes live `detect_git_status`; `test_dirty.py` | Met on **CLI**; **not** met on library `git_dirty=False` |
| Claim card: missing ≠ failure; science not claimed | `evaluate_claims` + `evaluate_ladder`; docs tests lock CLAIMS.md | Met for L0–L3 |
| L0 needs Bitcoin-height attestation, not pending OTS; headers not claimed | `ladder.py`; `PAPER_LADDER_CELL = "—"` | Met in code; #81 decision not recorded as 1.0 policy |
| L1 = recorded command + git SHA + env; command not run | `evaluate_ladder` | Met |
| L2 = beacon binding; fetch failure unearned not fail | `beacon.py` + `verify.py`; TLS, no BLS check, documented | Met as specified; not a cryptographic beacon verify |
| L3 = Sigstore on receipt, not independence | `sigstore.py`; tokens via env / `@PATH` only | Met |
| `reproduce` / `derived_from` fail-closed | CLI exit 2 without trust/`--i-accept-untrusted-command`; `allow_execute=False` | Met; advertised **CI** trust is missing |
| Untrusted manifest cannot hash/pin outside the run dir | `validate` / `verify_run_dir` / `IpfsClient.add_run_dir` | **Failed** (C1) |
| JSON Schema is a freeze harness | `additionalProperties: true`; untyped `anchor` / `beacon` / `ci_provenance`; schemas not in the wheel | Weak |
| Experimental surfaces labeled, not frozen | CLI help; README status; #80 open | Incomplete |
| 0.2 → 1.0 migration + pins | `docs/MIGRATION.md` has 0.1 → 0.2 only | Missing |
| Version / classifier / tag | `TOOL_VERSION = "0.2.0"`; Beta classifier | Not started |
| Tests cover critical path | 458 passed locally | Strong for unit/offline; no live soak |
| Docs match CLI | `test_docs.py` | Strong for README/CLAIMS/slides; VERIFIER vs ladder vocab is dual |

## Findings

### Blockers (do not freeze or tag)

#### C1. Untrusted manifests can make verify hash files outside the run directory ([#88](https://github.com/Dooders/FarmNotary/issues/88))

`Manifest.validate()` checks types, required keys, artifact/hash agreement,
and the denylist. It does **not** reject `..`, absolute paths, or NUL. 
`verify_run_dir` then does `run_dir / name` with no containment check.

Reproduced on this install:

- Artifact `../secret.bin` with the sibling file’s SHA-256: `validate()`
  succeeds, `verify_run_dir` returns `[]`, claim card `tamper_evident`
  is `pass`.
- Artifact `/etc/hosts` with that file’s SHA-256: `Path(run_dir) / "/etc/hosts"`
  becomes `/etc/hosts`; same pass.

`load_manifest` validates by default, so a downloaded `manifest.json` with
those names is accepted. `IpfsClient.add_run_dir` reads the same joined
paths and would upload those bytes. `anchor.detail.proof` is joined the
same way.

Contrast: withheld reveal (`withheld.py`) and Zenodo upload (`archive.py`)
already reject escapes. Build-time discovery uses `relative_to(run_dir)`
and skips symlinks. The hole is **consume**, which is the reader path.

A 1.0 freeze would bake this verify contract. Fix before tag: reject
non-relative, non-contained POSIX paths in `validate()`, and refuse
`verify` / pin joins that escape `run_dir.resolve()`.

#### H1. `git_dirty=False` bypasses the dirty-tree check ([#89](https://github.com/Dooders/FarmNotary/issues/89))

`require_clean_identity` only re-detects when `git_dirty is None`.
`resolve_git_identity` trusts a supplied `False` and never looks at the
tree. On this dirty checkout:

- `require_clean_identity(None)` raises `DirtyTreeError`
- `require_clean_identity(False)` returns

`notarize_run` / `anchor_run` / `build_precommit` all go through that
helper. The CLI is safe because it passes live `detected_dirty`. AgentFarm’s
documented hook is the library. Docs say a supplied SHA is not a bypass;
a supplied `git_dirty=False` is.

Always re-detect unless `allow_dirty=True`. Treat caller `git_dirty` as
a recorded bit, not as permission.

#### H2. Index claim labels cannot earn `derived` or `bitwise+derived` ([#90](https://github.com/Dooders/FarmNotary/issues/90))

`CLAIM_DERIVED` and `CLAIM_BITWISE_DERIVED` are never returned. Any
receipt plus `derived_from` yields `bitwise+derived_declared`, including
when the receipt is `ok` and bound. A failed receipt in that branch is
also `_declared`, which hides a failed bitwise check behind the same
label as “rules exist but were not run.”

CLAIMS.md says `--verify-derived` earns `bitwise+derived`.
`infer_claim_level` does not take derivation success. Paper-pack and
`index` use this vocabulary. Dead constants plus over-claiming docs are
not a freeze-ready public surface.

#### H3. Advertised CI auto-trust for `reproduce` is not implemented ([#91](https://github.com/Dooders/FarmNotary/issues/91))

Help and the refuse-path error say automatic trust is “same local
checkout **or** the same GitHub Actions repo/SHA.” 
`_trusted_reproduce_source` only ever returns `"local"` or `None`. It
does not read `GITHUB_SHA` / `GITHUB_REPOSITORY`. The `trust_note == "ci"`
branch is dead.

`sign-receipt` defaults to `false` (fail-closed, good). If a workflow
sets `sign-receipt: true` without `reproduce-cwd` pointing at a git root
whose HEAD matches `manifest.git_sha`, reproduce exits 2 and never
reaches `--sign`. Implement CI trust or stop advertising it; if the
Action should sign, pass a working trust path or
`--i-accept-untrusted-command` only in that controlled job.

#### P1. 1.0 process work is unfinished

On this tree: version `0.2.0`, Beta classifier, no `0.2 → 1.0` migration,
#80–#83 open. Paper-pack already prints `—` for the reader ladder
(option 2 of #81) but that is not recorded as 1.0 policy. Shipping
PR #84 as “the freeze” would freeze experimental CLI peers and skip C1–H3.

### High (honesty / contract)

#### H4. `evaluate_claims` ignores identity failures ([#92](https://github.com/Dooders/FarmNotary/issues/92))

`verify_identity_record` is a public export but is not part of
`ClaimCard.problems`. The CLI appends it after `evaluate_claims`. A
library caller who only uses `evaluate_claims` / `card.ok` can see a
forged `identity` stamp as fine. Fold identity into the card or stop
exporting `evaluate_claims` as the complete check.

#### H5. Campaign `verify` can print `OK` with no local children ([#93](https://github.com/Dooders/FarmNotary/issues/93))

Missing children are skipped unless `--require-local`. Exit 0 still
prints `OK` and the child **count**, which reads as a full check of a
CID-only campaign. Subset seed coverage is noted when `seed_index`
exists; that does not replace “N of M children were rehashed.” Print
what was skipped, or default `--require-local` for published campaigns.

### Medium

#### M1. Glob `*` is not “one path component” ([#94](https://github.com/Dooders/FarmNotary/issues/94))

`_matches_any_pattern` documents POSIX-style `*` (no `/`). Python
`fnmatch.fnmatch("subdir/file.csv", "*")` is `True`. `--publish '*'`
publishes the whole tree minus denylist. Named profiles use concrete
names / `*.ext`, so the default lab path is fine; freeze the real
semantics or implement component-wise matching.

#### M2. JSON Schema is too loose to be a freeze ([#95](https://github.com/Dooders/FarmNotary/issues/95))

Root `additionalProperties: true`. `environment`, `anchor`, `beacon`,
`ci_provenance` are untyped objects. Artifact paths have no
relative-path constraint. Tests lock **required** keys, not optional
renames. Schemas live at repo root and are **not** in the wheel
(`package-data` is only `py.typed`).

#### M3. Experimental CLI is a peer of the stable commands ([#80](https://github.com/Dooders/FarmNotary/issues/80))

`register-schema` help has no experimental marker. `anchor --backend eas`
says “deprecated” while PRINCIPLES/EAS.md say “experimental.” 
`emit-interop` / `archive` / `chain` are mostly honest in help text but
still sit next to `manifest` / `verify`. #80 is the right issue; the
CLI has not done it. Do **not** freeze those subcommands as 1.0.

#### M4. CID binding proof failures are swallowed ([#96](https://github.com/Dooders/FarmNotary/issues/96))

`write_cid_binding_proof` catches `OtsError` and returns `None` so a
calendar outage does not abort notarize. There is no warning. An
operator can believe CID is bound when `manifest.cid.ots` was never
written. Log it; do not imply binding in the receipt unless the file
exists.

#### M5. Campaign `seed_plan` cross-check is count-only ([#97](https://github.com/Dooders/FarmNotary/issues/97))

`min_round`, `chain_hash`, `inclusion`, and `derivation` can diverge
from the precommit plan. Count match is not plan match.

#### M6. Zenodo token on argv ([#98](https://github.com/Dooders/FarmNotary/issues/98))

Sigstore correctly rejects raw JWTs. `--zenodo-token TOKEN` still
puts a secret on the process list. Prefer env-only, matching OIDC
handling.

#### M7. `cli.py` is 1805 lines

Stable commands, claim-card rendering, reproduce trust, and later-wave
helpers share one module. That is over the project’s 1k-line
maintainability bar. Splitting experimental subcommands out is the
cleanup #79 asked for *before* freeze, not after.

#### M8. Dual claim vocabulary

L0–L3 (`ladder.py`, CLAIMS.md, `verify`) vs `bytes` /
`derived_declared` (`claims.py`, `check`, VERIFIER.md, paper-pack).
The split is documented in `ladder.py`. VERIFIER.md is not locked by
`test_docs.py`. Fine if freeze text says “two vocabularies, do not
mix”; confusing if 1.0 marketing says “claim level” without saying
which.

### Low / accepted non-goals

- Pending OTS calendars are unauthenticated until Bitcoin (documented).
- L2 does not check drand threshold signatures (documented).
- Cross-hardware bitwise identity is not claimed (`DEMONSTRATED_SCOPES`
  is x86-64 Linux only).
- Withheld files are not re-hashed at `verify` (privacy vs integrity
  tradeoff; say so at freeze).
- Denylist substrings (`vote`, `private`) false-positive `votes.csv` /
  `privacy.md` — likely intentional.
- TOCTOU between classify and hash if the run dir is mutated mid-build.
- Release workflow does not re-run the test matrix before PyPI publish.
- No golden `content_hash` fixture for a known v1 body.
- `pyproject.toml` version vs `TOOL_VERSION` is only checked on tag.

## What is already 1.0-quality

These should survive a freeze; they are not the reason to wait.

- **Product shape.** Simulation stays off-chain. Anchoring is outsourced.
  Principles are used as constraints, not slogans. The claim card’s last
  row is always `not claimed: scientific correctness`.
- **Content hash.** Canonical JSON, stamp fields stripped, pin/stamp/sign
  without circular hashing. CID binding digest
  `H(content_hash || cid)` is the right idea when the proof is actually
  written.
- **Publication scope.** No patterns, no manifest. Profiles
  `consensus` / `rl-sweep` / `evolution-run`. Denylist after allowlist.
  `unmatched_count` + salted withheld root; names not printed.
- **CLI defaults.** `anchor` / `precommit` dry-run; Action defaults to
  `ots`. `--pin-remote` is the documented citation path; local Kubo
  warns.
- **Command execution.** `reproduce` and `--verify-derived` are
  fail-closed. Raw Sigstore JWTs on argv are rejected. Cosign pin
  `v2.5.3` is documented.
- **Ladder honesty.** Pending ≠ L0. L1 does not mean the command ran.
  L3 is not “independently reproduced.” Paper-pack does not cite `Ln`.
- **Tests and hygiene.** 458 pytest passed; ruff and mypy clean on
  `farm_notary`. Docs tests lock claim-card rows, forbidden phrases, and
  Action input names. `py.typed`, `__all__`, extras (`ots`, `chain`,
  `sigstore`, `lint`, `dev`) are coherent. Core stays stdlib-only.
- **Empirical scope.** Consensus matrix documents 10/10 on x86-64 Linux
  and does not over-claim ARM.

## 1.0 milestone vs this tree

| Issue | Title | This tree |
|---|---|---|
| #79 | Freeze schema / `__all__` / CLI | **Do not freeze yet.** Cleanup is still required (C1, H1–H3, experimental carve-out). PR #84 documents the opposite. |
| #80 | Keep experimental surfaces out of the promise | **Open.** Help text is incomplete; freeze text in #84 would include those commands. |
| #81 | Bitcoin headers vs paper-pack `Ln` | **Code already chose option 2** (`PAPER_LADDER_CELL = "—"`). Record it as 1.0 policy; do not imply in-tool header verify. |
| #82 | 0.2 → 1.0 migration | **Missing.** Need pin guidance (`>=1.0,<2.0`, `@v1.0.0`) and experimental labels even if the cut is additive. |
| #83 | Tag 1.0.0 | **Not started.** Still 0.2.0 / Beta. Soak (Bitcoin-height OTS + AgentFarm pin) not shown in this environment. |

## Relation to PR #84

[PR #84](https://github.com/Dooders/FarmNotary/pull/84) adds DESIGN /
MIGRATION freeze sections that say no pre-tag cleanup was required. That
is the omit-when-empty hash invariant only. It does not inspect verify
path containment, the dirty-tree API, claim-label earnability, or CI
reproduce trust.

Merging #84 as-is would make “we already froze 1.0” the story while C1
is still true. Record the freeze **after** the consume-path fixes, and
carve experimental commands out of the frozen CLI.

## Gate to “pass with caveats”

Minimum before tagging 1.0:

1. Contain artifact and proof paths in `validate` / `verify` / IPFS
   (C1), with tests for `..`, absolute paths, and pin.
2. Always inspect the working tree unless `allow_dirty=True` (H1).
3. Make `infer_claim_level` match CLAIMS.md, or delete the unearned
   labels and fix the docs (H2).
4. Implement or remove CI reproduce auto-trust; make `sign-receipt`
   work in the documented Action job (H3).
5. Label EAS / `register-schema` / `emit-interop` / `archive` / `chain`
   experimental and **exclude them from the 1.0 freeze** (#80).
6. Write `0.2.0 → 1.0.0` in MIGRATION.md; record #81 option 2; bump
   version, classifier, pins (#82, #83).
7. One live OTS proof that `verify` reports as `Bitcoin height N`
   (headers still `ots verify`). AgentFarm pin `>=1.0,<2.0` after ship.

Caveats that may remain on a 1.0 tag (say so in MIGRATION):

- This tool does not verify Bitcoin headers.
- L2 is TLS-to-drand, not BLS.
- L3 is not independent reproduction.
- Cross-hardware bitwise identity is not claimed.
- Later-wave helpers are unsigned / lookup-only / hash lineage.
- JSON Schema stays permissive for optional fields unless you tighten
  it in the same cut.

## Evidence

### Commands on this checkout

```text
.venv/bin/python -m pytest --tb=line -q
# 458 passed, 44 warnings in 21.23s

.venv/bin/ruff check farm_notary
# All checks passed

.venv/bin/mypy farm_notary
# Success: no issues found in 29 source files
```

### Consume-path probe (C1)

`verify_run_dir` on a manifest whose only artifact is `../secret.bin`
(sibling of the run dir) returned `[]`. The same for `/etc/hosts`.
`validate()` raised nothing. `evaluate_claims(...).tamper_evident` was
`"pass"`.

### Dirty-tree probe (H1)

On this dirty worktree, `require_clean_identity(None)` raised
`DirtyTreeError`; `require_clean_identity(False)` did not.

### Claim-label probe (H2)

Valid bound receipt plus `derived_from` inferred
`bitwise+derived_declared`, not `bitwise+derived`.

### CI-trust probe (H3)

`_trusted_reproduce_source` source contains neither `"ci"` nor
`GITHUB_SHA`.

### Line counts (freeze / maintainability)

| File | Lines |
|---|---|
| `farm_notary/cli.py` | 1805 |
| `farm_notary/beacon.py` | 816 |
| `farm_notary/manifest.py` | 743 |
| `farm_notary/verify.py` | 589 |
| `tests/test_sigstore.py` | 705 |
| `tests/test_beacon.py` | 753 |
| `tests/test_cli.py` | 639 |
