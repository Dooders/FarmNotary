"""Package metadata, public API, and typed-package marker stay aligned."""

import importlib.metadata
import types
from pathlib import Path

import farm_notary
from farm_notary.schema import MANIFEST_VERSION, TOOL_VERSION


def test_py_typed_is_shipped_with_the_package():
    package_dir = Path(farm_notary.__file__).resolve().parent
    assert (package_dir / "py.typed").is_file()


def test_all_matches_public_exports():
    """``__all__`` is the public API. Submodules bound by imports are not."""
    missing = [name for name in farm_notary.__all__ if not hasattr(farm_notary, name)]
    assert missing == []

    exported = {name for name in farm_notary.__all__ if name != "__version__"}
    public_api = {
        name
        for name in dir(farm_notary)
        if not name.startswith("_")
        and not isinstance(getattr(farm_notary, name), types.ModuleType)
    }
    assert exported == public_api
    assert farm_notary.__version__ == TOOL_VERSION
    assert farm_notary.TOOL_VERSION == TOOL_VERSION
    assert farm_notary.MANIFEST_VERSION == MANIFEST_VERSION
    assert "infer_claim_level" in farm_notary.__all__
    assert "notarize_run" in farm_notary.__all__


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
