"""Checked-in JSON Schema files stay aligned with the code."""

import json
from pathlib import Path

import jsonschema
import jsonschema.exceptions

from farm_notary.campaign import build_campaign, write_campaign
from farm_notary.manifest import build_manifest, write_manifest
from farm_notary.registry import write_registry
from farm_notary.schema import REQUIRED_KEYS

SCHEMAS = Path("schemas")


def _load(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


def test_manifest_schema_is_valid_json_schema():
    schema = _load("farmnotary.manifest.v1.json")
    jsonschema.Draft202012Validator.check_schema(schema)


def test_campaign_schema_is_valid_json_schema():
    schema = _load("farmnotary.campaign.v1.json")
    jsonschema.Draft202012Validator.check_schema(schema)


def test_registry_schema_is_valid_json_schema():
    schema = _load("farmnotary.registry.v1.json")
    jsonschema.Draft202012Validator.check_schema(schema)


def test_manifest_schema_required_keys_match_code():
    schema = _load("farmnotary.manifest.v1.json")
    assert schema["required"] == list(REQUIRED_KEYS)
    assert schema["properties"]["schema"]["const"] == "farmnotary.manifest.v1"
    for key in ("withheld_salt", "withheld_root", "withheld_classes", "ci_provenance"):
        assert key in schema["properties"]
    assert "Stamp field" in schema["properties"]["identity"]["description"]


def test_built_manifest_has_schema_required_keys(tmp_path: Path):
    (tmp_path / "summary.csv").write_text("ok\n", encoding="utf-8")
    (tmp_path / "extra.json").write_text("{}\n", encoding="utf-8")
    manifest = build_manifest(
        tmp_path,
        publish_patterns=["*.csv"],
        git_sha="abc",
        runner="test-runner",
        command="python run.py",
    )
    body = manifest.to_dict()
    schema = _load("farmnotary.manifest.v1.json")
    for key in schema["required"]:
        assert key in body
    assert "withheld_root" in body
    write_manifest(manifest, tmp_path)
    # Validate the written manifest against the JSON Schema
    written = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    jsonschema.validate(written, schema)


def test_campaign_and_registry_schema_ids():
    campaign = _load("farmnotary.campaign.v1.json")
    registry = _load("farmnotary.registry.v1.json")
    assert campaign["properties"]["schema"]["const"] == "farmnotary.campaign.v1"
    assert registry["properties"]["schema"]["const"] == "farmnotary.registry.v1"
    assert "identity" in campaign["properties"]


def test_campaign_and_registry_instances_cover_required(tmp_path: Path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "summary.csv").write_text("ok\n", encoding="utf-8")
    from farm_notary.manifest import build_manifest, write_manifest

    write_manifest(
        build_manifest(run, publish_patterns=["*.csv"], git_sha="abc", config={"seed": 0}),
        run,
    )
    campaign = build_campaign([run], name="sweep")
    write_campaign(campaign, tmp_path)
    body = json.loads((tmp_path / "campaign.json").read_text(encoding="utf-8"))
    campaign_schema = _load("farmnotary.campaign.v1.json")
    for key in campaign_schema["required"]:
        assert key in body
    # Validate the campaign instance against the JSON Schema
    jsonschema.validate(body, campaign_schema)

    write_registry(
        [
            {
                "experiment": "sweep",
                "seed": 0,
                "cid": None,
                "claim_level": "bytes",
                "date": "2026-08-31",
                "content_hash": campaign.content_hash(),
            }
        ],
        tmp_path / "registry.md",
    )
    registry = json.loads((tmp_path / "registry.json").read_text(encoding="utf-8"))
    registry_schema = _load("farmnotary.registry.v1.json")
    for key in registry_schema["required"]:
        assert key in registry
    # Validate the registry instance against the JSON Schema
    jsonschema.validate(registry, registry_schema)

