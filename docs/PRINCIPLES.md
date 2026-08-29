# FarmNotary principles

FarmNotary records which bytes a run directory contained, optionally pins
those bytes, and can prove a digest existed by a date. A verifier who has
the bytes can rehash them and check an OpenTimestamps proof against that
digest.

This document must never be read as claiming that a notarized run is
scientifically correct, that unpublished runs do not exist, or that the
author is a reliable narrator of their own experiment.

These are constraints, used to refuse features without relitigating them.

## 1. An anchor proves existence, not correctness

**State only what the bytes and the calendar can support.**

An OpenTimestamps proof commits a digest to Bitcoin. It answers: these
bytes existed by time T. It does not answer whether the model is
specified well, whether the seed was chosen after seeing outcomes, or
whether anyone should believe the result. `farm-notary verify` prints a
claim card whose last row is always `not claimed: scientific correctness`.
Exit code 0 means no attempted check failed. A manifest can perfectly
notarize a wrong result.

**Rules out:** a "verified result" badge; a paper-pack sentence that the
finding is correct; treating `verify` exit 0 as review; any line that
collapses "tamper-evident record" into "valid experiment."

## 2. Close the reader gap; do not decorate the publisher

**Build what a stranger can check. Refuse what only flatters the lab.**

A publisher leaves with a timestamp and a CID. A skeptical reader gains
those only after obtaining the bytes and running the checks. That
asymmetry is a product defect, not a property to keep. Features that let
a stranger fetch, rehash, reproduce, and read a scoped claim card are in
scope. Features that score labs, rank runs, or issue a protocol identity
are not.

The cost: authors get less ceremony. Readers retrieve the tree, run
`verify`, and decide whether to re-execute recorded commands.

**Rules out:** reputation tokens; scoreboards; a "trusted lab" registry;
making EAS or `sign` required for a passing card.

## 3. Cherry-picking is out of scope; mislabeling it is not

**Do not prevent selective publication. Do not describe a post-hoc stamp
as pre-registration.**

Anchoring is free. An author can stamp 500 runs and cite one. Each proof
is valid for its digest. FarmNotary cannot see unpublished directories
and will not try. A mandatory public ledger of every local run is not a
product.

What the tool does instead: `precommit` anchors config, command, and
code identity before artifacts exist. Only a bound precommit earns
"pre-specified design." A timestamp taken after the result is known is
not that claim.

The cost: a dishonest author still publishes the favorable run. The tool
will stamp it.

**Rules out:** a claim that this run is the only run; describing an OTS
proof as evidence against HARKing or the file drawer; refusing to stamp
because other runs exist.

## 4. Self-assertion is an input, not a conclusion

**Record what the author says. Promote it to a claim only when a
stranger can rerun the check — or see that nobody has.**

`command`, `git_sha`, `environment`, declared `derived_from` rules, and
a same-party `reproduction.json` are produced by the party who benefits.
They belong on the record because they are checkable or explicitly
unchecked. They must not look earned when they are not.

`git_dirty` is recorded, not hidden. `precommit` and `anchor` refuse a
dirty tree unless `--allow-dirty`; a supplied SHA is not a bypass.
Same-machine author reproduction earns "bitwise reproducible (scoped)",
not "independently reproduced." Derivation commands on a downloaded
manifest are not executed unless `--verify-derived`. Claim levels use a
`_declared` suffix when the artefact has not been validated against
this record.

**Rules out:** treating `git_sha` as a third-party code audit; default
execution of `derived_from`; collapsing `_declared` into the earned
label; printing "independently reproduced" for a receipt the author
wrote on the original machine.

## 5. Publish is one-way; the default is not to publish

**Do not contact a network until the operator has an inspectable local
record and has opted in.**

A remote pin cannot be unpublished. A calendar stamp and an EAS
attestation are likewise one-way. The CLI therefore defaults `anchor`
and `precommit` to `dry-run`. Nothing is hashed or uploaded until a
publish allowlist is declared. The manifest records `publish_patterns`,
optional `publish_profile`, and `unmatched_count` so the hashed set can
be inspected before `--pin-remote` or `--backend ots`. The Action
defaults to `ots` because adding it is already a publish decision.
There is no undo.

The cost: an extra local step. CI must name a live backend.

**Rules out:** hashing the whole directory by default; implicit remote
pin; a "retract" that claims to remove pinned bytes; changing the CLI
default to a live backend.

## 6. Omission is policy, and the policy is part of the claim

**Never drop a file from the hashed set without a recorded rule. Never
print the names of omitted files.**

A manifest that looks complete while silently skipping files is an
integrity failure. Discovery is allowlist-first: no patterns, no
manifest. A denylist of path fragments (`ballot`, `vote`, `voter`,
`individual_choice`, `private`) then rejects matches even if a glob
would admit them. Non-hidden, non-notary files that were not hashed
increment `unmatched_count`. Names of those files are not printed, so a
forgotten path is not leaked by the warning.

The cost: `unmatched_count` tells a reviewer that something was left
out, not what. A forgotten official artifact is simply absent from the
proof.

**Rules out:** a default whole-directory hash; omitting `unmatched_count`
or `publish_patterns` from the v1 body; a flag that overrides the
denylist; logging omitted filenames on the CLI or in the paper pack.

## 7. Spend a trust assumption only when it buys something the keyless path cannot

**OpenTimestamps is the recommended live backend because it adds no key
and no identity. Anything that requires both stays experimental and
labeled.**

OTS needs no keys and no gas. FarmNotary checks that the proof commits
to the content hash; full Bitcoin header verification is left to
`ots verify`. EAS needs a funded key, gas, and a verifier who already
knows the attester address. That extra assumption is acceptable only
when the goal is attributable, queryable attestation. It is not a
general upgrade from OTS.

**Rules out:** making `eas` the CLI or Action default; a FarmNotary-issued
identity or protocol token; presenting an EAS UID as equivalent to a
Bitcoin attestation; dropping the "experimental" label while the
attester-address assumption remains.

## 8. Do not operate infrastructure that is a solved public good

**Implement the domain. Outsource the stamp.**

A custom `SimulationRegistry` contract was removed. Public calendars,
Bitcoin, IPFS pinning services, and EAS on Base already exist.
FarmNotary's work is deciding what is hashed, what never leaves the
machine, and what a claim card may say. A static `index` is a directory
of published manifests, not a chain and not a ranking.

The cost: availability and policy of calendars, gateways, and pin
services are not controlled here. A local Kubo pin is not archival.

**Rules out:** a new FarmNotary contract, hosted calendar, or pinning
service; running the simulation on-chain; turning `index` into a
scoreboard.

## Non-goals

Permanently out of scope, not deferred.

- **Scientific correctness.** The tool hashes and timestamps.
- **Preventing the file drawer.** Unpublished runs are invisible.
  `precommit` makes pre-specification checkable; it does not inventory
  what the author did not publish.
- **A complete archive of the run directory.** Allowlist plus denylist
  is the product. Individual voter or agent choices are not published.
- **Cross-hardware bitwise identity.** The only sentence the tool may
  emit today is `byte-identical on x86-64 Linux in a pinned
  environment`. Other machines report `N/M` until
  `DEMONSTRATED_SCOPES` expands.
- **Anchoring infrastructure.** No contracts, calendars, or pin
  services operated here.
- **Scores, rankings, or reputation.** Claim levels are labels, not
  ranks.
- **Default execution of recorded commands on `verify`.** A downloaded
  manifest is untrusted input.
- **Retracting a pin or a stamp.** Publish is one-way.

## How to use this document

When a proposed change conflicts with a principle, reject the change.
"Users would like it" is not an argument. If the principle is wrong,
change it in the same commit as the code, with a changelog note that
states which principle moved and why. A principle edited to fit one
feature is being ignored; rewrite it so a later contributor can still
refuse the next similar request.

Do not add a principle that forbids nothing. If a sentence would not
change a design argument, it does not belong here.
