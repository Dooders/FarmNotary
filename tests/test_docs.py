"""Keep the published docs aligned with the CLI, schema, and claim card."""

import argparse
from pathlib import Path

from farm_notary.cli import _build_parser
from farm_notary.profiles import PROFILE_NAMES
from farm_notary.schema import REQUIRED_KEYS, TOOL_VERSION
from farm_notary.scope import ALLOWED_SENTENCE
from farm_notary.verify import _CLAIM_NAMES

README = Path("README.md").read_text(encoding="utf-8")
CLAIMS = Path("docs/CLAIMS.md").read_text(encoding="utf-8")
DESIGN = Path("docs/DESIGN.md").read_text(encoding="utf-8")
ACTION = Path("docs/ACTION.md").read_text(encoding="utf-8")
PRINCIPLES = Path("docs/PRINCIPLES.md").read_text(encoding="utf-8")


def _cli_commands() -> list[str]:
    parser = _build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return sorted(action.choices)
    raise AssertionError("farm-notary parser has no subcommands")


def test_readme_lists_every_cli_command():
    for name in _cli_commands():
        assert f"`{name}`" in README, f"README is missing command {name!r}"


def test_readme_and_docs_state_current_version_and_pypi_gap():
    assert TOOL_VERSION == "0.2.0"
    assert "0.2.0" in README
    assert "0.1.0" in README
    assert "git+https://github.com/Dooders/FarmNotary.git@dev" in README
    assert "farm-notary>=0.2,<0.3" in README


def test_readme_documents_verify_derived_and_missing_is_not_failure():
    assert "--verify-derived" in README
    assert "--verify-derived" in CLAIMS
    assert "--verify-derived" in DESIGN
    assert "Missing is not failure" in README
    assert "Missing is not failure" in CLAIMS


def test_readme_profiles_match_checked_in_names():
    for name in PROFILE_NAMES:
        assert f"`{name}`" in README
        assert name in DESIGN


def test_readme_required_schema_keys():
    for key in REQUIRED_KEYS:
        assert f"`{key}`" in README, f"README schema section missing {key}"


def test_claim_card_rows_match_the_tool():
    for name in _CLAIM_NAMES:
        assert name in CLAIMS
        assert name in README
    assert "not claimed: scientific correctness" in README
    assert "not claimed: scientific correctness" in CLAIMS
    assert ALLOWED_SENTENCE in README
    assert ALLOWED_SENTENCE in CLAIMS


def test_principles_is_listed_and_forbids_real_things():
    assert "[docs/PRINCIPLES.md]" in README
    assert "PRINCIPLES.md" in DESIGN
    assert "not claimed: scientific correctness" in PRINCIPLES
    assert "## Non-goals" in PRINCIPLES
    assert "## How to use this document" in PRINCIPLES
    for needle in (
        "verified result",
        "file drawer",
        "independently reproduced",
        "unmatched_count",
        "SimulationRegistry",
        "dry-run",
    ):
        assert needle in PRINCIPLES, f"PRINCIPLES.md lost a concrete forbid: {needle!r}"


def test_action_docs_match_action_yml_defaults():
    action = Path("action.yml").read_text(encoding="utf-8")
    assert "default: ots" in action
    assert "dooders/FarmNotary@dev" in README
    assert "dooders/FarmNotary@dev" in ACTION
    assert "v0.2" in README and "tag" in README
    assert "--verify-derived" in ACTION
    assert "not passed" in ACTION or "without" in ACTION
    assert "dry-run" in README
    assert "CLI" in ACTION and "dry-run" in ACTION
