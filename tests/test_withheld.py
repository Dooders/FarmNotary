"""Salted Merkle commitment over unpublished files."""

import json
from pathlib import Path

from farm_notary.cli import main
from farm_notary.manifest import build_manifest, list_withheld, write_manifest
from farm_notary.withheld import (
    commit_withheld,
    leaf_digest,
    reveal_withheld,
    verify_reveal,
    write_reveal,
)


SALT = "ab" * 32


def _run(tmp_path: Path) -> Path:
    (tmp_path / "summary.csv").write_text("ok\n", encoding="utf-8")
    (tmp_path / "extra.json").write_text("{}", encoding="utf-8")
    (tmp_path / "votes_ballot.csv").write_text("A,B\n", encoding="utf-8")
    return tmp_path


def test_different_withheld_sets_different_roots(tmp_path: Path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "summary.csv").write_text("ok\n", encoding="utf-8")
    (b / "summary.csv").write_text("ok\n", encoding="utf-8")
    (a / "left.json").write_text("1\n", encoding="utf-8")
    (b / "right.json").write_text("1\n", encoding="utf-8")
    ma = build_manifest(a, publish_patterns=["*.csv"], git_sha="abc", withheld_salt=SALT)
    mb = build_manifest(b, publish_patterns=["*.csv"], git_sha="abc", withheld_salt=SALT)
    assert ma.withheld_root
    assert mb.withheld_root
    assert ma.withheld_root != mb.withheld_root
    assert ma.withheld_salt == mb.withheld_salt == SALT


def test_same_set_same_salt_same_root(tmp_path: Path):
    _run(tmp_path)
    first = build_manifest(
        tmp_path, publish_patterns=["*.csv"], git_sha="abc", withheld_salt=SALT
    )
    second = build_manifest(
        tmp_path, publish_patterns=["*.csv"], git_sha="abc", withheld_salt=SALT
    )
    assert first.withheld_root == second.withheld_root
    assert first.withheld_classes == second.withheld_classes


def test_unsalted_ballot_hashes_are_not_stored(tmp_path: Path):
    _run(tmp_path)
    ballot = (tmp_path / "votes_ballot.csv").read_bytes()
    unsalted = __import__("hashlib").sha256(ballot).hexdigest()
    manifest = build_manifest(
        tmp_path, publish_patterns=["summary.csv"], git_sha="abc", withheld_salt=SALT
    )
    blob = json.dumps(manifest.to_dict())
    assert unsalted not in blob
    assert "votes_ballot.csv" not in blob
    assert "votes_ballot.csv" not in manifest.artifact_hashes
    assert manifest.withheld_root
    # The leaf is salted; it is not the raw SHA-256 of the ballot.
    salt = bytes.fromhex(SALT)
    leaf = leaf_digest(salt, "votes_ballot.csv", ballot).hex()
    assert leaf != unsalted
    assert leaf not in blob


def test_classes_split_denylist_and_unmatched(tmp_path: Path):
    _run(tmp_path)
    manifest = build_manifest(
        tmp_path, publish_patterns=["summary.csv"], git_sha="abc", withheld_salt=SALT
    )
    assert manifest.unmatched_count == 2
    assert manifest.withheld_classes["denylist"]["count"] == 1
    assert manifest.withheld_classes["unmatched"]["count"] == 1
    assert "reason" in manifest.withheld_classes["denylist"]


def test_no_commitment_when_everything_published(tmp_path: Path):
    (tmp_path / "summary.csv").write_text("ok\n", encoding="utf-8")
    manifest = build_manifest(tmp_path, publish_patterns=["*.csv"], git_sha="abc")
    assert manifest.unmatched_count == 0
    assert manifest.withheld_root is None
    assert "withheld_root" not in manifest.to_dict()


def test_older_manifest_without_withheld_fields_still_loads(tmp_path: Path):
    (tmp_path / "summary.csv").write_text("ok\n", encoding="utf-8")
    (tmp_path / "extra.json").write_text("{}\n", encoding="utf-8")
    manifest = build_manifest(tmp_path, publish_patterns=["*.csv"], git_sha="abc")
    body = manifest.to_dict()
    assert body["unmatched_count"] == 1
    assert "withheld_root" in body
    body.pop("withheld_salt", None)
    body.pop("withheld_root", None)
    body.pop("withheld_classes", None)
    (tmp_path / "manifest.json").write_text(json.dumps(body), encoding="utf-8")
    from farm_notary.manifest import load_manifest

    loaded = load_manifest(tmp_path)
    assert loaded.withheld_root is None
    assert loaded.unmatched_count == 1
    assert loaded.artifacts == ["summary.csv"]


def test_reveal_subset_and_verify(tmp_path: Path):
    _run(tmp_path)
    manifest = build_manifest(
        tmp_path, publish_patterns=["summary.csv"], git_sha="abc", withheld_salt=SALT
    )
    write_manifest(manifest, tmp_path)
    withheld = list_withheld(tmp_path, manifest.publish_patterns)
    entries = reveal_withheld(
        withheld, ["votes_ballot.csv"], salt_hex=manifest.withheld_salt
    )
    assert len(entries) == 1
    assert entries[0].path == "votes_ballot.csv"
    assert entries[0].cls == "denylist"
    assert verify_reveal(
        entries,
        salt_hex=manifest.withheld_salt,
        root_hex=manifest.withheld_root,
        run_dir=tmp_path,
    ) == []


def test_reveal_wrong_bytes_fails(tmp_path: Path):
    _run(tmp_path)
    manifest = build_manifest(
        tmp_path, publish_patterns=["summary.csv"], git_sha="abc", withheld_salt=SALT
    )
    withheld = list_withheld(tmp_path, manifest.publish_patterns)
    entries = reveal_withheld(
        withheld, ["extra.json"], salt_hex=manifest.withheld_salt
    )
    (tmp_path / "extra.json").write_text("tampered\n", encoding="utf-8")
    problems = verify_reveal(
        entries,
        salt_hex=manifest.withheld_salt,
        root_hex=manifest.withheld_root,
        run_dir=tmp_path,
    )
    assert problems


def test_cli_manifest_prints_root_not_names(tmp_path: Path, capsys):
    _run(tmp_path)
    code = main(
        [
            "manifest",
            "--run-dir",
            str(tmp_path),
            "--publish",
            "summary.csv",
            "--git-sha",
            "abc",
        ]
    )
    assert code == 0
    printed = capsys.readouterr()
    data = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert data["withheld_root"]
    assert "votes_ballot.csv" not in printed.out
    assert "votes_ballot.csv" not in printed.err
    assert "extra.json" not in printed.out
    assert "extra.json" not in printed.err
    assert "votes_ballot.csv" not in data["artifacts"]
    assert "votes_ballot.csv" not in json.dumps(data.get("artifact_hashes"))


def test_cli_reveal_and_verify(tmp_path: Path, capsys):
    _run(tmp_path)
    assert (
        main(
            [
                "manifest",
                "--run-dir",
                str(tmp_path),
                "--publish",
                "summary.csv",
                "--git-sha",
                "abc",
            ]
        )
        == 0
    )
    dest = tmp_path / "reveal.json"
    assert (
        main(
            [
                "reveal-withheld",
                "--run-dir",
                str(tmp_path),
                "--path",
                "extra.json",
                "--out",
                str(dest),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "votes_ballot.csv" not in out
    assert dest.is_file()
    assert (
        main(
            [
                "reveal-withheld",
                "--run-dir",
                str(tmp_path),
                "--verify",
                "--reveal",
                str(dest),
            ]
        )
        == 0
    )


def test_commit_withheld_empty_is_none():
    assert commit_withheld([]) is None
