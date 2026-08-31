"""Keep the published docs aligned with the CLI, schema, and claim card."""

import argparse
from pathlib import Path

from farm_notary.cli import _build_parser
from farm_notary.profiles import PROFILE_NAMES
from farm_notary.schema import REQUIRED_KEYS, TOOL_VERSION
from farm_notary.scope import ALLOWED_SENTENCE
from farm_notary.ladder import L0_MEANING, LADDER_LEVELS, LADDER_MEANINGS
from farm_notary.verify import _CLAIM_NAMES

README = Path("README.md").read_text(encoding="utf-8")
CLAIMS = Path("docs/CLAIMS.md").read_text(encoding="utf-8")
CHANGELOG = Path("CHANGELOG.md").read_text(encoding="utf-8")
DESIGN = Path("docs/DESIGN.md").read_text(encoding="utf-8")
ACTION = Path("docs/ACTION.md").read_text(encoding="utf-8")
PRINCIPLES = Path("docs/PRINCIPLES.md").read_text(encoding="utf-8")
DEMO_NOTEBOOK = Path("docs/demo/farmnotary_live_demo.ipynb").read_text(encoding="utf-8")
SLIDES = Path("docs/slides/index.html").read_text(encoding="utf-8")
CONSENSUS_SLIDES = Path("docs/slides/consensus.html").read_text(encoding="utf-8")


def _cli_commands() -> list[str]:
    parser = _build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return sorted(action.choices)
    raise AssertionError("farm-notary parser has no subcommands")


def test_readme_lists_every_cli_command():
    for name in _cli_commands():
        assert f"`{name}`" in README, f"README is missing command {name!r}"


def test_readme_and_docs_state_current_version():
    assert TOOL_VERSION == "0.2.0"
    assert "0.2.0" in README
    assert 'pip install "farm-notary[ots]"' in README
    assert "farm-notary>=0.2,<0.3" in README
    assert "dooders/FarmNotary@v0.2.0" in README


def test_readme_documents_verify_derived_and_missing_is_not_failure():
    assert "--verify-derived" in README
    assert "--verify-derived" in CLAIMS
    assert "--verify-derived" in DESIGN
    assert "--i-accept-untrusted-command" in README
    assert "--i-accept-untrusted-command" in CLAIMS
    assert "untrusted code execution" in README
    assert "untrusted code execution" in CLAIMS
    assert "no network" in README
    assert "no network" in CLAIMS
    assert "Missing is not failure" in README
    assert "Missing is not failure" in CLAIMS
    assert "Exit 0 means the artifact hashes match" in README
    assert "Exit code 0 means the artifact hashes match" in CLAIMS


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
    for level in LADDER_LEVELS:
        assert level in CLAIMS
        assert level in README
    assert "level: none — no earned ladder level" in README
    assert "level: none — no earned ladder level" in CLAIMS
    assert f"next:  L0 — {L0_MEANING} (missing: Bitcoin attestation)" in README
    assert f"next:  L0 — {L0_MEANING} (missing: Bitcoin attestation)" in CLAIMS
    assert L0_MEANING in DESIGN
    assert "pending" in CLAIMS
    assert "not L0" in CLAIMS or "is not L0" in CLAIMS
    assert "user-supplied calendars" in CLAIMS
    assert "user-supplied calendars" in README
    assert "command was not run" in CLAIMS
    assert "command was not run" in README or "not a completed re-run" in README
    assert "Artifact label" in CLAIMS
    assert "Reader ladder" in CLAIMS
    assert "does not cite `Ln`" in CLAIMS
    assert "does not cite `Ln`" in README or "does not cite `Ln`" in DESIGN


def test_intro_deck_pdf_is_checked_in():
    """The talk is also a downloadable 16:9 PDF, not only a browser deck."""
    pdf = Path("docs/slides/farmnotary.pdf")
    slides_readme = Path("docs/slides/README.md").read_text(encoding="utf-8")
    assert pdf.is_file()
    assert pdf.read_bytes()[:5] == b"%PDF-"
    assert pdf.stat().st_size > 10_000
    assert "farmnotary.pdf" in README
    assert "farmnotary.pdf" in slides_readme
    assert "export_pdf.py" in slides_readme


def test_intro_deck_stays_inside_the_claim_card():
    """The researcher/lab talk may not outrun CLAIMS.md."""
    assert "[docs/slides/](docs/slides/)" in README
    assert "not claimed: scientific correctness" in SLIDES
    assert "Missing is not failure" in SLIDES
    assert ALLOWED_SENTENCE in SLIDES
    assert L0_MEANING in SLIDES
    assert "command was not run" in SLIDES or "not a completed re-run" in SLIDES
    for level in LADDER_LEVELS:
        assert level in SLIDES
    assert LADDER_MEANINGS["L3"] in CLAIMS
    assert "not proven independent" in SLIDES.lower()
    assert "cross-hardware" in SLIDES.lower()
    assert "verified result" in SLIDES.lower()
    assert "badge" in SLIDES.lower()
    assert "not independently reproduced" in SLIDES.lower()


def test_consensus_walkthrough_deck_stays_inside_the_claim_card():
    """The worked-example talk may not outrun CLAIMS.md or the live demo."""
    assert "docs/slides/consensus" in README
    assert "not claimed: scientific correctness" in CONSENSUS_SLIDES
    assert "Missing is not failure" in CONSENSUS_SLIDES
    assert ALLOWED_SENTENCE in CONSENSUS_SLIDES
    assert L0_MEANING in CONSENSUS_SLIDES
    assert "command was not run" in CONSENSUS_SLIDES or "not a completed re-run" in CONSENSUS_SLIDES
    for level in LADDER_LEVELS:
        assert level in CONSENSUS_SLIDES
    assert "not proven independent" in CONSENSUS_SLIDES.lower()
    assert "cross-hardware" in CONSENSUS_SLIDES.lower()
    assert "verified result" in CONSENSUS_SLIDES.lower()
    assert "badge" in CONSENSUS_SLIDES.lower()
    assert "not independently reproduced" in CONSENSUS_SLIDES.lower()
    assert "not a science failure" in CONSENSUS_SLIDES.lower()
    assert "dry-run" in CONSENSUS_SLIDES
    assert "private/ballots.csv" in CONSENSUS_SLIDES
    assert "--profile consensus" in CONSENSUS_SLIDES
    assert "docs/demo/experiment.py" in CONSENSUS_SLIDES


def test_consensus_walkthrough_pdf_is_checked_in():
    """The walkthrough is also a downloadable 16:9 PDF."""
    pdf = Path("docs/slides/consensus.pdf")
    slides_readme = Path("docs/slides/README.md").read_text(encoding="utf-8")
    assert pdf.is_file()
    assert pdf.read_bytes()[:5] == b"%PDF-"
    assert pdf.stat().st_size > 10_000
    assert "consensus.pdf" in README
    assert "consensus.pdf" in slides_readme
    assert "export_pdf.py" in slides_readme


def test_principles_is_listed_and_forbids_real_things():
    assert "[docs/PRINCIPLES.md](docs/PRINCIPLES.md)" in README
    assert "[PRINCIPLES.md](PRINCIPLES.md)" in DESIGN
    assert "not claimed: scientific correctness" in PRINCIPLES
    assert "## Non-goals" in PRINCIPLES
    assert "## How to use this document" in PRINCIPLES
    for needle in (
        "verified result",
        "file drawer",
        "independently reproduced",
        "unmatched_count",
        "withheld_root",
        "SimulationRegistry",
        "dry-run",
    ):
        assert needle in PRINCIPLES, f"PRINCIPLES.md lost a concrete forbid: {needle!r}"


def test_live_demo_notebook_stays_inside_the_claim_card():
    """The live demo may not outrun CLAIMS.md."""
    assert "[docs/demo/](docs/demo/)" in README
    assert "not claimed: scientific correctness" in DEMO_NOTEBOOK
    assert "Missing is not failure" in DEMO_NOTEBOOK
    assert ALLOWED_SENTENCE in DEMO_NOTEBOOK
    assert "dry-run" in DEMO_NOTEBOOK
    assert "not a science failure" in DEMO_NOTEBOOK.lower()
    lower = DEMO_NOTEBOOK.lower()
    assert "will not claim independently reproduced" in lower
    assert "may **not** take" in lower or "may not take" in lower
    assert "verified result" in lower
    assert "cross-hardware" in lower


def test_action_docs_match_action_yml_defaults():
    action = Path("action.yml").read_text(encoding="utf-8")
    assert "default: ots" in action
    assert "dooders/FarmNotary@v0.2.0" in README
    assert "dooders/FarmNotary@v0.2.0" in ACTION
    assert "--verify-derived" in ACTION
    assert "not passed" in ACTION or "without" in ACTION
    assert "dry-run" in README
    assert "CLI" in ACTION and "dry-run" in ACTION
    assert "sign-receipt" in ACTION
    assert "v2.5.3" in ACTION
    assert "default: \"false\"" in action or 'default: "false"' in action


def test_changelog_records_sigstore_and_honest_l3():
    assert "Sigstore" in CHANGELOG
    assert "identity not constrained" in CHANGELOG
    assert "L3 (independent" not in CHANGELOG
    assert "identity) is reserved" not in CHANGELOG


def test_docs_frame_withheld_as_publication_scope():
    for blob in (README, DESIGN, CLAIMS, PRINCIPLES, CHANGELOG):
        assert "withheld_root" in blob
    assert "publication scope" in README.lower() or "publication-scope" in README.lower()
    assert "privacy protocol" in CHANGELOG.lower() or "publication-scope" in CHANGELOG.lower()
    assert "The allowlist is the privacy model" not in CLAIMS
    assert Path("docs/MIGRATION.md").is_file()
    assert "withheld_salt" in Path("docs/MIGRATION.md").read_text(encoding="utf-8")
    assert Path("schemas/farmnotary.manifest.v1.json").is_file()
