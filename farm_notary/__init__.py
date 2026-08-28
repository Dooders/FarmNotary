"""FarmNotary: attest AgentFarm runs without executing them on-chain."""

from farm_notary.schema import TOOL_VERSION as __version__

from farm_notary.anchor import (
    AnchorReceipt,
    anchor_run,
    get_backend,
    notarize_run,
    write_proof,
)
from farm_notary.campaign import Campaign, build_campaign, load_campaign, write_campaign
from farm_notary.manifest import (
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
    verify_anchor,
    verify_derived_artifacts,
    verify_identity_record,
    verify_receipt,
    verify_run_dir,
)

__all__ = [
    "AnchorReceipt",
    "Campaign",
    "Manifest",
    "ReproductionResult",
    "anchor_run",
    "build_campaign",
    "build_manifest",
    "capture_environment",
    "get_backend",
    "hash_file",
    "hash_json",
    "load_campaign",
    "load_manifest",
    "notarize_run",
    "reproduce_run",
    "verify_anchor",
    "verify_derived_artifacts",
    "verify_identity_record",
    "verify_receipt",
    "verify_run_dir",
    "write_campaign",
    "write_manifest",
    "write_proof",
    "__version__",
]
