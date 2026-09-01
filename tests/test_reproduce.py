import json
import sys
from pathlib import Path

import pytest

from farm_notary.manifest import RECEIPT_NAME, build_manifest, load_manifest, write_manifest
from farm_notary.ots import stamp_digest
from farm_notary.reproduce import (
    RECEIPT_PROOF_NAME,
    ReproduceError,
    build_receipt,
    load_receipt,
    receipt_hash,
    reproduce_run,
    write_receipt,
)
from farm_notary.verify import verify_receipt
from tests.test_ots import pending_timestamp, serialize_timestamp

DETERMINISTIC_SCRIPT = """
import sys
from pathlib import Path
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
(out / "summary.csv").write_text("paradigm,total\\nparty,0.2\\n", encoding="utf-8")
(out / "media.mp4").write_text(sys.argv[2] if len(sys.argv) > 2 else "static", encoding="utf-8")
"""


def make_notarized_run(tmp_path: Path, *, media_arg: str = "static"):
    """Create a run dir + manifest whose command deterministically regenerates it."""
    script = tmp_path / "generate.py"
    script.write_text(DETERMINISTIC_SCRIPT, encoding="utf-8")
    run_dir = tmp_path / "run"
    command = f"{sys.executable} {script} {{run_dir}} {media_arg}"
    import subprocess

    subprocess.run(command.replace("{run_dir}", str(run_dir)), shell=True, check=True)
    manifest = build_manifest(run_dir, publish_patterns=["*.csv", "*.mp4"], git_sha="abc", command=command)
    write_manifest(manifest, run_dir)
    return run_dir, manifest, script


def test_reproduce_respects_cwd(tmp_path: Path):
    """Recorded commands are often relative to the experiment repo, not the run dir."""
    repo = tmp_path / "experiment"
    repo.mkdir()
    (repo / "generate.py").write_text(DETERMINISTIC_SCRIPT, encoding="utf-8")
    run_dir = tmp_path / "run"
    command = f"{sys.executable} generate.py {{run_dir}}"
    import subprocess

    subprocess.run(
        command.replace("{run_dir}", str(run_dir)),
        shell=True,
        check=True,
        cwd=repo,
    )
    manifest = build_manifest(
        run_dir, publish_patterns=["*.csv", "*.mp4"], git_sha="abc", command=command
    )
    write_manifest(manifest, run_dir)
    result = reproduce_run(
        manifest, fresh_dir=tmp_path / "fresh", original_dir=run_dir, cwd=repo
    )
    assert result.ok
    assert result.matched == ["media.mp4", "summary.csv"]


def test_reproduce_bitwise_match(tmp_path: Path):
    run_dir, manifest, _ = make_notarized_run(tmp_path)
    result = reproduce_run(manifest, fresh_dir=tmp_path / "fresh")
    assert result.ok
    assert result.matched == ["media.mp4", "summary.csv"]
    assert result.mismatched == [] and result.missing == []


def test_reproduce_detects_nondeterminism(tmp_path: Path):
    run_dir, manifest, script = make_notarized_run(tmp_path)
    # Change the command so the media artifact regenerates differently.
    manifest.command = f"{sys.executable} {script} {{run_dir}} different"
    result = reproduce_run(manifest, fresh_dir=tmp_path / "fresh")
    assert not result.ok
    assert result.mismatched == ["media.mp4"]
    assert result.matched == ["summary.csv"]


def test_reproduce_ignore_globs_scope_the_claim(tmp_path: Path):
    run_dir, manifest, script = make_notarized_run(tmp_path)
    manifest.command = f"{sys.executable} {script} {{run_dir}} different"
    result = reproduce_run(manifest, fresh_dir=tmp_path / "fresh", ignore=["*.mp4"])
    assert result.ok
    assert result.ignored == ["media.mp4"]
    assert result.ignore == ["*.mp4"]
    assert result.matched == ["summary.csv"]
    receipt = build_receipt(manifest, result)
    assert receipt["ignore"] == ["*.mp4"]


def test_reproduce_detects_missing_artifact(tmp_path: Path):
    run_dir, manifest, _ = make_notarized_run(tmp_path)
    manifest.artifact_hashes["ghost.csv"] = "0" * 64
    result = reproduce_run(manifest, fresh_dir=tmp_path / "fresh")
    assert not result.ok
    assert result.missing == ["ghost.csv"]


def test_reproduce_reports_extra_files(tmp_path: Path):
    run_dir, manifest, _ = make_notarized_run(tmp_path)
    del manifest.artifact_hashes["media.mp4"]
    manifest.artifacts.remove("media.mp4")
    result = reproduce_run(manifest, fresh_dir=tmp_path / "fresh")
    assert result.ok
    assert result.extra == ["media.mp4"]


def test_reproduce_requires_command(tmp_path: Path):
    run_dir, manifest, _ = make_notarized_run(tmp_path)
    manifest.command = None
    with pytest.raises(ReproduceError, match="no command"):
        reproduce_run(manifest)
    manifest.command = "python generate.py fixed-output-dir"
    with pytest.raises(ReproduceError, match="placeholder"):
        reproduce_run(manifest)


def test_receipt_round_trip_and_verify(tmp_path: Path):
    run_dir, manifest, _ = make_notarized_run(tmp_path)
    result = reproduce_run(manifest, fresh_dir=tmp_path / "fresh")
    receipt = build_receipt(manifest, result)
    write_receipt(receipt, run_dir)

    assert load_receipt(run_dir) == receipt
    assert receipt["ok"] is True
    assert receipt["original_manifest_hash"] == manifest.content_hash()
    assert receipt["ignore"] == []
    assert verify_receipt(manifest, run_dir) == []


def test_verify_receipt_detects_wrong_manifest(tmp_path: Path):
    run_dir, manifest, _ = make_notarized_run(tmp_path)
    result = reproduce_run(manifest, fresh_dir=tmp_path / "fresh")
    receipt = build_receipt(manifest, result)
    receipt["original_manifest_hash"] = "ff" * 32
    write_receipt(receipt, run_dir)
    problems = verify_receipt(manifest, run_dir)
    assert len(problems) == 1 and "refers to" in problems[0]


def test_verify_receipt_flags_failed_reproduction(tmp_path: Path):
    run_dir, manifest, _ = make_notarized_run(tmp_path)
    result = reproduce_run(manifest, fresh_dir=tmp_path / "fresh")
    receipt = build_receipt(manifest, result)
    receipt["ok"] = False
    write_receipt(receipt, run_dir)
    problems = verify_receipt(manifest, run_dir)
    assert any("failed reproduction" in p for p in problems)


def test_receipt_anchoring_and_proof_check(stub_server, tmp_path: Path):
    run_dir, manifest, _ = make_notarized_run(tmp_path)
    result = reproduce_run(manifest, fresh_dir=tmp_path / "fresh")
    receipt = build_receipt(manifest, result)
    write_receipt(receipt, run_dir)

    digest = bytes.fromhex(receipt_hash(receipt))
    stub_server.response_body = serialize_timestamp(
        pending_timestamp(digest, stub_server.url)
    )
    proof, accepted = stamp_digest(digest, calendars=[stub_server.url])
    (run_dir / RECEIPT_PROOF_NAME).write_bytes(proof)

    assert accepted == [stub_server.url]
    assert verify_receipt(manifest, run_dir) == []

    # A tampered receipt no longer matches the anchored proof.
    receipt["matched"] = ["forged.csv"]
    write_receipt(receipt, run_dir)
    problems = verify_receipt(manifest, run_dir)
    assert any(p.startswith("receipt proof:") for p in problems)


def test_notary_files_excluded_from_discovery(tmp_path: Path):
    run_dir, manifest, _ = make_notarized_run(tmp_path)
    (run_dir / "manifest.ots").write_bytes(b"proof")
    (run_dir / RECEIPT_NAME).write_text("{}", encoding="utf-8")
    (run_dir / RECEIPT_PROOF_NAME).write_bytes(b"proof")
    rebuilt = build_manifest(run_dir, publish_patterns=["*.csv", "*.mp4"], git_sha="abc", command=manifest.command)
    assert rebuilt.artifacts == ["media.mp4", "summary.csv"]
    assert rebuilt.content_hash() != ""  # sanity


def test_cli_reproduce_flow(tmp_path: Path, capsys):
    from farm_notary.cli import main

    run_dir, manifest, script = make_notarized_run(tmp_path)

    assert main(["reproduce", "--run-dir", str(run_dir), "--i-accept-untrusted-command"]) == 0
    out = capsys.readouterr().out
    assert "matched: 2 artifact(s) bitwise-identical" in out
    assert "receipt written to" in out
    assert (run_dir / RECEIPT_NAME).is_file()

    # verify reports the receipt as a scoped reproducibility claim.
    assert main(["verify", "--run-dir", str(run_dir)]) == 0
    out = capsys.readouterr().out
    assert "bitwise reproducible (scoped)" in out
    assert "2/2" in out
    from farm_notary.scope import format_bitwise_status

    receipt = json.loads((run_dir / RECEIPT_NAME).read_text(encoding="utf-8"))
    assert format_bitwise_status("2/2", receipt["environment"], ok=True) in out

    # A nondeterministic artifact fails unless ignored.
    data = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    data["command"] = f"{sys.executable} {script} {{run_dir}} different"
    (run_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    # manifest content changed -> old receipt now refers to a stale hash; remove it.
    (run_dir / RECEIPT_NAME).unlink()

    assert main(["reproduce", "--run-dir", str(run_dir), "--i-accept-untrusted-command"]) == 1
    out = capsys.readouterr().out
    assert "video_encoder" in out
    assert "not a science failure" in out
    assert (
        main(
            [
                "reproduce",
                "--run-dir",
                str(run_dir),
                "--i-accept-untrusted-command",
                "--ignore",
                "*.mp4",
            ]
        )
        == 0
    )
    assert "ignored globs (excluded from the claim): *.mp4" in capsys.readouterr().out

    assert main(["verify", "--run-dir", str(run_dir)]) == 0
    out = capsys.readouterr().out
    assert "bitwise reproducible (scoped)" in out
    assert "1/1, ignored: *.mp4" in out


def test_cli_reproduce_requires_acceptance_for_untrusted_command(tmp_path: Path, capsys):
    from farm_notary.cli import main

    run_dir, _manifest, _ = make_notarized_run(tmp_path)

    assert main(["reproduce", "--run-dir", str(run_dir)]) == 2
    captured = capsys.readouterr()
    assert "executes the manifest's recorded command via the shell" in captured.err
    assert "--i-accept-untrusted-command" in captured.err
    assert "no network" in captured.err
    assert not (run_dir / RECEIPT_NAME).exists()


def test_cli_reproduce_trusts_same_local_checkout(tmp_path: Path, capsys):
    from farm_notary.cli import main
    import subprocess

    repo = tmp_path / "experiment"
    repo.mkdir()
    script = repo / "generate.py"
    script.write_text(DETERMINISTIC_SCRIPT, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "agent@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Agent"], cwd=repo, check=True)
    subprocess.run(["git", "add", "generate.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    sha = (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
        .stdout.strip()
    )
    run_dir = tmp_path / "run"
    command = f"{sys.executable} generate.py {{run_dir}}"
    subprocess.run(
        command.replace("{run_dir}", str(run_dir)),
        shell=True,
        check=True,
        cwd=repo,
    )
    manifest = build_manifest(
        run_dir,
        publish_patterns=["*.csv", "*.mp4"],
        git_sha=sha,
        command=command,
    )
    write_manifest(manifest, run_dir)

    assert main(["reproduce", "--run-dir", str(run_dir), "--cwd", str(repo)]) == 0
    captured = capsys.readouterr()
    assert "trusted context matched the local checkout git_sha" in captured.err
    assert "matched: 2 artifact(s) bitwise-identical" in captured.out


def test_cli_reproduce_trusts_same_ci_context(tmp_path: Path, capsys, monkeypatch):
    """When ci_provenance repo/sha match the live GITHUB_* env, reproduce trusts it."""
    from farm_notary.cli import main

    run_dir, manifest, _ = make_notarized_run(tmp_path)
    manifest.ci_provenance = {
        "kind": "github_actions",
        "sha": manifest.git_sha,
        "repository": "Dooders/FarmNotary",
    }
    write_manifest(manifest, run_dir)

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", "Dooders/FarmNotary")
    monkeypatch.setenv("GITHUB_SHA", manifest.git_sha)

    assert main(["reproduce", "--run-dir", str(run_dir)]) == 0
    captured = capsys.readouterr()
    assert "trusted context matched the current GitHub Actions repo/SHA" in captured.err
    assert "matched: 2 artifact(s) bitwise-identical" in captured.out


def test_cli_reproduce_ci_context_mismatch_stays_untrusted(tmp_path: Path, capsys, monkeypatch):
    """A different repo/SHA (e.g. a downloaded manifest) must not be auto-trusted."""
    from farm_notary.cli import main

    run_dir, manifest, _ = make_notarized_run(tmp_path)
    manifest.ci_provenance = {
        "kind": "github_actions",
        "sha": manifest.git_sha,
        "repository": "someone-else/other-repo",
    }
    write_manifest(manifest, run_dir)

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", "Dooders/FarmNotary")
    monkeypatch.setenv("GITHUB_SHA", manifest.git_sha)

    assert main(["reproduce", "--run-dir", str(run_dir)]) == 2
    captured = capsys.readouterr()
    assert "--i-accept-untrusted-command" in captured.err
