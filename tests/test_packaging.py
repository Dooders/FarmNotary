"""Package metadata, public API, and typed-package marker stay aligned."""

import importlib.metadata
from pathlib import Path

import farm_notary
from farm_notary.schema import MANIFEST_VERSION, TOOL_VERSION


def test_py_typed_is_shipped_with_the_package():
    package_dir = Path(farm_notary.__file__).resolve().parent
    assert (package_dir / "py.typed").is_file()


def test_all_matches_public_exports():
    exported = {
        name
        for name in farm_notary.__all__
        if name != "__version__"
    }
    imported = {
        name
        for name in dir(farm_notary)
        if not name.startswith("_")
    }
    assert exported == imported
    missing = [name for name in farm_notary.__all__ if not hasattr(farm_notary, name)]
    assert missing == []
    assert farm_notary.__version__ == TOOL_VERSION
    assert farm_notary.TOOL_VERSION == TOOL_VERSION
    assert farm_notary.MANIFEST_VERSION == MANIFEST_VERSION
    assert "infer_claim_level" in farm_notary.__all__
    assert "notarize_run" in farm_notary.__all__
    assert exported <= imported


def test_all_is_sorted_and_unique():
    names = list(farm_notary.__all__)
    assert names == sorted(names)
    assert len(names) == len(set(names))


def test_pyproject_declares_typed_package_and_lint_extra():
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'farm_notary = ["py.typed"]' in text
    assert '"Typing :: Typed"' in text
    assert "Homepage" in text
    assert "ruff" in text
    assert "mypy" in text
    extras = importlib.metadata.metadata("farm-notary")
    assert extras["Name"] == "farm-notary"
    dist = importlib.metadata.distribution("farm-notary")
    names = {extra for extra in (dist.metadata.get_all("Provides-Extra") or [])}
    assert {"ots", "chain", "sigstore", "dev", "lint"} <= names
