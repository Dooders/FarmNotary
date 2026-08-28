"""What bitwise identity has been demonstrated — the sentence the tool may emit.

CLAIMS.md is the source of truth. Do not add a hardware class here until a
reproduction receipt exists for it. Cross-hardware / cross-BLAS identity is
not a claim.
"""

from __future__ import annotations

from typing import Mapping, Optional

# Hardware classes for which a same-environment bitwise receipt exists.
# Until Linux ARM and macOS ARM receipts exist, this stays a singleton.
DEMONSTRATED_SCOPES = frozenset({"x86-64 Linux"})

ALLOWED_SENTENCE = "byte-identical on x86-64 Linux in a pinned environment"

CROSS_HARDWARE_NOT_A_CLAIM = "cross-hardware bitwise identity is not a claim"


def machine_label(system: str, machine: str) -> str:
    """Canonical hardware class: 'x86-64 Linux', 'ARM64 Linux', 'ARM64 macOS'."""
    sys_norm = (system or "").lower()
    mach = (machine or "").lower()
    if sys_norm == "linux" and mach in {"x86_64", "amd64"}:
        return "x86-64 Linux"
    if sys_norm == "linux" and mach in {"aarch64", "arm64"}:
        return "ARM64 Linux"
    if sys_norm == "darwin" and mach in {"arm64", "aarch64"}:
        return "ARM64 macOS"
    if sys_norm == "darwin" and mach in {"x86_64", "amd64"}:
        return "x86-64 macOS"
    if sys_norm == "windows" and mach in {"amd64", "x86_64"}:
        return "x86-64 Windows"
    parts = [p for p in (system, machine) if p]
    return " ".join(parts) if parts else "unknown"


def _parse_platform_string(plat: str) -> tuple[str, str]:
    """Best-effort parse of ``platform.platform()`` for old receipts."""
    low = (plat or "").lower()
    system = ""
    if low.startswith("linux") or "-linux-" in low or low.startswith("linux-"):
        system = "Linux"
    elif "darwin" in low or "macos" in low or "mac-os" in low:
        system = "Darwin"
    elif "windows" in low:
        system = "Windows"
    machine = ""
    if "x86_64" in low or "amd64" in low:
        machine = "x86_64"
    elif "aarch64" in low:
        machine = "aarch64"
    elif "arm64" in low:
        machine = "arm64"
    return system, machine


def environment_machine(environment: Optional[Mapping] = None) -> str:
    """Hardware class from a manifest/receipt ``environment`` dict."""
    env = dict(environment or {})
    system = env.get("system") or ""
    machine = env.get("machine") or ""
    if not system or not machine:
        parsed_system, parsed_machine = _parse_platform_string(
            str(env.get("platform") or "")
        )
        system = system or parsed_system
        machine = machine or parsed_machine
    return machine_label(str(system), str(machine))


def scoped_clause(environment: Optional[Mapping] = None, *, ok: bool = True) -> str:
    """Scope clause for a bitwise result. Never widens past DEMONSTRATED_SCOPES."""
    label = environment_machine(environment)
    if ok and label in DEMONSTRATED_SCOPES:
        return ALLOWED_SENTENCE
    return f"on {label}; {CROSS_HARDWARE_NOT_A_CLAIM}"


def format_bitwise_status(
    score: str,
    environment: Optional[Mapping] = None,
    *,
    ok: bool,
) -> str:
    """Claim-card / reproduce status: ``N/M`` plus the allowed sentence.

    A failed receipt reports ``fail — N/M`` and does not emit the allowed
    sentence. A passing receipt on a demonstrated machine appends
    ``ALLOWED_SENTENCE``. A passing receipt anywhere else still reports
    ``N/M`` but refuses a cross-hardware claim.
    """
    if not ok:
        return f"fail — {score}"
    clause = scoped_clause(environment, ok=True)
    if clause == ALLOWED_SENTENCE:
        return f"{score}; {ALLOWED_SENTENCE}"
    return f"{score} {clause}"
