import json
import sys

from farm_notary.campaign import build_campaign
from farm_notary.cli import main
from farm_notary.manifest import build_manifest, write_manifest
from farm_notary.paper import (
    PAPER_LADDER_CELL,
    PAPER_LADDER_NOTE,
    bitcoin_attestation_label,
    build_paper_pack,
    write_paper_pack,
)
from farm_notary.ots import DEFAULT_CALENDARS, PROOF_NAME, serialize_proof
from tests.test_ots import pending_timestamp


def _run(tmp_path, seed=0):
    run = tmp_path / f"seed-{seed}"
    run.mkdir()
    (run / "summary.csv").write_text("ok\n", encoding="utf-8")
    (run / "trials.csv").write_text("t\n", encoding="utf-8")
    config = {
        "seed": seed,
        "notary": {
            "publish": ["*.csv"],
            "derived_from": [
                {
                    "outputs": ["summary.csv"],
                    "sources": ["trials.csv"],
                    "command": "true",
                    "mode": "verify",
                }
            ],
        },
    }
    manifest = build_manifest(run, config=config, git_sha="abc", runner="consensus")
    manifest.cid = "bafytesthash"
    manifest.precommit_hash = "aa" * 32
    write_manifest(manifest, run)
    return run, manifest


def test_paper_pack_contains_required_fields(tmp_path):
    run, manifest = _run(tmp_path)
    text = build_paper_pack(manifest, run, experiment="consensus")
    assert "bafytesthash" in text
    assert manifest.content_hash() in text
    assert "Bitcoin attestation" in text
    assert "`*.csv`" in text
    assert "Unmatched files" in text
    assert "| Artifact label |" in text
    assert f"| Reader ladder | {PAPER_LADDER_CELL} |" in text
    assert PAPER_LADDER_NOTE in text
    assert "| Claim level |" not in text
    assert "| Ladder |" not in text
    assert manifest.precommit_hash in text
    assert "1-ulp" in text
    assert "summary.csv" in text
    assert "trials.csv" in text


def test_paper_pack_records_withheld_root_not_names(tmp_path):
    run, _ = _run(tmp_path)
    (run / "extra.json").write_text("{}\n", encoding="utf-8")
    (run / "votes_ballot.csv").write_text("A,B\n", encoding="utf-8")
    from farm_notary.manifest import build_manifest, write_manifest

    manifest = build_manifest(
        run, publish_patterns=["summary.csv", "trials.csv"], git_sha="abc"
    )
    write_manifest(manifest, run)
    text = build_paper_pack(manifest, run, experiment="consensus")
    assert manifest.withheld_root
    assert f"| Withheld root | `{manifest.withheld_root}` |" in text
    assert "Withheld classes" in text
    assert "votes_ballot.csv" not in text
    assert "extra.json" not in text


def test_bitcoin_attestation_none_and_pending():
    class Rec:
        anchor = None

    assert bitcoin_attestation_label(Rec()) == "none"

    class Pending:
        anchor = {"backend": "opentimestamps", "detail": {"status": "pending", "calendars": ["https://x"]}}

    assert bitcoin_attestation_label(Pending()) == "Pending (calendar attestation only)"

    class Dry:
        anchor = {"backend": "dry-run"}

    assert bitcoin_attestation_label(Dry()) == "none"


def test_bitcoin_attestation_label_distinguishes_public_and_user_supplied_pending(tmp_path):
    run, manifest = _run(tmp_path)
    digest = bytes.fromhex(manifest.content_hash())
    manifest.anchor = {"backend": "opentimestamps", "detail": {"proof": PROOF_NAME}}

    (run / PROOF_NAME).write_bytes(
        serialize_proof(pending_timestamp(digest, DEFAULT_CALENDARS[0]))
    )
    assert bitcoin_attestation_label(manifest, run) == "Pending (unverified claim; public OpenTimestamps calendars)"

    (run / PROOF_NAME).write_bytes(
        serialize_proof(pending_timestamp(digest, "https://example.com"))
    )
    assert bitcoin_attestation_label(manifest, run) == (
        "Pending at user-supplied calendar https://example.com "
        "(unverified claim; untrusted until Bitcoin)"
    )


def test_cli_paper_pack(tmp_path, capsys):
    run, _ = _run(tmp_path)
    assert main(["paper-pack", "--run-dir", str(run), "--name", "consensus"]) == 0
    out = capsys.readouterr().out
    assert "appendix.md" in out
    assert (run / "appendix.md").is_file()
    body = (run / "appendix.md").read_text(encoding="utf-8")
    assert "CID" in body
    assert "Content hash" in body
    assert "Publish allowlist" in body


def test_bitcoin_attestation_eas_is_experimental():
    class Eas:
        anchor = {"backend": "eas", "attestation_uid": "0xabc"}

    assert bitcoin_attestation_label(Eas()) == "EAS (experimental) 0xabc"

    class Submitted:
        anchor = {"backend": "eas"}

    assert bitcoin_attestation_label(Submitted()) == "EAS (experimental) submitted"


def test_cli_paper_pack_verify_derived_confirms_or_notes(tmp_path, capsys):
    run, _ = _run(tmp_path)
    assert main(["paper-pack", "--run-dir", str(run), "--name", "consensus"]) == 0
    body = (run / "appendix.md").read_text(encoding="utf-8")
    assert "--verify-derived" in body
    assert "recompute exactly" not in body
    assert "| Artifact label | derived_declared |" in body
    capsys.readouterr()

    assert (
        main(
            [
                "paper-pack",
                "--run-dir",
                str(run),
                "--name",
                "consensus",
                "--verify-derived",
            ]
        )
        == 0
    )
    confirmed = (run / "appendix.md").read_text(encoding="utf-8")
    assert "recompute exactly" in confirmed
    assert "| Artifact label | derived |" in confirmed


def test_cli_paper_pack_records_failed_derivation(tmp_path):
    run, _ = _run(tmp_path)
    script = tmp_path / "fail.py"
    script.write_text("import sys; sys.exit(1)\n", encoding="utf-8")
    data = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    data["derived_from"][0]["command"] = f"{sys.executable} {script}"
    (run / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    assert main(["paper-pack", "--run-dir", str(run), "--verify-derived"]) == 0
    body = (run / "appendix.md").read_text(encoding="utf-8")
    assert "were not confirmed" in body


def test_paper_pack_for_campaign_lists_children(tmp_path):
    runs = []
    for seed in range(3):
        run, _ = _run(tmp_path, seed=seed)
        runs.append(run)
    campaign = build_campaign(runs, name="consensus-sweep", campaign_dir=tmp_path)
    text = build_paper_pack(campaign, tmp_path)
    assert "Child runs" in text
    assert f"| Reader ladder | {PAPER_LADDER_CELL} |" in text
    assert PAPER_LADDER_NOTE in text
    assert "seeds 0…2" in text or "0" in text
    dest = write_paper_pack(text, tmp_path)
    assert dest.name == "appendix.md"
