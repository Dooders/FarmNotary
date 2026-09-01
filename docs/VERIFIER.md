# Reviewer quick-check (zero-install)

A reviewer who has a `manifest.json` (and optionally `manifest.ots`) can check
the claim level in seconds — **no venv, no extras, no account**.

## One-liner

```bash
# uv (recommended — installs in an isolated cache automatically)
uvx farm-notary check --manifest path/to/manifest.json

# pipx
pipx run farm-notary check --manifest path/to/manifest.json
```

Both tools manage an isolated environment automatically.  `uvx` is part of
[uv](https://docs.astral.sh/uv/); `pipx` is available via `pip install pipx`.
Neither requires you to create or activate a venv. Requires FarmNotary
**1.0.0** or later (`check` is not in 0.1.0).

The command reads `manifest.json` and reports anchor status. When
`manifest.ots` sits in the same directory, proof verification requires the
optional `[ots]` extra.

## Example output

```
content_hash: a1b2c3…
cid:          bafybeig…
claim_level:  bytes
anchor:       Bitcoin height 840000
```

Or, when the timestamp is still pending:

```
content_hash: a1b2c3…
anchor:       pending on public OpenTimestamps calendars: alice.btc.calendar.opentimestamps.org (unverified claim; not yet Bitcoin-attested)
```

`claim_level` is always reported from the manifest structure alone (no artifact
rehash, no network call):

| Level | Meaning |
|---|---|
| `bytes` | Content hash declared; no receipt or derivation rules |
| `derived_declared` | Derivation rules recorded but not confirmed here |
| *(not emitted by `check`)* `bitwise_declared` | Reproduction receipt status requires `verify` |
| *(not emitted by `check`)* `bitwise+derived_declared` | Receipt + derivation status requires `verify` |

To **validate** artifact hashes and receipts, use the full `verify` command
(requires the artifact files):

```bash
uvx farm-notary verify --run-dir path/to/run
```

## What is and is not checked

| Checked by `check` | Not checked by `check` |
|---|---|
| Content hash is internally consistent | Artifact files match the hashes |
| Anchor hash matches the content hash | Reproduction receipt is valid |
| OTS proof commits to the content hash (requires `[ots]`) | Identity signature (public key not required) |
| Claim level from manifest structure | Scientific correctness |

`check` never invents trust: if the OTS extra is missing it reports that and
exits 0 rather than falsely claiming the proof was verified.

## OTS proof verification

`check` verifies the OTS proof when the `[ots]` extra is installed:

```bash
uvx "farm-notary[ots]" check --manifest path/to/manifest.json
```

Without the extra the proof is reported as present but unverified.

## Offline check (archival)

If you have a downloaded run directory and no network:

```bash
uvx farm-notary check --manifest path/to/run/manifest.json
# OTS proof check is local (no network needed once proof is Bitcoin-attested)
```

The hash check and claim-level display are fully offline. The OTS proof check
reads the `.ots` file locally, but OpenTimestamps proofs do not embed Bitcoin
headers and `check` does not validate attestations against the Bitcoin chain.

## Full verify (for archival / deeper review)

```bash
uvx "farm-notary[ots]" verify --run-dir path/to/run
```

This rehashes all declared artifacts, verifies the anchor proof, and prints a
full CLAIMS.md claim card.  Exit 0 means every attempted check passed; it does
not mean the science is correct.
