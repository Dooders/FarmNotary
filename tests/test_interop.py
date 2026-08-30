"""Tests for farm_notary.interop — SLSA/in-toto, RO-Crate, and C2PA emission."""

import json
from pathlib import Path

import pytest

from farm_notary.manifest import build_manifest, write_manifest
from farm_notary.interop import (
    INTEROP_FORMATS,
    emit_c2pa,
    emit_interop,
    emit_ro_crate,
    emit_slsa,
    to_c2pa_claim,
    to_ro_crate,
    to_slsa_provenance,
)


def _make_manifest(tmp_path: Path):
    (tmp_path / "output.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    return build_manifest(
        tmp_path,
        publish_patterns=["*.csv"],
        git_sha="deadbeef",
        config={"lr": 0.01},
    )


# ---------------------------------------------------------------------------
# SLSA / in-toto
# ---------------------------------------------------------------------------


class TestSLSAProvenance:
    def test_statement_type(self, tmp_path):
        m = _make_manifest(tmp_path)
        stmt = to_slsa_provenance(m)
        assert stmt["_type"] == "https://in-toto.io/Statement/v1"
        assert stmt["predicateType"] == "https://slsa.dev/provenance/v1"

    def test_subjects_from_artifact_hashes(self, tmp_path):
        m = _make_manifest(tmp_path)
        stmt = to_slsa_provenance(m)
        subjects = {s["name"]: s["digest"]["sha256"] for s in stmt["subject"]}
        assert "output.csv" in subjects
        # The hash stored in the manifest uses a "sha256:" prefix; the SLSA
        # statement should strip it.
        raw_hash = m.artifact_hashes["output.csv"].removeprefix("sha256:")
        assert subjects["output.csv"] == raw_hash

    def test_content_hash_in_internal_parameters(self, tmp_path):
        m = _make_manifest(tmp_path)
        stmt = to_slsa_provenance(m)
        internal = stmt["predicate"]["buildDefinition"]["internalParameters"]
        assert internal["content_hash"] == m.content_hash()

    def test_emit_writes_file(self, tmp_path):
        m = _make_manifest(tmp_path)
        write_manifest(m, tmp_path)
        path = emit_slsa(m, tmp_path)
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["_type"] == "https://in-toto.io/Statement/v1"

    def test_ci_provenance_sets_github_build_type(self, tmp_path):
        m = _make_manifest(tmp_path)
        object.__setattr__(
            m,
            "ci_provenance",
            {
                "kind": "github_actions",
                "sha": "abc123",
                "repository": "Dooders/FarmNotary",
                "run_id": "999",
            },
        )
        stmt = to_slsa_provenance(m)
        build_type = stmt["predicate"]["buildDefinition"]["buildType"]
        assert "github-actions" in build_type


# ---------------------------------------------------------------------------
# RO-Crate
# ---------------------------------------------------------------------------


class TestROCrate:
    def test_context_present(self, tmp_path):
        m = _make_manifest(tmp_path)
        doc = to_ro_crate(m)
        assert doc["@context"] == "https://w3id.org/ro/crate/1.1/context"

    def test_root_dataset_present(self, tmp_path):
        m = _make_manifest(tmp_path)
        doc = to_ro_crate(m)
        entities = {e["@id"]: e for e in doc["@graph"]}
        root = entities["./"]
        assert root["@type"] == "Dataset"
        assert "output.csv" in [p["@id"] for p in root["hasPart"]]

    def test_content_hash_stamped(self, tmp_path):
        m = _make_manifest(tmp_path)
        doc = to_ro_crate(m)
        entities = {e["@id"]: e for e in doc["@graph"]}
        root = entities["./"]
        assert root["farmnotary:contentHash"] == m.content_hash()

    def test_emit_writes_file(self, tmp_path):
        m = _make_manifest(tmp_path)
        write_manifest(m, tmp_path)
        path = emit_ro_crate(m, tmp_path)
        assert path.name == "ro-crate-metadata.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert "@graph" in data


# ---------------------------------------------------------------------------
# C2PA
# ---------------------------------------------------------------------------


class TestC2PA:
    def test_claim_generator(self, tmp_path):
        m = _make_manifest(tmp_path)
        claim = to_c2pa_claim(m)
        assert claim["claim_generator"].startswith("FarmNotary/")

    def test_content_hash_in_farmnotary_field(self, tmp_path):
        m = _make_manifest(tmp_path)
        claim = to_c2pa_claim(m)
        assert claim["farmnotary"]["content_hash"] == m.content_hash()

    def test_artifact_assertions(self, tmp_path):
        m = _make_manifest(tmp_path)
        claim = to_c2pa_claim(m)
        names = [a["data"]["name"] for a in claim["assertions"]]
        assert "output.csv" in names

    def test_emit_writes_file(self, tmp_path):
        m = _make_manifest(tmp_path)
        write_manifest(m, tmp_path)
        path = emit_c2pa(m, tmp_path)
        assert path.name == "c2pa-claim.json"
        assert path.exists()


# ---------------------------------------------------------------------------
# emit_interop convenience
# ---------------------------------------------------------------------------


class TestEmitInterop:
    def test_emits_all_formats_by_default(self, tmp_path):
        m = _make_manifest(tmp_path)
        write_manifest(m, tmp_path)
        written = emit_interop(m, tmp_path)
        assert set(written.keys()) == set(INTEROP_FORMATS)
        for path in written.values():
            assert path.exists()

    def test_emits_selected_formats(self, tmp_path):
        m = _make_manifest(tmp_path)
        write_manifest(m, tmp_path)
        written = emit_interop(m, tmp_path, formats=("slsa",))
        assert list(written.keys()) == ["slsa"]

    def test_unknown_format_raises(self, tmp_path):
        m = _make_manifest(tmp_path)
        with pytest.raises(ValueError, match="unknown interop format"):
            emit_interop(m, tmp_path, formats=("bogus",))
