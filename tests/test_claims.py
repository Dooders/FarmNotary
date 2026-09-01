"""Claim levels and paper sentences — documented in CLAIMS.md, previously untested."""

import json
from pathlib import Path

from farm_notary.campaign import Campaign
from farm_notary.claims import (
    CLAIM_BITWISE,
    CLAIM_BITWISE_DECLARED,
    CLAIM_BITWISE_DERIVED,
    CLAIM_BITWISE_DERIVED_DECLARED,
    CLAIM_BYTES,
    CLAIM_DERIVED,
    CLAIM_DERIVED_DECLARED,
    infer_claim_level,
    scoped_reproducibility_sentence,
)
from farm_notary.manifest import RECEIPT_NAME, Manifest, build_manifest, write_manifest


def _run(tmp_path: Path, *, derived: bool = False) -> tuple[Path, Manifest]:
    run = tmp_path / "run"
    run.mkdir()
    (run / "summary.csv").write_text("ok\n", encoding="utf-8")
    (run / "trials.csv").write_text("t\n", encoding="utf-8")
    config = {"seed": 0}
    if derived:
        config["notary"] = {
            "publish": ["*.csv"],
            "derived_from": [
                {
                    "outputs": ["summary.csv"],
                    "sources": ["trials.csv"],
                    "command": "true",
                    "mode": "verify",
                }
            ],
        }
    manifest = build_manifest(
        run, publish_patterns=["*.csv"], git_sha="abc", config=config, runner="consensus"
    )
    write_manifest(manifest, run)
    return run, manifest


def _write_receipt(run: Path, manifest: Manifest, *, ok: bool = True, bound: bool = True) -> None:
    receipt = {
        "ok": ok,
        "original_manifest_hash": manifest.content_hash() if bound else "00" * 32,
        "matched": ["summary.csv"],
        "mismatched": [],
        "missing": [],
    }
    (run / RECEIPT_NAME).write_text(json.dumps(receipt), encoding="utf-8")


def test_bytes_when_nothing_else_is_present(tmp_path: Path):
    run, manifest = _run(tmp_path)
    assert infer_claim_level(manifest, run) == CLAIM_BYTES
    assert infer_claim_level(manifest) == CLAIM_BYTES


def test_derived_rules_without_receipt_are_declared_not_earned(tmp_path: Path):
    run, manifest = _run(tmp_path, derived=True)
    assert manifest.derived_from
    assert infer_claim_level(manifest, run) == CLAIM_DERIVED_DECLARED
    assert infer_claim_level(manifest, run, derived_ok=True) == CLAIM_DERIVED


def test_derived_from_config_only_still_declares(tmp_path: Path):
    run, manifest = _run(tmp_path)
    manifest.derived_from = []
    manifest.config = {
        "notary": {
            "derived_from": [{"outputs": ["a"], "sources": ["b"], "command": "x"}]
        }
    }
    assert infer_claim_level(manifest, run) == CLAIM_DERIVED_DECLARED


def test_valid_receipt_is_bitwise(tmp_path: Path):
    run, manifest = _run(tmp_path)
    _write_receipt(run, manifest, ok=True, bound=True)
    assert infer_claim_level(manifest, run) == CLAIM_BITWISE


def test_unbound_or_failed_receipt_is_bitwise_declared(tmp_path: Path):
    run, manifest = _run(tmp_path)
    _write_receipt(run, manifest, ok=True, bound=False)
    assert infer_claim_level(manifest, run) == CLAIM_BITWISE_DECLARED
    _write_receipt(run, manifest, ok=False, bound=True)
    assert infer_claim_level(manifest, run) == CLAIM_BITWISE_DECLARED


def test_receipt_plus_derived_rules(tmp_path: Path):
    run, manifest = _run(tmp_path, derived=True)
    _write_receipt(run, manifest, ok=True, bound=True)
    assert infer_claim_level(manifest, run) == CLAIM_BITWISE_DERIVED_DECLARED
    assert infer_claim_level(manifest, run, derived_ok=True) == CLAIM_BITWISE_DERIVED
    _write_receipt(run, manifest, ok=False, bound=True)
    assert infer_claim_level(manifest, run) == CLAIM_BITWISE_DECLARED
    assert infer_claim_level(manifest, run, derived_ok=True) == CLAIM_BITWISE_DECLARED


def test_unreadable_receipt_is_ignored(tmp_path: Path):
    run, manifest = _run(tmp_path)
    (run / RECEIPT_NAME).write_text("not-json", encoding="utf-8")
    assert infer_claim_level(manifest, run) == CLAIM_BYTES


def test_sentence_mentions_seed_and_unconfirmed_derivation(tmp_path: Path):
    _, manifest = _run(tmp_path, derived=True)
    text = scoped_reproducibility_sentence(manifest)
    assert "from seed 0" in text
    assert "the consensus record" in text
    assert "1-ulp" in text
    assert "--verify-derived" in text
    assert "summary.csv" in text
    assert "trials.csv" in text


def test_sentence_derived_ok_true_and_false(tmp_path: Path):
    _, manifest = _run(tmp_path, derived=True)
    ok = scoped_reproducibility_sentence(manifest, derived_ok=True)
    assert "recompute exactly" in ok
    bad = scoped_reproducibility_sentence(manifest, derived_ok=False)
    assert "were not confirmed" in bad
    assert "recompute exactly" not in bad


def test_campaign_sentence_contiguous_and_sparse_seeds():
    contiguous = Campaign(
        name="sweep",
        runs=[{"seed": i, "content_hash": "aa"} for i in range(3)],
        config_hash="deadbeef",
        environment={"os": "Linux", "arch": "x86_64", "python": "3.12"},
    )
    text = scoped_reproducibility_sentence(contiguous)
    assert "sweep of 3 runs" in text
    assert "seeds 0…2" in text
    assert "sharing config hash deadbeef" in text
    assert "x86_64 Linux" in text

    sparse = Campaign(name="odd", runs=[{"seed": 0}, {"seed": 4}])
    sparse_text = scoped_reproducibility_sentence(sparse)
    assert "seeds 0, 4" in sparse_text
    assert "…" not in sparse_text
