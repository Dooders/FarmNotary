#!/usr/bin/env python3
"""Tiny consensus-style experiment for the FarmNotary live demo.

stdlib only. Writes an official record plus a private ballot file so the
notary's allowlist / denylist is visible. Re-running with the same seed
into a different ``--out`` is bitwise identical unless ``--embed-path``
bakes the output directory into ``REPORT.md``.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter
from pathlib import Path

CANDIDATES = ("north", "east", "south", "west")
N_VOTERS = 12

# 1×1 PNG so the consensus profile's ``*.png`` glob has something official.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def run_config(seed: int) -> dict:
    return {
        "seed": seed,
        "n_voters": N_VOTERS,
        "candidates": list(CANDIDATES),
        "notary": {
            "profile": "consensus",
            "derived_from": [
                {
                    "outputs": ["summary.csv", "allocation_means.csv"],
                    "sources": ["run_config.json"],
                    "command": f"{sys.executable} {Path(__file__).resolve()} verify --out {{run_dir}}",
                    "mode": "verify",
                }
            ],
        },
    }


def _write_csv(path: Path, header: list[str], rows: list[tuple]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def allocate(seed: int) -> list[tuple[int, str]]:
    rng = random.Random(seed)
    return [(voter, rng.choice(CANDIDATES)) for voter in range(N_VOTERS)]


def aggregates(ballots: list[tuple[int, str]]) -> tuple[list[tuple], list[tuple], list[tuple], str]:
    counts = Counter(choice for _, choice in ballots)
    total = len(ballots)
    summary = [(name, counts[name], f"{counts[name] / total:.6f}") for name in CANDIDATES]
    means = [(name, f"{counts[name] / total:.6f}") for name in CANDIDATES]
    winner = max(CANDIDATES, key=lambda name: (counts[name], -CANDIDATES.index(name)))
    contrasts = [
        (name, f"{(counts[name] - counts[winner]) / total:.6f}") for name in CANDIDATES
    ]
    return summary, means, contrasts, winner


def write_report(
    path: Path,
    *,
    seed: int,
    winner: str,
    out: Path,
    embed_path: bool,
) -> None:
    lines = [
        "# Demo consensus run",
        "",
        f"seed: {seed}",
        f"voters: {N_VOTERS}",
        f"winner: {winner}",
        "command: python experiment.py --seed {seed} --out {run_dir}".format(
            seed=seed, run_dir="{run_dir}"
        ),
        "",
    ]
    if embed_path:
        lines.append(f"output: {out.resolve()}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run(out: Path, seed: int, *, embed_path: bool = False) -> None:
    out.mkdir(parents=True, exist_ok=True)
    ballots = allocate(seed)
    summary, means, contrasts, winner = aggregates(ballots)

    _write_csv(out / "trials.csv", ["trial", "seed", "winner"], [(0, seed, winner)])
    _write_csv(out / "summary.csv", ["paradigm", "votes", "share"], summary)
    _write_csv(out / "allocation_means.csv", ["paradigm", "mean"], means)
    _write_csv(out / "contrasts.csv", ["paradigm", "delta_vs_winner"], contrasts)
    (out / "figure.png").write_bytes(_PNG)
    (out / "run_config.json").write_text(
        json.dumps(run_config(seed), indent=2) + "\n", encoding="utf-8"
    )
    write_report(out / "REPORT.md", seed=seed, winner=winner, out=out, embed_path=embed_path)

    private = out / "private"
    private.mkdir(exist_ok=True)
    _write_csv(private / "ballots.csv", ["voter", "choice"], ballots)
    (out / "scratch_notes.txt").write_text(
        "lab scratch — not part of the official record\n", encoding="utf-8"
    )


def verify(out: Path) -> None:
    """Recompute summary + allocation_means from trials.csv; exit 1 on mismatch."""
    seed = json.loads((out / "run_config.json").read_text(encoding="utf-8"))["seed"]
    ballots = allocate(int(seed))
    summary, means, _contrasts, recomputed_winner = aggregates(ballots)
    with (out / "trials.csv").open(encoding="utf-8", newline="") as fh:
        recorded = list(csv.DictReader(fh))
    if not recorded or recorded[0].get("winner") != recomputed_winner:
        raise SystemExit("verify: trials.csv winner does not recompute from the seed")

    def _read(path: Path) -> str:
        return path.read_text(encoding="utf-8")

    expected_summary = Path(out / "_expected_summary.csv")
    expected_means = Path(out / "_expected_means.csv")
    _write_csv(expected_summary, ["paradigm", "votes", "share"], summary)
    _write_csv(expected_means, ["paradigm", "mean"], means)
    try:
        if _read(expected_summary) != _read(out / "summary.csv"):
            raise SystemExit("verify: summary.csv does not recompute from the seed")
        if _read(expected_means) != _read(out / "allocation_means.csv"):
            raise SystemExit("verify: allocation_means.csv does not recompute from the seed")
    finally:
        expected_summary.unlink(missing_ok=True)
        expected_means.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--embed-path",
        action="store_true",
        help="Bake the output directory into REPORT.md (packaging bug, not science)",
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default="run",
        choices=("run", "verify"),
    )
    args = parser.parse_args(argv)
    if args.mode == "verify":
        verify(args.out)
    else:
        run(args.out, args.seed, embed_path=args.embed_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
