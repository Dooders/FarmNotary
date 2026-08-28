from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from farm_notary.anchor import anchor_run, get_backend
from farm_notary.manifest import Manifest, build_manifest, write_manifest
from farm_notary.verify import verify_run_dir


def load_manifest(run_dir: Path) -> Manifest:
    data = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    return Manifest(**{k: data.get(k) for k in Manifest.__dataclass_fields__})


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

    p_anc = sub.add_parser("anchor", help="Anchor manifest hash on-chain (dry-run by default)")
    p_anc.add_argument("--run-dir", required=True)
    p_anc.add_argument("--backend", choices=["dry-run", "eas"], default="dry-run")
    p_anc.add_argument("--cid", help="CID of the pinned run directory, stored alongside the hash")
    p_anc.add_argument(
        "--no-write",
        action="store_true",
        help="Do not write the chain receipt back into manifest.json",
    )

    sub.add_parser(
        "register-schema",
        help="Register the FarmNotary schema with the EAS SchemaRegistry (one-time per chain)",
    )

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
            manifest = Manifest(**{k: data.get(k) for k in Manifest.__dataclass_fields__})
        else:
            run_dir = Path(args.run_dir)
            manifest = load_manifest(run_dir)
        problems = verify_run_dir(manifest, run_dir)
        if problems:
            for p in problems:
                print("FAIL", p)
            return 1
        print("OK", manifest.content_hash())
        return 0

    if args.cmd == "anchor":
        run_dir = Path(args.run_dir)
        manifest = load_manifest(run_dir)
        receipt = anchor_run(manifest, cid=args.cid, backend=get_backend(args.backend))
        out = asdict(receipt)
        if receipt.attestation_uid and args.backend == "eas":
            from farm_notary.eas import EASConfig, attestation_url

            out["easscan_url"] = attestation_url(EASConfig.from_env(), receipt.attestation_uid)
        print(json.dumps(out, indent=2))
        if not receipt.dry_run and not args.no_write:
            write_manifest(manifest, run_dir)
        return 0

    if args.cmd == "register-schema":
        from farm_notary.eas import FARM_NOTARY_SCHEMA, register_schema

        uid = register_schema()
        print(json.dumps({"schema": FARM_NOTARY_SCHEMA, "schema_uid": uid}, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
