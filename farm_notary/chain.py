"""Multi-stage provenance chain.

When AgentFarm (or any other pipeline) stages feed each other, a flat
per-run manifest cannot express lineage.  This module provides:

* :func:`chain_manifests` — link a sequence of manifests into a directed
  chain by hashing each stage's output attestation into the next stage's
  input attestation.

* :class:`ChainLink` — lightweight dataclass that records one stage's
  position in a chain.

* :func:`load_chain` / :func:`write_chain` — persist the chain as
  ``provenance-chain.json`` next to the final-stage manifest.

Design
------
The chain is a JSON array where each element is::

    {
        "stage":          <int or str>,
        "manifest_path":  <relative path | null>,
        "content_hash":   <sha256 hex of the manifest body>,
        "parent_hash":    <link_hash of the previous link | null for root>,
        "link_hash":      <sha256(parent_hash + content_hash)>,
    }

``link_hash`` commits to the entire history up to this point, so a verifier
can walk from the root to the tip and re-derive every hash without trusting
any intermediate storage.  There is no trusted server in this design.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union


# ---------------------------------------------------------------------------
# ChainLink
# ---------------------------------------------------------------------------


@dataclass
class ChainLink:
    """One node in a provenance chain.

    Attributes
    ----------
    stage:
        Human-readable stage label (``0``, ``1``, ``"preprocess"``, …).
    manifest_path:
        Relative path to the manifest file, or ``None`` for a synthetic node.
    content_hash:
        SHA-256 hash of the manifest body (from
        :meth:`~farm_notary.manifest.Manifest.content_hash`).
    parent_hash:
        ``link_hash`` of the previous link, or ``None`` for the root.
    link_hash:
        Commitment over the full lineage: ``sha256(parent_hash + content_hash)``.
        For the root, ``sha256("" + content_hash)``.
    """

    stage: Union[int, str]
    manifest_path: Optional[str]
    content_hash: str
    parent_hash: Optional[str]
    link_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChainLink":
        return cls(
            stage=data["stage"],
            manifest_path=data.get("manifest_path"),
            content_hash=data["content_hash"],
            parent_hash=data.get("parent_hash"),
            link_hash=data["link_hash"],
        )


# ---------------------------------------------------------------------------
# Core chain operations
# ---------------------------------------------------------------------------


def _link_hash(parent_hash: Optional[str], content_hash: str) -> str:
    prefix = parent_hash or ""
    return hashlib.sha256((prefix + content_hash).encode()).hexdigest()


def chain_manifests(
    manifests: Sequence[Any],
    *,
    stage_labels: Optional[Sequence[Union[int, str]]] = None,
    manifest_paths: Optional[Sequence[Optional[str]]] = None,
) -> List[ChainLink]:
    """Link a sequence of manifests into a provenance chain.

    Parameters
    ----------
    manifests:
        Ordered sequence of :class:`~farm_notary.manifest.Manifest` objects
        (earliest/root stage first).
    stage_labels:
        Optional labels for each stage.  Defaults to ``0, 1, 2, …``.
    manifest_paths:
        Optional path strings for each manifest file (for
        human-readable cross-referencing in the chain JSON).  May be
        absolute or relative paths.

    Returns
    -------
    list of ChainLink
        Chain from root to tip.  Each link's ``parent_hash`` is the previous
        link's ``link_hash``; each ``link_hash`` is a commitment over the
        full history.
    """
    if not manifests:
        return []

    labels = list(stage_labels) if stage_labels else list(range(len(manifests)))
    paths = list(manifest_paths) if manifest_paths else [None] * len(manifests)

    if len(labels) != len(manifests):
        raise ValueError(
            f"stage_labels length {len(labels)} != manifests length {len(manifests)}"
        )
    if len(paths) != len(manifests):
        raise ValueError(
            f"manifest_paths length {len(paths)} != manifests length {len(manifests)}"
        )

    chain: List[ChainLink] = []
    parent_hash: Optional[str] = None

    for label, manifest, path in zip(labels, manifests, paths):
        ch = manifest.content_hash()
        lh = _link_hash(parent_hash, ch)
        chain.append(
            ChainLink(
                stage=label,
                manifest_path=path,
                content_hash=ch,
                parent_hash=parent_hash,
                link_hash=lh,
            )
        )
        parent_hash = lh

    return chain


# ---------------------------------------------------------------------------
# Verify chain integrity
# ---------------------------------------------------------------------------


def verify_chain(
    chain: Sequence[ChainLink], *, chain_dir: Optional[Path] = None
) -> List[str]:
    """Verify the integrity of a provenance chain.

    Re-derives every ``link_hash`` from ``parent_hash`` + ``content_hash``
    and checks that consecutive links' ``parent_hash`` values are consistent.
    When *chain_dir* is given, each link whose ``manifest_path`` is set is
    loaded from disk and its content is hashed to confirm it matches the
    recorded ``content_hash`` — preventing an attacker from swapping a
    referenced manifest without updating the chain.

    Parameters
    ----------
    chain:
        Sequence of :class:`ChainLink` objects in order (root first).
    chain_dir:
        Optional base directory used to resolve relative ``manifest_path``
        values.  When omitted, on-disk manifest verification is skipped and
        a warning is appended for each link that has a ``manifest_path``.

    Returns
    -------
    list of str
        List of error messages; empty list means the chain is intact.
    """
    errors: List[str] = []
    prev_link_hash: Optional[str] = None

    for i, link in enumerate(chain):
        # Check parent_hash consistency.
        if i == 0:
            if link.parent_hash is not None:
                errors.append(
                    f"link {i} (stage {link.stage!r}): root link must have parent_hash=null"
                )
        else:
            if link.parent_hash != prev_link_hash:
                errors.append(
                    f"link {i} (stage {link.stage!r}): parent_hash mismatch "
                    f"(expected {prev_link_hash!r}, got {link.parent_hash!r})"
                )

        # Re-derive link_hash.
        expected_lh = _link_hash(link.parent_hash, link.content_hash)
        if link.link_hash != expected_lh:
            errors.append(
                f"link {i} (stage {link.stage!r}): link_hash mismatch "
                f"(expected {expected_lh!r}, got {link.link_hash!r})"
            )

        # Verify the referenced manifest file if a base directory was given.
        if link.manifest_path:
            if chain_dir is not None:
                manifest_file = (Path(chain_dir) / link.manifest_path).resolve()
                if not manifest_file.exists():
                    errors.append(
                        f"link {i} (stage {link.stage!r}): manifest_path "
                        f"{link.manifest_path!r} not found"
                    )
                else:
                    try:
                        from farm_notary.manifest import load_manifest

                        m = load_manifest(manifest_file)
                        actual_ch = m.content_hash()
                    except Exception as exc:
                        errors.append(
                            f"link {i} (stage {link.stage!r}): could not load "
                            f"manifest {link.manifest_path!r}: {exc}"
                        )
                    else:
                        if actual_ch != link.content_hash:
                            errors.append(
                                f"link {i} (stage {link.stage!r}): manifest "
                                f"content_hash mismatch — manifest on disk has "
                                f"{actual_ch!r}, chain records {link.content_hash!r}"
                            )
            else:
                errors.append(
                    f"link {i} (stage {link.stage!r}): manifest_path "
                    f"{link.manifest_path!r} not verified (no chain_dir supplied)"
                )

        prev_link_hash = link.link_hash

    return errors


# ---------------------------------------------------------------------------
# Persist / load chain
# ---------------------------------------------------------------------------

CHAIN_FILE_NAME = "provenance-chain.json"


def write_chain(chain: Sequence[ChainLink], dest_dir: Path) -> Path:
    """Write *chain* as ``provenance-chain.json`` inside *dest_dir*.

    Parameters
    ----------
    chain:
        Ordered sequence of :class:`ChainLink` objects.
    dest_dir:
        Directory where the file is written (typically the final stage's run
        directory, but can be any shared output location).

    Returns
    -------
    Path
        Path to the written file.
    """
    dest = Path(dest_dir) / CHAIN_FILE_NAME
    data = [link.to_dict() for link in chain]
    dest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return dest


def load_chain(path: Path) -> List[ChainLink]:
    """Load a provenance chain from *path*.

    Parameters
    ----------
    path:
        Path to a ``provenance-chain.json`` file.

    Returns
    -------
    list of ChainLink
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [ChainLink.from_dict(item) for item in raw]


# ---------------------------------------------------------------------------
# CLI-friendly helper
# ---------------------------------------------------------------------------


def chain_run_dirs(
    run_dirs: Sequence[Union[str, Path]],
    *,
    stage_labels: Optional[Sequence[Union[int, str]]] = None,
    output_dir: Optional[Path] = None,
) -> List[ChainLink]:
    """Build and persist a provenance chain from a sequence of run directories.

    Loads ``manifest.json`` from each directory (in order), chains them, and
    writes ``provenance-chain.json`` to *output_dir* (defaulting to the last
    directory).

    Parameters
    ----------
    run_dirs:
        Ordered sequence of directories containing ``manifest.json``.
    stage_labels:
        Optional labels; defaults to 0, 1, 2, …
    output_dir:
        Where to write the chain file.  Defaults to the last *run_dir*.

    Returns
    -------
    list of ChainLink
        The full chain.
    """
    from farm_notary.manifest import load_manifest

    paths = [Path(d) for d in run_dirs]
    manifests = [load_manifest(p / "manifest.json") for p in paths]
    rel_paths = [str(p / "manifest.json") for p in paths]

    chain = chain_manifests(
        manifests,
        stage_labels=stage_labels,
        manifest_paths=rel_paths,
    )
    dest_dir = output_dir or paths[-1]
    write_chain(chain, dest_dir)
    return chain
