import json
from pathlib import Path

from farm_notary import cli
from farm_notary.anchor import AnchorReceipt


def make_run_dir(tmp_path: Path) -> Path:
    (tmp_path / "summary.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    assert cli.main(["manifest", "--run-dir", str(tmp_path), "--git-sha", "abc"]) == 0
    return tmp_path


def test_manifest_and_verify(tmp_path: Path):
    run_dir = make_run_dir(tmp_path)
    assert (run_dir / "manifest.json").is_file()
    assert cli.main(["verify", "--run-dir", str(run_dir)]) == 0


def test_anchor_dry_run_does_not_touch_manifest(tmp_path: Path, capsys):
    run_dir = make_run_dir(tmp_path)
    before = (run_dir / "manifest.json").read_text(encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["anchor", "--run-dir", str(run_dir)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["backend"] == "dry-run"
    assert out["dry_run"] is True
    assert out["tx_hash"] is None
    assert (run_dir / "manifest.json").read_text(encoding="utf-8") == before


class FakeBackend:
    def submit(self, manifest, *, cid=None):
        return AnchorReceipt(
            backend="eas",
            manifest_hash=manifest.content_hash(),
            cid=cid,
            tx_hash="0x" + "11" * 32,
            attestation_uid="0x" + "22" * 32,
            chain_id=84532,
            dry_run=False,
        )


def test_anchor_real_backend_writes_receipt(tmp_path: Path, capsys, monkeypatch):
    run_dir = make_run_dir(tmp_path)
    monkeypatch.setattr(cli, "get_backend", lambda name: FakeBackend())
    assert cli.main(["anchor", "--run-dir", str(run_dir), "--cid", "bafytest"]) == 0
    capsys.readouterr()
    data = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert data["cid"] == "bafytest"
    assert data["chain"]["attestation_uid"] == "0x" + "22" * 32
    assert data["chain"]["dry_run"] is False
    # The anchored hash matches what verify recomputes from the written manifest.
    assert cli.main(["verify", "--run-dir", str(run_dir)]) == 0


def test_anchor_no_write(tmp_path: Path, capsys, monkeypatch):
    run_dir = make_run_dir(tmp_path)
    before = (run_dir / "manifest.json").read_text(encoding="utf-8")
    monkeypatch.setattr(cli, "get_backend", lambda name: FakeBackend())
    assert cli.main(["anchor", "--run-dir", str(run_dir), "--no-write"]) == 0
    assert (run_dir / "manifest.json").read_text(encoding="utf-8") == before
