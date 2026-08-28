"""FarmNotary: attest AgentFarm runs without executing them on-chain."""

from farm_notary.schema import TOOL_VERSION as __version__

from farm_notary.anchor import (
    AnchorReceipt,
    anchor_run,
    get_backend,
    notarize_run,
    write_proof,
)
from farm_notary.manifest import (
    DirtyTreeError,
    Manifest,
    build_manifest,
    capture_environment,
    hash_file,
    hash_json,
    load_manifest,
    write_manifest,
)
from farm_notary.reproduce import ReproductionResult, reproduce_run
from farm_notary.verify import (
    ClaimCard,
    evaluate_claims,
    verify_anchor,
    verify_receipt,
    verify_run_dir,
)

__all__ = [
    "AnchorReceipt",
    "ClaimCard",
    "DirtyTreeError",
    "Manifest",
    "ReproductionResult",
    "anchor_run",
    "build_manifest",
    "capture_environment",
    "evaluate_claims",
    "get_backend",
    "hash_file",
    "hash_json",
    "load_manifest",
    "notarize_run",
    "reproduce_run",
    "verify_anchor",
    "verify_receipt",
    "verify_run_dir",
    "write_manifest",
    "write_proof",
    "__version__",
]
