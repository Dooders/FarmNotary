"""Unsigned interop vocabulary exports.

Dual-write helpers that translate a :class:`~farm_notary.manifest.Manifest`
into well-known *vocabularies* so a reader who already speaks those
formats can find the FarmNotary content hash. They are **not**
verifiable provenance:

* SLSA / in-toto JSON is not a DSSE envelope and is not signed.
* C2PA output is a JSON summary, not a JUMBF manifest store.
* None of these files appear on the claim card.

Filenames carry ``.unsigned.json`` (except the RO-Crate metadata name,
which is fixed by the spec). Every document also records
``farmnotary_interop.status = unsigned-summary-not-for-verification``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from farm_notary.manifest import Manifest

# ---------------------------------------------------------------------------
# SLSA / in-toto
# ---------------------------------------------------------------------------

_SLSA_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
_SLSA_PREDICATE_TYPE = "https://slsa.dev/provenance/v1"

INTEROP_STATUS = "unsigned-summary-not-for-verification"
SLSA_FILE_NAME = "slsa-provenance.unsigned.json"
ROCRATE_FILE_NAME = "ro-crate-metadata.json"
C2PA_FILE_NAME = "c2pa-claim-summary.unsigned.json"

_INTEROP_NOTE = (
    "Unsigned JSON vocabulary export. Not a DSSE envelope, not a C2PA "
    "JUMBF, and not a claim-ladder row."
)


def _unsigned_mark() -> Dict[str, Any]:
    return {
        "farmnotary_interop": {
            "status": INTEROP_STATUS,
            "note": _INTEROP_NOTE,
        }
    }


def _is_full_git_sha(value: str) -> bool:
    return len(value) == 40 and all(c in "0123456789abcdefABCDEF" for c in value)


def to_slsa_provenance(manifest: "Manifest") -> Dict[str, Any]:
    """Return an in-toto Statement containing a SLSA provenance predicate.

    The statement is not signed. Callers that want a DSSE envelope must
    wrap the serialised bytes themselves. The returned dict is marked
    ``unsigned-summary-not-for-verification``.

    Parameters
    ----------
    manifest:
        A :class:`~farm_notary.manifest.Manifest` instance (already built).

    Returns
    -------
    dict
        Serialisable in-toto Statement with a SLSA v1 provenance predicate.
    """
    subjects = [
        {
            "name": path,
            "digest": {"sha256": digest.removeprefix("sha256:")},
        }
        for path, digest in sorted(manifest.artifact_hashes.items())
    ]

    build_type = "https://github.com/Dooders/FarmNotary/build-types/local@v1"
    # Use a stable workflow/builder identity, not a per-run URL.
    builder_id = "https://github.com/Dooders/FarmNotary"

    ci_prov = manifest.ci_provenance or {}
    invocation_id: Optional[str] = None
    if ci_prov.get("kind") == "github_actions":
        build_type = "https://github.com/Dooders/FarmNotary/build-types/github-actions@v1"
        # builder.id must identify the stable workflow, not the individual run.
        repo = ci_prov.get("repository", "")
        workflow = ci_prov.get("workflow", "")
        if repo and workflow:
            builder_id = f"https://github.com/{repo}/actions/workflows/{workflow}"
        elif repo:
            builder_id = f"https://github.com/{repo}/actions"
        # Per-run URL belongs in invocationId, not builder.id.
        invocation_id = ci_prov.get(
            "run_url",
            (
                f"https://github.com/{repo}/actions/runs/{ci_prov.get('run_id', '')}"
                if repo
                else None
            ),
        )

    # SLSA v1 runDetails.metadata fields.
    metadata: Dict[str, Any] = {
        "finishedOn": manifest.created_utc,
    }
    if invocation_id:
        metadata["invocationId"] = invocation_id

    run_details: Dict[str, Any] = {
        "builder": {"id": builder_id},
        "metadata": metadata,
    }

    # Build the externalParameters block; CI inputs belong here, not in metadata.
    external_params: Dict[str, Any] = {
        "config": manifest.config,
        "publish_patterns": manifest.publish_patterns,
    }
    if ci_prov:
        external_params["ci"] = ci_prov

    # resolvedDependencies: include the Git revision as a resolved dependency.
    resolved_deps: List[Dict[str, Any]] = []
    if manifest.git_sha and _is_full_git_sha(manifest.git_sha):
        repo = ci_prov.get("repository", "") if ci_prov else ""
        dep: Dict[str, Any] = {"digest": {"sha1": manifest.git_sha}}
        if repo:
            dep["uri"] = f"git+https://github.com/{repo}"
        resolved_deps.append(dep)

    build_definition: Dict[str, Any] = {
        "buildType": build_type,
        "externalParameters": external_params,
        "internalParameters": {
            "farmnotary_schema": manifest.schema,
            "content_hash": manifest.content_hash(),
        },
    }
    if resolved_deps:
        build_definition["resolvedDependencies"] = resolved_deps

    predicate: Dict[str, Any] = {
        "buildDefinition": build_definition,
        "runDetails": run_details,
    }

    if manifest.beacon:
        predicate["buildDefinition"]["externalParameters"]["beacon"] = manifest.beacon

    statement: Dict[str, Any] = {
        "_type": _SLSA_STATEMENT_TYPE,
        "predicateType": _SLSA_PREDICATE_TYPE,
        "subject": subjects,
        "predicate": predicate,
    }
    statement.update(_unsigned_mark())
    return statement


def emit_slsa(manifest: "Manifest", run_dir: Path) -> Path:
    """Write ``slsa-provenance.unsigned.json`` next to ``manifest.json``.

    Parameters
    ----------
    manifest:
        Built manifest for *run_dir*.
    run_dir:
        Directory that contains ``manifest.json``.

    Returns
    -------
    Path
        Path to the written file.
    """
    dest = Path(run_dir) / SLSA_FILE_NAME
    dest.write_text(
        json.dumps(to_slsa_provenance(manifest), indent=2) + "\n",
        encoding="utf-8",
    )
    return dest


# ---------------------------------------------------------------------------
# RO-Crate
# ---------------------------------------------------------------------------

_ROCRATE_CONTEXT = "https://w3id.org/ro/crate/1.1/context"


def to_ro_crate(manifest: "Manifest", *, crate_id: str = "./") -> Dict[str, Any]:
    """Return a minimal RO-Crate metadata document for *manifest*.

    The returned dict can be serialised as ``ro-crate-metadata.json``.

    Parameters
    ----------
    manifest:
        A built :class:`~farm_notary.manifest.Manifest` instance.
    crate_id:
        The ``@id`` of the root dataset entity (default ``"./"``, i.e. the
        directory containing the crate).

    Returns
    -------
    dict
        Serialisable RO-Crate metadata document.
    """
    content_hash = manifest.content_hash()

    parts = [
        {
            "@id": path,
            "@type": "File",
            "name": path,
            "sha256": digest.removeprefix("sha256:"),
        }
        for path, digest in sorted(manifest.artifact_hashes.items())
    ]

    root_dataset: Dict[str, Any] = {
        "@id": crate_id,
        "@type": "Dataset",
        "datePublished": manifest.created_utc,
        "description": "FarmNotary run artifact crate",
        "hasPart": [{"@id": p["@id"]} for p in parts],
        "farmnotary:contentHash": content_hash,
        "farmnotary:schema": manifest.schema,
    }

    if manifest.git_sha:
        root_dataset["version"] = manifest.git_sha

    if manifest.cid:
        root_dataset["identifier"] = f"ipfs:{manifest.cid}"
    root_dataset.update(_unsigned_mark())

    metadata_entity = {
        "@id": "ro-crate-metadata.json",
        "@type": "CreativeWork",
        "about": {"@id": crate_id},
        "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"},
    }

    return {
        "@context": _ROCRATE_CONTEXT,
        "@graph": [metadata_entity, root_dataset] + parts,
    }


def emit_ro_crate(manifest: "Manifest", run_dir: Path) -> Path:
    """Write ``ro-crate-metadata.json`` next to ``manifest.json``.

    Parameters
    ----------
    manifest:
        Built manifest for *run_dir*.
    run_dir:
        Directory that contains ``manifest.json``.

    Returns
    -------
    Path
        Path to the written file.
    """
    dest = Path(run_dir) / ROCRATE_FILE_NAME
    dest.write_text(
        json.dumps(to_ro_crate(manifest, crate_id="./"), indent=2) + "\n",
        encoding="utf-8",
    )
    return dest


# ---------------------------------------------------------------------------
# C2PA (JSON claim summary)
# ---------------------------------------------------------------------------

def to_c2pa_claim(manifest: "Manifest") -> Dict[str, Any]:
    """Return a minimal C2PA-style claim summary for *manifest*.

    This is a JSON vocabulary summary, not a C2PA JUMBF manifest store
    and not a signed claim. It will not pass a C2PA validator.

    Parameters
    ----------
    manifest:
        A built :class:`~farm_notary.manifest.Manifest` instance.

    Returns
    -------
    dict
        Serialisable C2PA claim summary.
    """
    content_hash = manifest.content_hash()

    assertions = [
        {
            "label": "c2pa.hash.data",
            "data": {
                "alg": "sha256",
                "hash": content_hash,
                "name": "farmnotary:content_hash",
            },
        }
    ]

    for path, digest in sorted(manifest.artifact_hashes.items()):
        assertions.append(
            {
                "label": "c2pa.hash.data",
                "data": {
                    "alg": "sha256",
                    "hash": digest.removeprefix("sha256:"),
                    "name": path,
                },
            }
        )

    claim: Dict[str, Any] = {
        "claim_generator": f"FarmNotary/{manifest.farm_notary_version or 'unknown'}",
        "title": "FarmNotary run attestation",
        "dc:format": "application/json",
        "created": manifest.created_utc,
        "assertions": assertions,
        "farmnotary": {
            "schema": manifest.schema,
            "content_hash": content_hash,
        },
    }
    claim.update(_unsigned_mark())

    if manifest.git_sha:
        claim["farmnotary"]["git_sha"] = manifest.git_sha

    if manifest.anchor:
        claim["farmnotary"]["anchor"] = manifest.anchor

    return claim


def emit_c2pa(manifest: "Manifest", run_dir: Path) -> Path:
    """Write ``c2pa-claim-summary.unsigned.json`` next to ``manifest.json``.

    Parameters
    ----------
    manifest:
        Built manifest for *run_dir*.
    run_dir:
        Directory that contains ``manifest.json``.

    Returns
    -------
    Path
        Path to the written file.
    """
    dest = Path(run_dir) / C2PA_FILE_NAME
    dest.write_text(
        json.dumps(to_c2pa_claim(manifest), indent=2) + "\n",
        encoding="utf-8",
    )
    return dest


# ---------------------------------------------------------------------------
# Convenience: emit all formats at once
# ---------------------------------------------------------------------------

INTEROP_FORMATS = ("slsa", "ro-crate", "c2pa")


def emit_interop(
    manifest: "Manifest",
    run_dir: Path,
    *,
    formats: Optional[tuple] = None,
) -> Dict[str, Path]:
    """Emit one or more interop files next to ``manifest.json``.

    Parameters
    ----------
    manifest:
        Built manifest for *run_dir*.
    run_dir:
        Directory that contains ``manifest.json``.
    formats:
        Tuple of format names to emit.  Defaults to all supported formats:
        ``("slsa", "ro-crate", "c2pa")``.

    Returns
    -------
    dict
        Mapping of format name → written path.
    """
    if formats is None:
        formats = INTEROP_FORMATS
    emitters = {
        "slsa": emit_slsa,
        "ro-crate": emit_ro_crate,
        "c2pa": emit_c2pa,
    }
    result: Dict[str, Path] = {}
    for fmt in formats:
        if fmt not in emitters:
            raise ValueError(f"unknown interop format {fmt!r}; choose from {INTEROP_FORMATS}")
        result[fmt] = emitters[fmt](manifest, run_dir)
    return result
