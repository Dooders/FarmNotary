from __future__ import annotations

import argparse
import json
from pathlib import Path

from farm_notary.anchor import anchor_run
from farm_notary.manifest import Manifest, build_manifest, write_manifest
from farm_notary.verify import verify_run_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="farm-notary")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_man = sub.add_parser("manifest", help="Write manifest.json for a run directory")
    p_man.add_argument("--run-dir", required=True)
    p_man.add_argument("--git-sha")
    p_man.add_argument("--runner")

    p_ver = sub.add_parser("verify", help="Rehash artifacts and check manifest")
    p_ver.add_argument("--run-dir")
    p_ver.add_argument("--manifest")

    p_anc = sub.add_parser("anchor", help="Dry-run on-chain payload (default)")
    p_anc.add_argument("--run-dir", required=True)

    args = parser.parse_args(argv)

    if args.cmd == "manifest":
        run_dir = Path(args.run_dir)
        manifest = build_manifest(run_dir, git_sha=args.git_sha, runner=args.runner)
        path = write_manifest(manifest, run_dir)
        print(path)
        print("content_hash", manifest.content_hash())
        return 0

    if args.cmd == "verify":
        if args.manifest:
            data = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
            run_dir = Path(args.manifest).parent
        else:
            run_dir = Path(args.run_dir)
            data = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest = Manifest(**{k: data.get(k) for k in Manifest.__dataclass_fields__})
        problems = verify_run_dir(manifest, run_dir)
        if problems:
            for p in problems:
                print("FAIL", p)
            return 1
        print("OK", manifest.content_hash())
        return 0

    if args.cmd == "anchor":
        run_dir = Path(args.run_dir)
        data = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest = Manifest(**{k: data.get(k) for k in Manifest.__dataclass_fields__})
        receipt = anchor_run(manifest)
        print(json.dumps({
            "backend": receipt.backend,
            "manifest_hash": receipt.manifest_hash,
            "cid": receipt.cid,
            "tx_hash": receipt.tx_hash,
            "dry_run": receipt.dry_run,
        }, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
