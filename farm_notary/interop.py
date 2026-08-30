"""Interop schema emission.

Dual-write helpers that translate a :class:`~farm_notary.manifest.Manifest`
into well-known provenance formats so verifiers that already speak those
vocabularies do not need to learn ``farmnotary.manifest.v1``.

Supported targets
-----------------
* **SLSA / in-toto** – ``https://slsa.dev/provenance/v1`` predicate wrapped
  in an in-toto ``Statement``.  The FarmNotary content hash is placed in
  ``buildDefinition.internalParameters.content_hash``; every artifact hash
  is listed as a ``subject``.
* **RO-Crate** – minimal ``ro-crate-metadata.json`` (schema.org/Dataset).
* **C2PA** – minimal JSON representation of a C2PA-style claim (not a binary
  JUMBF; useful for tooling that consumes JSON claim summaries).

All functions are **pure** (no I/O) and return plain dicts that callers can
serialize to JSON.  The ``emit_*`` helpers additionally write the file next
to ``manifest.json`` and return the destination path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from farm_notary.manifest import Manifest

# ---------------------------------------------------------------------------
# SLSA / in-toto
# ---------------------------------------------------------------------------

_SLSA_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
_SLSA_PREDICATE_TYPE = "https://slsa.dev/provenance/v1"


def to_slsa_provenance(manifest: "Manifest") -> Dict[str, Any]:
    """Return an in-toto Statement containing a SLSA provenance predicate.

    The statement is not signed here; callers that want a DSSE envelope
    should wrap the serialised bytes themselves.

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
    builder_id = "https://github.com/Dooders/FarmNotary"

    ci_prov = manifest.ci_provenance or {}
    if ci_prov.get("kind") == "github_actions":
        build_type = "https://github.com/Dooders/FarmNotary/build-types/github-actions@v1"
        builder_id = ci_prov.get(
            "run_url",
            f"https://github.com/{ci_prov.get('repository', '')}/actions/runs/{ci_prov.get('run_id', '')}",
        )

    run_details: Dict[str, Any] = {
        "builder": {"id": builder_id},
        "metadata": {
            "buildFinishedOn": manifest.created_utc,
            "reproducible": True,
        },
    }

    if manifest.git_sha:
        run_details["buildConfig"] = {
            "ref": manifest.git_sha,
            "config": manifest.config,
        }

    if ci_prov:
        run_details["metadata"]["externalParameters"] = ci_prov

    predicate: Dict[str, Any] = {
        "buildDefinition": {
            "buildType": build_type,
            "externalParameters": {
                "config": manifest.config,
                "publish_patterns": manifest.publish_patterns,
            },
            "internalParameters": {
                "farmnotary_schema": manifest.schema,
                "content_hash": manifest.content_hash(),
            },
        },
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
    return statement


def emit_slsa(manifest: "Manifest", run_dir: Path) -> Path:
    """Write ``slsa-provenance.json`` next to ``manifest.json``.

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
    dest = Path(run_dir) / "slsa-provenance.json"
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
    dest = Path(run_dir) / "ro-crate-metadata.json"
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

    This is a JSON representation of a C2PA claim, not a binary JUMBF
    structure.  It is intended for tooling that consumes JSON claim summaries
    and for interop with systems such as Atlas that independently adopted C2PA.

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

    if manifest.git_sha:
        claim["farmnotary"]["git_sha"] = manifest.git_sha

    if manifest.anchor:
        claim["farmnotary"]["anchor"] = manifest.anchor

    return claim


def emit_c2pa(manifest: "Manifest", run_dir: Path) -> Path:
    """Write ``c2pa-claim.json`` next to ``manifest.json``.

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
    dest = Path(run_dir) / "c2pa-claim.json"
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
