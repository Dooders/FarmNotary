"""Scoped bitwise sentence: only x86-64 Linux is a demonstrated claim."""

from farm_notary.scope import (
    ALLOWED_SENTENCE,
    CROSS_HARDWARE_NOT_A_CLAIM,
    DEMONSTRATED_SCOPES,
    environment_machine,
    format_bitwise_status,
    machine_label,
    scoped_clause,
)


def test_demonstrated_scopes_stay_narrow_until_arm_receipts_exist():
    assert DEMONSTRATED_SCOPES == frozenset({"x86-64 Linux"})
    assert ALLOWED_SENTENCE == (
        "byte-identical on x86-64 Linux in a pinned environment"
    )


def test_canonical_machine_labels():
    assert machine_label("Linux", "x86_64") == "x86-64 Linux"
    assert machine_label("Linux", "amd64") == "x86-64 Linux"
    assert machine_label("Linux", "aarch64") == "ARM64 Linux"
    assert machine_label("Linux", "arm64") == "ARM64 Linux"
    assert machine_label("Darwin", "arm64") == "ARM64 macOS"
    assert machine_label("Darwin", "x86_64") == "x86-64 macOS"
    assert machine_label("", "") == "unknown"


def test_old_receipts_parse_platform_string():
    assert (
        environment_machine({"platform": "Linux-6.11.0-x86_64-with-glibc2.39"})
        == "x86-64 Linux"
    )
    assert (
        environment_machine({"platform": "macOS-14.6-arm64-arm-64bit"})
        == "ARM64 macOS"
    )
    assert (
        environment_machine({"platform": "Linux-6.8.0-aarch64-with-glibc2.39"})
        == "ARM64 Linux"
    )


def test_system_and_machine_fields_win_over_platform():
    assert (
        environment_machine(
            {
                "system": "Linux",
                "machine": "aarch64",
                "platform": "Linux-6.11.0-x86_64-with-glibc2.39",
            }
        )
        == "ARM64 Linux"
    )


def test_partial_environment_fills_only_the_missing_field():
    """An explicit system/machine is kept; only the unset field is parsed."""
    assert (
        environment_machine(
            {
                "system": "Linux",
                "platform": "macOS-14.6-arm64-arm-64bit",
            }
        )
        == "ARM64 Linux"
    )
    assert (
        environment_machine(
            {
                "machine": "x86_64",
                "platform": "macOS-14.6-arm64-arm-64bit",
            }
        )
        == "x86-64 Linux"
    )


def test_allowed_sentence_only_on_demonstrated_ok_receipt():
    linux = {"system": "Linux", "machine": "x86_64"}
    assert scoped_clause(linux, ok=True) == ALLOWED_SENTENCE
    assert format_bitwise_status("8/8", linux, ok=True) == (
        f"8/8; {ALLOWED_SENTENCE}"
    )


def test_other_hardware_cannot_emit_the_allowed_sentence():
    arm_linux = {"system": "Linux", "machine": "aarch64"}
    arm_mac = {"system": "Darwin", "machine": "arm64"}
    for env, label in (
        (arm_linux, "ARM64 Linux"),
        (arm_mac, "ARM64 macOS"),
    ):
        status = format_bitwise_status("8/8", env, ok=True)
        assert ALLOWED_SENTENCE not in status
        assert status == f"8/8 on {label}; {CROSS_HARDWARE_NOT_A_CLAIM}"


def test_failed_receipt_does_not_emit_the_allowed_sentence():
    linux = {"system": "Linux", "machine": "x86_64"}
    status = format_bitwise_status("5/8", linux, ok=False)
    assert status == "fail — 5/8"
    assert ALLOWED_SENTENCE not in status
