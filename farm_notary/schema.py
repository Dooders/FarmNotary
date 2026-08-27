"""Canonical manifest fields.

Keep this small. Chain storage is the hash of the manifest, not the artifacts.
"""

MANIFEST_VERSION = "farmnotary.manifest.v1"

REQUIRED_KEYS = (
    "schema",
    "created_utc",
    "git_sha",
    "config",
    "artifacts",
    "artifact_hashes",
)

# Never hash or upload these names from a run directory.
PRIVATE_NAME_FRAGMENTS = (
    "ballot",
    "vote",
    "voter",
    "individual_choice",
    "private",
)
