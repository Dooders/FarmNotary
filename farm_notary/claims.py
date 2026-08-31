"""Claim levels and scoped reproducibility sentences.

A claim level is a label for what was checked — never a score or rank.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence

from farm_notary.fingerprint import environment_scope
from farm_notary.manifest import RECEIPT_NAME

CLAIM_BYTES = "bytes"
CLAIM_DERIVED = "derived"
CLAIM_BITWISE = "bitwise"
CLAIM_BITWISE_DERIVED = "bitwise+derived"
# "declared" variants indicate the relevant artefact exists but has not been
# validated (e.g. receipt not bound to this manifest, or derivation unrun).
CLAIM_DERIVED_DECLARED = "derived_declared"
CLAIM_BITWISE_DECLARED = "bitwise_declared"
CLAIM_BITWISE_DERIVED_DECLARED = "bitwise+derived_declared"


def infer_claim_level(record: Any, run_dir: Optional[Path] = None) -> str:
    """Infer a claim level from a manifest or campaign plus optional receipts.

    Labels ending in ``_declared`` mean the corresponding artefact (receipt or
    derivation rules) is present but has **not** been validated against this
    record's content hash.  Only fully-validated results use the plain labels.
    """
    has_derived = bool(getattr(record, "derived_from", None))
    if not has_derived:
        config = getattr(record, "config", None) or {}
        notary = config.get("notary") if isinstance(config, dict) else None
        if isinstance(notary, dict) and notary.get("derived_from"):
            has_derived = True
    # Campaigns list children; a parent with runs is still a byte-level record
    # unless a reproduction receipt sits next to it.
    has_receipt = False
    receipt_valid = False
    if run_dir is not None:
        receipt_path = Path(run_dir) / RECEIPT_NAME
        if receipt_path.is_file():
            try:
                from farm_notary.reproduce import load_receipt

                receipt = load_receipt(run_dir)
                # Receipt must reference this record's content hash and succeed.
                manifest_hash = record.content_hash() if callable(getattr(record, "content_hash", None)) else None
                receipt_ok = bool(receipt.get("ok"))
                hash_match = (
                    manifest_hash is not None
                    and receipt.get("original_manifest_hash") == manifest_hash
                )
                has_receipt = True
                receipt_valid = receipt_ok and hash_match
            except (ValueError, OSError, KeyError):
                has_receipt = False
                receipt_valid = False
    if has_receipt and has_derived:
        return CLAIM_BITWISE_DERIVED_DECLARED
    if has_receipt:
        return CLAIM_BITWISE if receipt_valid else CLAIM_BITWISE_DECLARED
    if has_derived:
        return CLAIM_DERIVED_DECLARED
    return CLAIM_BYTES


def scoped_reproducibility_sentence(
    record: Any,
    *,
    derived_ok: Optional[bool] = None,
    experiment: Optional[str] = None,
) -> str:
    """One paragraph a paper can paste: what is claimed, on which machine class."""
    env = getattr(record, "environment", None) or {}
    scope = environment_scope(env)
    name = experiment or getattr(record, "name", None) or getattr(record, "runner", None)
    subject = f"the {name} record" if name else "this record"

    runs = getattr(record, "runs", None)
    if runs:
        seeds = []
        for run in runs:
            if not isinstance(run, dict):
                continue
            seed = run.get("seed")
            if seed is not None:
                seeds.append(seed)
        seed_bit = ""
        if seeds:
            if _is_contiguous_int_range(seeds):
                seed_bit = f" seeds {min(seeds)}…{max(seeds)}"
            else:
                seed_bit = f" seeds {', '.join(str(s) for s in seeds)}"
        config_hash = getattr(record, "config_hash", None)
        hash_bit = f" sharing config hash {config_hash}" if config_hash else ""
        return (
            f"A sweep of {len(runs)} runs{seed_bit}{hash_bit} is listed by "
            f"child CID and content hash. Bitwise identity, where claimed, is "
            f"scoped to {scope}; a 1-ulp difference on a different OS, arch, "
            f"or BLAS does not refute the published record."
        )

    config = getattr(record, "config", None) or {}
    seed = None
    for key in ("seed", "rng_seed", "random_seed"):
        if key in config:
            seed = config[key]
            break
    seed_bit = f" from seed {seed}" if seed is not None else ""

    derived_bit = ""
    rules = getattr(record, "derived_from", None) or []
    if not rules and isinstance(config, dict):
        notary = config.get("notary") or {}
        if isinstance(notary, dict):
            rules = notary.get("derived_from") or []
    if rules:
        from farm_notary.derive import derived_outputs, derived_sources

        outputs = ", ".join(f"`{n}`" for n in derived_outputs(rules))
        sources = ", ".join(f"`{n}`" for n in derived_sources(rules))
        if derived_ok is False:
            derived_bit = (
                f" Derivation rules are recorded ({outputs} from {sources}) "
                f"but were not confirmed on this check."
            )
        elif derived_ok is True:
            derived_bit = (
                f" Derived statistics ({outputs}) recompute exactly from {sources}."
            )
        else:
            # derived_ok is None: rules are declared but not yet run
            derived_bit = (
                f" Derivation rules are recorded ({outputs} from {sources}) "
                f"but have not been confirmed (pass --verify-derived to check)."
            )

    return (
        f"The published artifacts of {subject}{seed_bit} are a tamper-evident "
        f"byte record. Bitwise reproducibility is scoped to {scope}; a 1-ulp "
        f"difference on a different OS, arch, or BLAS does not refute the "
        f"published hashes.{derived_bit}"
    )


def _is_contiguous_int_range(seeds: Sequence[Any]) -> bool:
    try:
        ints = sorted(int(s) for s in seeds)
    except (TypeError, ValueError):
        return False
    if len(ints) < 2:
        return False
    return ints == list(range(ints[0], ints[-1] + 1))
