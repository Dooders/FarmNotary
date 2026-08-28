# EAS anchoring (experimental)

> **Experimental.** EAS anchoring requires a funded private key, costs gas,
> and introduces an attester-address trust problem that OTS avoids.  Prefer
> `--backend ots` for most use cases.  See the tradeoff note below.

The `eas` backend attests `(manifest_hash, cid)` on
[EAS](https://attest.org), which is a predeploy on Base and Base Sepolia
(contract `0x4200...0021` on both).

## When to prefer EAS over OTS

| | OTS | EAS |
|---|---|---|
| Keys required | none | funded attester key |
| Gas cost | none | yes (Base) |
| Trust model | Bitcoin — verifier trusts the chain, not you | attester address — verifier must know and trust your address |
| Queryable on-chain | no | yes (EASScan, composable) |
| Bitcoin-backed | yes | no |

Use EAS when you need attributable, queryable, on-chain attestations and your
verifiers already know your attester address.  Use OTS when you want a
Bitcoin-backed proof with no keys, no gas, and no prior relationship with the
verifier.

## Schema

Schema (non-revocable, no resolver):

```text
bytes32 manifestHash,string cid
```

Its UID is derived deterministically and is the same on every chain:

```text
0xc3d61e7073e9dcc59f65fe1a8a4bfd0b3e2c5fd2e32ad1c1d6c473fb1274ac08
```

## Configuration

| Variable | Meaning | Default |
| --- | --- | --- |
| `FARM_NOTARY_CHAIN` | `base` or `base-sepolia` | `base-sepolia` |
| `FARM_NOTARY_PRIVATE_KEY` | Attester key (funded with a little ETH for gas) | required |
| `FARM_NOTARY_RPC_URL` | JSON-RPC endpoint | public RPC for the chain |
| `FARM_NOTARY_EAS_SCHEMA_UID` | Schema UID to attest against | the UID above |
| `FARM_NOTARY_EAS_ADDRESS` | EAS contract | OP-stack predeploy |

## Usage

One-time per chain, register the schema, then anchor runs:

```bash
pip install -e ".[chain]"   # adds web3

python -m farm_notary.cli register-schema
farm-notary anchor --run-dir path/to/run --backend eas --cid <cid>
```

A successful anchor writes the receipt (tx hash, attestation UID, chain id)
back into `manifest.json` and prints an [EASScan](https://base-sepolia.easscan.org)
link.

## Trust model

The attester address is the trust anchor: attestations only mean something to
verifiers who know which address is yours.  Publish your attester address (and
the schema UID) wherever you publish results, and keep attesting from that
address.  Verifiers then check: look up the attestation on EASScan, confirm the
attester, fetch the CID, rehash with `farm-notary verify`, and compare to the
attested `manifestHash`.
