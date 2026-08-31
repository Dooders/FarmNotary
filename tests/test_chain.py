"""Tests for farm_notary.chain — multi-stage provenance chains."""

import json
from pathlib import Path

import pytest

from farm_notary.chain import (
    CHAIN_FILE_NAME,
    ChainLink,
    chain_manifests,
    chain_run_dirs,
    load_chain,
    verify_chain,
    write_chain,
)
from farm_notary.manifest import build_manifest, write_manifest


def _make_run(tmp_path: Path, name: str, **kwargs) -> Path:
    d = tmp_path / name
    d.mkdir()
    (d / "output.csv").write_text(f"val\n{name}\n", encoding="utf-8")
    m = build_manifest(
        d,
        publish_patterns=["*.csv"],
        git_sha=kwargs.get("git_sha", "abc"),
        config=kwargs.get("config", {}),
    )
    write_manifest(m, d)
    return d


# ---------------------------------------------------------------------------
# chain_manifests
# ---------------------------------------------------------------------------


class TestChainManifests:
    def test_single_stage_root_parent_null(self, tmp_path):
        d = _make_run(tmp_path, "stage0")
        m = build_manifest(d, publish_patterns=["*.csv"])
        chain = chain_manifests([m])
        assert len(chain) == 1
        assert chain[0].parent_hash is None
        assert chain[0].stage == 0

    def test_two_stage_chain_parent_hash(self, tmp_path):
        d0 = _make_run(tmp_path, "s0", config={"x": 1})
        d1 = _make_run(tmp_path, "s1", config={"x": 2})
        m0 = build_manifest(d0, publish_patterns=["*.csv"])
        m1 = build_manifest(d1, publish_patterns=["*.csv"])
        chain = chain_manifests([m0, m1])
        assert chain[1].parent_hash == chain[0].link_hash

    def test_link_hash_recomputed(self, tmp_path):
        import hashlib

        d0 = _make_run(tmp_path, "r0")
        m0 = build_manifest(d0, publish_patterns=["*.csv"])
        chain = chain_manifests([m0])
        expected = hashlib.sha256(m0.content_hash().encode()).hexdigest()
        assert chain[0].link_hash == expected

    def test_custom_labels(self, tmp_path):
        d0 = _make_run(tmp_path, "p0")
        d1 = _make_run(tmp_path, "p1")
        m0 = build_manifest(d0, publish_patterns=["*.csv"])
        m1 = build_manifest(d1, publish_patterns=["*.csv"])
        chain = chain_manifests([m0, m1], stage_labels=["preprocess", "train"])
        assert chain[0].stage == "preprocess"
        assert chain[1].stage == "train"

    def test_label_length_mismatch_raises(self, tmp_path):
        d = _make_run(tmp_path, "x0")
        m = build_manifest(d, publish_patterns=["*.csv"])
        with pytest.raises(ValueError, match="stage_labels length"):
            chain_manifests([m], stage_labels=["a", "b"])


# ---------------------------------------------------------------------------
# verify_chain
# ---------------------------------------------------------------------------


class TestVerifyChain:
    def test_valid_chain_no_errors(self, tmp_path):
        d0 = _make_run(tmp_path, "v0", config={"a": 1})
        d1 = _make_run(tmp_path, "v1", config={"a": 2})
        m0 = build_manifest(d0, publish_patterns=["*.csv"])
        m1 = build_manifest(d1, publish_patterns=["*.csv"])
        chain = chain_manifests([m0, m1])
        assert verify_chain(chain) == []

    def test_tampered_content_hash_detected(self, tmp_path):
        d = _make_run(tmp_path, "t0")
        m = build_manifest(d, publish_patterns=["*.csv"])
        chain = chain_manifests([m])
        # Tamper the content_hash.
        link = chain[0]
        bad = ChainLink(
            stage=link.stage,
            manifest_path=link.manifest_path,
            content_hash="0" * 64,
            parent_hash=link.parent_hash,
            link_hash=link.link_hash,
        )
        errors = verify_chain([bad])
        assert errors  # link_hash no longer matches

    def test_root_with_parent_hash_is_error(self, tmp_path):
        d = _make_run(tmp_path, "u0")
        m = build_manifest(d, publish_patterns=["*.csv"])
        chain = chain_manifests([m])
        bad = ChainLink(
            stage=chain[0].stage,
            manifest_path=chain[0].manifest_path,
            content_hash=chain[0].content_hash,
            parent_hash="somehash",
            link_hash=chain[0].link_hash,
        )
        errors = verify_chain([bad])
        assert any("root link must have parent_hash=null" in e for e in errors)


# ---------------------------------------------------------------------------
# write_chain / load_chain
# ---------------------------------------------------------------------------


class TestWriteLoadChain:
    def test_roundtrip(self, tmp_path):
        d0 = _make_run(tmp_path, "w0")
        d1 = _make_run(tmp_path, "w1")
        m0 = build_manifest(d0, publish_patterns=["*.csv"])
        m1 = build_manifest(d1, publish_patterns=["*.csv"])
        chain = chain_manifests([m0, m1])
        path = write_chain(chain, tmp_path)
        assert path.name == CHAIN_FILE_NAME
        loaded = load_chain(path)
        assert len(loaded) == 2
        assert loaded[-1].link_hash == chain[-1].link_hash


# ---------------------------------------------------------------------------
# chain_run_dirs
# ---------------------------------------------------------------------------


class TestChainRunDirs:
    def test_builds_and_writes_chain(self, tmp_path):
        d0 = _make_run(tmp_path, "rd0")
        d1 = _make_run(tmp_path, "rd1")
        chain = chain_run_dirs([d0, d1])
        assert len(chain) == 2
        assert (d1 / CHAIN_FILE_NAME).exists()

    def test_custom_output_dir(self, tmp_path):
        d0 = _make_run(tmp_path, "co0")
        d1 = _make_run(tmp_path, "co1")
        out = tmp_path / "out"
        out.mkdir()
        chain_run_dirs([d0, d1], output_dir=out)
        assert (out / CHAIN_FILE_NAME).exists()

    def test_verify_chain_after_build(self, tmp_path):
        d0 = _make_run(tmp_path, "vb0", config={"step": 0})
        d1 = _make_run(tmp_path, "vb1", config={"step": 1})
        d2 = _make_run(tmp_path, "vb2", config={"step": 2})
        chain = chain_run_dirs([d0, d1, d2])
        errors = verify_chain(chain, chain_dir=tmp_path)
        assert errors == []
