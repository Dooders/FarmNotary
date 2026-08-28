"""Classify reproduction byte-diffs so labs do not treat every mismatch as science.

The consensus experiment's first receipt was 7/7 until REPORT.md embedded the
output path; recording ``{run_dir}`` made it 8/8. That is a packaging bug, not
a failed result. This module names the common packaging causes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

KIND_EMBEDDED_PATH = "embedded_absolute_path"
KIND_TIMESTAMP = "timestamp"
KIND_FLOAT_FORMAT = "float_print_format"
KIND_VIDEO_ENCODER = "video_encoder"
KIND_UNCLASSIFIED = "unclassified"

VIDEO_SUFFIXES = frozenset({".mp4", ".webm", ".mov", ".avi", ".mkv", ".m4v"})

# ISO dates, optional time and timezone. Also bare HH:MM:SS clock readings.
_ISO_TS = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?"
)
_CLOCK = re.compile(r"\b\d{1,2}:\d{2}:\d{2}(?:\.\d+)?\b")

# Decimal or scientific floats, not bare integers (those are usually counts).
_FLOAT = re.compile(
    r"(?<![A-Za-z0-9_])[-+]?(?:\d+\.\d*|\.\d+)(?:[eE][-+]?\d+)?"
    r"|(?<![A-Za-z0-9_])[-+]?\d+[eE][-+]?\d+"
)

# Absolute POSIX or Windows paths. Requires at least two components so
# "6/7" and "8/8" in prose do not match.
_ABS_PATH = re.compile(
    r"(?:[A-Za-z]:[\\/]|/)(?:[^\s'\"`,;:)\]}]+[\\/])+[^\s'\"`,;:)\]}]+"
)

_HINTS = {
    KIND_EMBEDDED_PATH: (
        "record the command with {run_dir}; do not embed the output path"
    ),
    KIND_TIMESTAMP: (
        "a clock reading in the artifact is not a result; write times from "
        "the seed/config, or --ignore the file"
    ),
    KIND_FLOAT_FORMAT: (
        "same numbers, different spelling; pin a print format "
        "(pandas: float_precision='round_trip')"
    ),
    KIND_VIDEO_ENCODER: "encoder output is not bit-stable; use --ignore '*.mp4'",
}


@dataclass(frozen=True)
class MismatchDiagnosis:
    """One artifact's byte-diff, named as a packaging cause when we can."""

    artifact: str
    kinds: Tuple[str, ...]
    detail: str
    hint: str
    explains: bool

    def to_dict(self) -> dict:
        return {
            "artifact": self.artifact,
            "kinds": list(self.kinds),
            "detail": self.detail,
            "hint": self.hint,
            "explains": self.explains,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MismatchDiagnosis":
        kinds = tuple(str(k) for k in (data.get("kinds") or ()) if k)
        return cls(
            artifact=str(data.get("artifact") or ""),
            kinds=kinds or (KIND_UNCLASSIFIED,),
            detail=str(data.get("detail") or ""),
            hint=str(data.get("hint") or ""),
            explains=bool(data.get("explains")),
        )

    def summary_lines(self) -> List[str]:
        kinds = ", ".join(self.kinds)
        lines = [f"mismatch: {self.artifact} — {kinds}"]
        if self.detail:
            lines.append(f"  {self.detail}")
        if self.explains and self.hint:
            lines.append(f"  not a science failure: {self.hint}")
        else:
            lines.append("  byte-diff is not a science verdict")
            if self.hint:
                lines.append(f"  {self.hint}")
        return lines


def _unique(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _path_variants(path: Path) -> List[str]:
    variants: List[str] = []
    for candidate in (path, path.resolve()):
        variants.append(str(candidate))
        variants.append(candidate.as_posix())
        variants.append(str(candidate) + "/")
        variants.append(candidate.as_posix() + "/")
    return _unique(variants)


def _as_text(data: bytes) -> Optional[str]:
    if b"\x00" in data[:4096]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _is_video(name: str, data: bytes) -> bool:
    suffix = Path(name).suffix.lower()
    if suffix in VIDEO_SUFFIXES:
        return True
    if len(data) >= 8 and data[4:8] == b"ftyp":
        return True
    return False


def _mask_timestamps(text: str) -> str:
    return _CLOCK.sub("<TS>", _ISO_TS.sub("<TS>", text))


def _mask_abs_paths(text: str) -> str:
    return _ABS_PATH.sub("<PATH>", text)


def _canon_floats(text: str) -> str:
    def repl(match: re.Match) -> str:
        raw = match.group(0)
        try:
            return format(float(raw), ".17g")
        except ValueError:
            return raw

    return _FLOAT.sub(repl, text)


def _substitute_run_dirs(text: str, src: Path, dst: Path) -> str:
    out = text
    replacements = list(zip(_path_variants(src), _path_variants(dst)))
    # Longest source first so /tmp/run-a/figures beats /tmp/run-a.
    for src_s, dst_s in sorted(replacements, key=lambda p: len(p[0]), reverse=True):
        if src_s in out:
            out = out.replace(src_s, dst_s)
    return out


def _diff_paths(left: str, right: str) -> List[str]:
    left_paths = set(_ABS_PATH.findall(left))
    right_paths = set(_ABS_PATH.findall(right))
    return sorted((left_paths | right_paths) - (left_paths & right_paths))


def _apply(
    left: str, right: str, fn
) -> Tuple[str, str]:
    return fn(left), fn(right)


def diagnose_mismatch(
    name: str,
    original: Path,
    reproduced: Path,
    *,
    original_dir: Optional[Path] = None,
    fresh_dir: Optional[Path] = None,
) -> MismatchDiagnosis:
    """Name the packaging cause of a byte-diff, or mark it unclassified."""
    orig_bytes = Path(original).read_bytes()
    new_bytes = Path(reproduced).read_bytes()
    if orig_bytes == new_bytes:
        return MismatchDiagnosis(
            artifact=name,
            kinds=(),
            detail="files are byte-identical",
            hint="",
            explains=True,
        )

    if _is_video(name, orig_bytes) or _is_video(name, new_bytes):
        return MismatchDiagnosis(
            artifact=name,
            kinds=(KIND_VIDEO_ENCODER,),
            detail="video encoder output is not bit-stable",
            hint=_HINTS[KIND_VIDEO_ENCODER],
            explains=True,
        )

    left = _as_text(orig_bytes)
    right = _as_text(new_bytes)
    if left is None or right is None:
        return MismatchDiagnosis(
            artifact=name,
            kinds=(KIND_UNCLASSIFIED,),
            detail="binary artifact differs",
            hint="",
            explains=False,
        )

    kinds: List[str] = []
    detail_parts: List[str] = []
    cur_l, cur_r = left, right

    # Apply path, then timestamp, then float-print. A later step can finish
    # what an earlier one only reduced (path + timestamp in the same report).
    if original_dir is not None and fresh_dir is not None:
        swapped = _substitute_run_dirs(cur_l, Path(original_dir), Path(fresh_dir))
        if swapped != cur_l:
            kinds.append(KIND_EMBEDDED_PATH)
            detail_parts.append(
                f"original embeds {Path(original_dir)}; "
                f"re-run embeds {Path(fresh_dir)}"
            )
            cur_l = swapped

    if cur_l != cur_r:
        differing = _diff_paths(cur_l, cur_r)
        masked_l, masked_r = _apply(cur_l, cur_r, _mask_abs_paths)
        if differing:
            if KIND_EMBEDDED_PATH not in kinds:
                kinds.append(KIND_EMBEDDED_PATH)
                detail_parts.append(f"embedded paths differ: {', '.join(differing[:4])}")
            cur_l, cur_r = masked_l, masked_r

    if cur_l != cur_r:
        ts_l, ts_r = _apply(cur_l, cur_r, _mask_timestamps)
        if ts_l == ts_r:
            kinds.append(KIND_TIMESTAMP)
            detail_parts.append("clock / ISO timestamp text differs")
            cur_l, cur_r = ts_l, ts_r

    if cur_l != cur_r:
        fl_l, fl_r = _apply(cur_l, cur_r, _canon_floats)
        if fl_l == fl_r:
            kinds.append(KIND_FLOAT_FORMAT)
            detail_parts.append("float tokens parse as the same numbers")
            cur_l, cur_r = fl_l, fl_r

    explains = cur_l == cur_r and bool(kinds)
    if not kinds:
        kinds.append(KIND_UNCLASSIFIED)
        detail_parts.append("diff is not path, timestamp, float print, or video encoder")
    elif not explains:
        kinds.append(KIND_UNCLASSIFIED)
        detail_parts.append("remaining byte-diff after packaging causes")

    hint = "; ".join(_HINTS[k] for k in kinds if k in _HINTS)
    return MismatchDiagnosis(
        artifact=name,
        kinds=tuple(kinds),
        detail="; ".join(detail_parts),
        hint=hint,
        explains=explains,
    )


def diagnose_mismatches(
    names: Sequence[str],
    original_dir: Path,
    fresh_dir: Path,
) -> List[MismatchDiagnosis]:
    """Diagnose every mismatched artifact that exists on both sides."""
    out: List[MismatchDiagnosis] = []
    original_dir = Path(original_dir)
    fresh_dir = Path(fresh_dir)
    for name in names:
        orig = original_dir / name
        fresh = fresh_dir / name
        if not orig.is_file() or not fresh.is_file():
            continue
        out.append(
            diagnose_mismatch(
                name,
                orig,
                fresh,
                original_dir=original_dir,
                fresh_dir=fresh_dir,
            )
        )
    return out


def format_diagnostics(diagnostics: Sequence[MismatchDiagnosis]) -> List[str]:
    lines: List[str] = []
    for item in diagnostics:
        lines.extend(item.summary_lines())
    return lines


def diagnostics_from_receipt(receipt: dict) -> List[MismatchDiagnosis]:
    raw = receipt.get("diagnostics") or []
    return [MismatchDiagnosis.from_dict(item) for item in raw if isinstance(item, dict)]
