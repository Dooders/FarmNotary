"""Mismatch classifier: name packaging causes, not science failures."""

import sys
from pathlib import Path

from farm_notary.diagnose import (
    KIND_EMBEDDED_PATH,
    KIND_FLOAT_FORMAT,
    KIND_TIMESTAMP,
    KIND_UNCLASSIFIED,
    KIND_VIDEO_ENCODER,
    diagnose_mismatch,
)
from farm_notary.manifest import build_manifest, write_manifest
from farm_notary.reproduce import build_receipt, reproduce_run, write_receipt
from farm_notary.verify import evaluate_claims


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_embedded_absolute_path_is_the_run_dir_story(tmp_path: Path):
    """The 7/7 → 8/8 fix: REPORT.md embedded the output directory."""
    original_dir = tmp_path / "run-a"
    fresh_dir = tmp_path / "run-b"
    _write(original_dir / "REPORT.md", f"Artifacts written to {original_dir}/\n")
    _write(fresh_dir / "REPORT.md", f"Artifacts written to {fresh_dir}/\n")
    diag = diagnose_mismatch(
        "REPORT.md",
        original_dir / "REPORT.md",
        fresh_dir / "REPORT.md",
        original_dir=original_dir,
        fresh_dir=fresh_dir,
    )
    assert diag.kinds == (KIND_EMBEDDED_PATH,)
    assert diag.explains
    assert "not a science failure" in "\n".join(diag.summary_lines())
    assert "{run_dir}" in diag.hint


def test_timestamp_in_report(tmp_path: Path):
    original_dir = tmp_path / "a"
    fresh_dir = tmp_path / "b"
    _write(original_dir / "REPORT.md", "Generated 2026-08-27T10:00:00Z\nseed 0\n")
    _write(fresh_dir / "REPORT.md", "Generated 2026-08-28T11:22:33Z\nseed 0\n")
    diag = diagnose_mismatch(
        "REPORT.md",
        original_dir / "REPORT.md",
        fresh_dir / "REPORT.md",
        original_dir=original_dir,
        fresh_dir=fresh_dir,
    )
    assert diag.kinds == (KIND_TIMESTAMP,)
    assert diag.explains
    assert "not a science failure" in "\n".join(diag.summary_lines())


def test_float_print_format(tmp_path: Path):
    original_dir = tmp_path / "a"
    fresh_dir = tmp_path / "b"
    _write(original_dir / "summary.csv", "paradigm,total\nparty,0.2\n")
    _write(fresh_dir / "summary.csv", "paradigm,total\nparty,0.20000000000000001\n")
    diag = diagnose_mismatch(
        "summary.csv",
        original_dir / "summary.csv",
        fresh_dir / "summary.csv",
        original_dir=original_dir,
        fresh_dir=fresh_dir,
    )
    assert diag.kinds == (KIND_FLOAT_FORMAT,)
    assert diag.explains
    assert "round_trip" in diag.hint


def test_video_encoder(tmp_path: Path):
    original_dir = tmp_path / "a"
    fresh_dir = tmp_path / "b"
    (original_dir).mkdir()
    (fresh_dir).mkdir()
    (original_dir / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypisom" + b"aaaa")
    (fresh_dir / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypisom" + b"bbbb")
    diag = diagnose_mismatch(
        "clip.mp4",
        original_dir / "clip.mp4",
        fresh_dir / "clip.mp4",
        original_dir=original_dir,
        fresh_dir=fresh_dir,
    )
    assert diag.kinds == (KIND_VIDEO_ENCODER,)
    assert diag.explains
    assert "--ignore" in diag.hint


def test_real_numeric_change_is_unclassified(tmp_path: Path):
    original_dir = tmp_path / "a"
    fresh_dir = tmp_path / "b"
    _write(original_dir / "summary.csv", "paradigm,total\nparty,0.2\n")
    _write(fresh_dir / "summary.csv", "paradigm,total\nparty,0.9\n")
    diag = diagnose_mismatch(
        "summary.csv",
        original_dir / "summary.csv",
        fresh_dir / "summary.csv",
        original_dir=original_dir,
        fresh_dir=fresh_dir,
    )
    assert diag.kinds == (KIND_UNCLASSIFIED,)
    assert not diag.explains
    assert "not a science verdict" in "\n".join(diag.summary_lines())
    assert "not a science failure" not in "\n".join(diag.summary_lines())


def test_scientific_notation_is_float_print_format(tmp_path: Path):
    original_dir = tmp_path / "a"
    fresh_dir = tmp_path / "b"
    _write(original_dir / "summary.csv", "x\n1.23e-4\n")
    _write(fresh_dir / "summary.csv", "x\n0.000123\n")
    diag = diagnose_mismatch(
        "summary.csv",
        original_dir / "summary.csv",
        fresh_dir / "summary.csv",
        original_dir=original_dir,
        fresh_dir=fresh_dir,
    )
    assert diag.kinds == (KIND_FLOAT_FORMAT,)
    assert diag.explains


def test_path_and_timestamp_together(tmp_path: Path):
    original_dir = tmp_path / "run-a"
    fresh_dir = tmp_path / "run-b"
    _write(
        original_dir / "REPORT.md",
        f"out {original_dir}\ngenerated 2026-08-27T10:00:00Z\n",
    )
    _write(
        fresh_dir / "REPORT.md",
        f"out {fresh_dir}\ngenerated 2026-08-28T11:00:00Z\n",
    )
    diag = diagnose_mismatch(
        "REPORT.md",
        original_dir / "REPORT.md",
        fresh_dir / "REPORT.md",
        original_dir=original_dir,
        fresh_dir=fresh_dir,
    )
    assert KIND_EMBEDDED_PATH in diag.kinds
    assert KIND_TIMESTAMP in diag.kinds
    assert diag.explains


def test_reproduce_classifies_embedded_path(tmp_path: Path):
    script = tmp_path / "generate.py"
    script.write_text(
        "import sys\nfrom pathlib import Path\n"
        "out = Path(sys.argv[1])\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "(out / 'REPORT.md').write_text(f'Artifacts written to {out}/\\n')\n"
        "(out / 'summary.csv').write_text('paradigm,total\\nparty,0.2\\n')\n",
        encoding="utf-8",
    )
    run_dir = tmp_path / "run"
    command = f"{sys.executable} {script} {{run_dir}}"
    import subprocess

    subprocess.run(command.replace("{run_dir}", str(run_dir)), shell=True, check=True)
    manifest = build_manifest(
        run_dir,
        publish_patterns=["REPORT.md", "summary.csv"],
        git_sha="abc",
        command=command,
    )
    write_manifest(manifest, run_dir)
    result = reproduce_run(
        manifest, fresh_dir=tmp_path / "fresh", original_dir=run_dir
    )
    assert not result.ok
    assert result.mismatched == ["REPORT.md"]
    assert result.matched == ["summary.csv"]
    assert result.diagnostics[0].kinds == (KIND_EMBEDDED_PATH,)
    text = "\n".join(result.summary())
    assert "embedded_absolute_path" in text
    assert "not a science failure" in text

    write_receipt(build_receipt(manifest, result), run_dir)
    card = evaluate_claims(manifest, run_dir)
    assert not card.ok
    assert any("embedded_absolute_path" in line for line in card.notes)
    assert any("not a science failure" in line for line in card.notes)
