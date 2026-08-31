"""FarmNotary: attest AgentFarm runs without executing them on-chain."""

from farm_notary.anchor import (
    AnchorReceipt,
    anchor_run,
    get_backend,
    notarize_run,
    write_proof,
)
from farm_notary.campaign import Campaign, build_campaign, load_campaign, write_campaign
from farm_notary.claims import infer_claim_level
from farm_notary.diagnose import MismatchDiagnosis, diagnose_mismatch
from farm_notary.ladder import LadderResult, evaluate_ladder
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
from farm_notary.profiles import PUBLISH_PROFILES, PublishProfile, get_profile
from farm_notary.reproduce import ReproductionResult, reproduce_run
from farm_notary.schema import MANIFEST_VERSION, TOOL_VERSION
from farm_notary.schema import TOOL_VERSION as __version__
from farm_notary.withheld import (
    WithheldCommitment,
    reveal_withheld,
    verify_reveal,
)
from farm_notary.verify import (
    ClaimCard,
    evaluate_claims,
    verify_anchor,
    verify_derived_artifacts,
    verify_identity_record,
    verify_receipt,
    verify_run_dir,
)

__all__ = [
    "AnchorReceipt",
    "Campaign",
    "ClaimCard",
    "DirtyTreeError",
    "LadderResult",
    "MANIFEST_VERSION",
    "Manifest",
    "MismatchDiagnosis",
    "PUBLISH_PROFILES",
    "PublishProfile",
    "ReproductionResult",
    "TOOL_VERSION",
    "WithheldCommitment",
    "__version__",
    "anchor_run",
    "build_campaign",
    "build_manifest",
    "capture_environment",
    "diagnose_mismatch",
    "evaluate_claims",
    "evaluate_ladder",
    "get_backend",
    "get_profile",
    "hash_file",
    "hash_json",
    "infer_claim_level",
    "load_campaign",
    "load_manifest",
    "notarize_run",
    "reproduce_run",
    "reveal_withheld",
    "verify_anchor",
    "verify_derived_artifacts",
    "verify_identity_record",
    "verify_receipt",
    "verify_reveal",
    "verify_run_dir",
    "write_campaign",
    "write_manifest",
    "write_proof",
]
