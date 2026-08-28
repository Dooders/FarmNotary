import platform

from farm_notary.fingerprint import (
    environment_scope,
    fingerprint_fields,
    normalize_arch,
    numpy_build_info,
)
from farm_notary.manifest import build_manifest, capture_environment


def test_fingerprint_fields_are_first_class():
    fields = fingerprint_fields()
    assert fields["os"] == platform.system()
    assert fields["arch"] == platform.machine()
    assert fields["python"] == platform.python_version()
    assert fields["python_implementation"]


def test_normalize_arch_aliases():
    assert normalize_arch("AMD64") == "x86_64"
    assert normalize_arch("aarch64") == "arm64"
    assert normalize_arch("x86_64") == "x86_64"


def test_capture_environment_includes_os_arch_python(tmp_path):
    env = capture_environment()
    assert env["os"]
    assert env["arch"]
    assert env["python"]
    assert len(env["packages_hash"]) == 64


def test_build_manifest_records_fingerprint(tmp_path):
    (tmp_path / "summary.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    manifest = build_manifest(tmp_path, publish_patterns=["*.csv"], git_sha="abc")
    assert manifest.environment["os"] == platform.system()
    assert manifest.environment["arch"] == platform.machine()
    assert manifest.environment["python"]


def test_environment_scope_names_machine_class():
    text = environment_scope(
        {
            "os": "Linux",
            "arch": "x86_64",
            "python": "3.12.3",
            "numpy": {"version": "2.0.0", "blas": "openblas"},
            "lockfile": "requirements.lock",
            "lockfile_sha256": "ab",
        }
    )
    assert "x86_64 Linux" in text
    assert "Python 3.12.3" in text
    assert "openblas" in text
    assert "requirements.lock" in text


def test_numpy_build_info_without_numpy(monkeypatch):
    import farm_notary.fingerprint as fp
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "numpy":
            raise ImportError("no numpy")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert fp.numpy_build_info() is None


def test_numpy_build_info_when_installed():
    try:
        import numpy  # noqa: F401
    except ImportError:
        info = numpy_build_info()
        assert info is None
        return
    info = numpy_build_info()
    assert info is not None
    assert "version" in info
