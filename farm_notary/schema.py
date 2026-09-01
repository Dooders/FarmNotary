"""Canonical manifest fields.

Keep this small. Chain storage is the hash of the manifest, not the artifacts.
"""

TOOL_VERSION = "1.0.0"

MANIFEST_VERSION = "farmnotary.manifest.v1"
CAMPAIGN_VERSION = "farmnotary.campaign.v1"
REGISTRY_VERSION = "farmnotary.registry.v1"

REQUIRED_KEYS = (
    "schema",
    "created_utc",
    "git_sha",
    "config",
    "artifacts",
    "artifact_hashes",
    "publish_patterns",
    "unmatched_count",
)

# Belt-and-braces denylist applied *after* the allowlist (including every
# named experiment-type profile in farm_notary.profiles).
# Files matching these fragments are never hashed, listed, or uploaded even
# if a publish pattern would otherwise admit them.
PRIVATE_NAME_FRAGMENTS = (
    "ballot",
    "vote",
    "voter",
    "individual_choice",
    "private",
)
