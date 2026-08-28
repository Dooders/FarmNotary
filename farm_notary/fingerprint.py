"""First-class execution-environment fingerprint.

Lockfile hash alone cannot keep “bitwise on x86-64 Linux, pinned env” honest:
the same lockfile on Apple Silicon or a different BLAS can produce a 1-ulp
diff.  The fingerprint records OS, arch, Python, and the numpy/BLAS build so
a scoped claim names the machine class it was earned on.
"""

from __future__ import annotations

import platform
from typing import Any, Mapping, Optional


def normalize_arch(arch: str) -> str:
    """Collapse common aliases so index/paper-pack sentences stay comparable."""
    mapping = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "aarch64": "arm64",
        "arm64": "arm64",
        "x86_64": "x86_64",
    }
    return mapping.get(arch.lower(), arch)


def numpy_build_info() -> Optional[dict]:
    """Return numpy version + BLAS/LAPACK build metadata, or None if absent."""
    try:
        import numpy as np
    except ImportError:
        return None

    info: dict = {"version": getattr(np, "__version__", "unknown")}
    cfg: Any = None
    try:
        cfg = np.show_config(mode="dicts")
    except TypeError:
        cfg = None
    except Exception:
        cfg = None

    if not isinstance(cfg, dict):
        return info

    build_deps = cfg.get("Build Dependencies") or cfg.get("build_dependencies") or {}
    if not isinstance(build_deps, dict):
        return info

    blas = build_deps.get("blas")
    if isinstance(blas, dict):
        if blas.get("name"):
            info["blas"] = str(blas["name"])
        if blas.get("version"):
            info["blas_version"] = str(blas["version"])
        conf = blas.get("openblas configuration") or blas.get("openblas_configuration")
        if conf:
            info["blas_config"] = str(conf).replace("\n", " ").strip()[:200]

    lapack = build_deps.get("lapack")
    if isinstance(lapack, dict) and lapack.get("name"):
        info["lapack"] = str(lapack["name"])

    machine = cfg.get("Machine Information") or cfg.get("machine_information") or {}
    host = machine.get("host") if isinstance(machine, dict) else None
    if isinstance(host, dict) and host.get("cpu"):
        info["numpy_host_cpu"] = str(host["cpu"])

    return info


def fingerprint_fields() -> dict:
    """OS / arch / Python identity used as first-class environment keys."""
    return {
        "os": platform.system(),
        "arch": platform.machine(),
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
    }


def environment_scope(env: Optional[Mapping[str, Any]]) -> str:
    """One-line machine-class description for scoped reproducibility claims."""
    env = dict(env or {})
    os_name = env.get("os") or "unknown-OS"
    arch = normalize_arch(str(env.get("arch") or "unknown-arch"))
    python = env.get("python")
    parts = [f"{arch} {os_name}"]
    if python:
        parts.append(f"Python {python}")
    numpy = env.get("numpy")
    if isinstance(numpy, dict):
        version = numpy.get("version")
        blas = numpy.get("blas")
        if version and blas:
            parts.append(f"numpy {version} / {blas}")
        elif version:
            parts.append(f"numpy {version}")
        elif blas:
            parts.append(str(blas))
    if env.get("lockfile_sha256"):
        lock = env.get("lockfile") or "lockfile"
        parts.append(f"pinned {lock}")
    return ", ".join(parts)
