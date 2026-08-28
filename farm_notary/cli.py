from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from farm_notary.anchor import anchor_run, get_backend, write_proof
from farm_notary.manifest import (
    MANIFEST_NAME,
    build_manifest,
    load_manifest,
    write_manifest,
)
from farm_notary.verify import verify_anchor, verify_receipt, verify_run_dir


def _load_json_arg(path: Optional[str]) -> Optional[dict]:
    if not path:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"error: {path} must contain a JSON object")
    return data


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="farm-notary",
        description="Notarize AgentFarm runs: manifest, optional IPFS pin, public anchor.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_man = sub.add_parser("manifest", help=f"Write {MANIFEST_NAME} for a run directory")
    p_man.add_argument("--run-dir", required=True)
    p_man.add_argument("--git-sha", help="Code identity; auto-detected from cwd if omitted")
    p_man.add_argument("--runner", help="Name of the runner that produced the artifacts")
    p_man.add_argument("--config", help="Path to a JSON file with the run configuration")
    p_man.add_argument(
        "--command",
        help='Exact command that produced the run, with "{run_dir}" marking the '
        "output directory (enables `farm-notary reproduce`)",
    )
    p_man.add_argument(
        "--lockfile",
        help="Dependency lockfile to hash into the environment record",
    )
    p_man.add_argument(
        "--official-record",
        help="Path to a JSON file with aggregate results (never per-agent choices)",
    )

    p_anc = sub.add_parser("anchor", help="Pin (optional) and anchor an existing manifest")
    p_anc.add_argument("--run-dir", required=True)
    p_anc.add_argument(
        "--backend",
        choices=("dry-run", "ots", "eas"),
        default="dry-run",
        help="dry-run prints the payload; ots anchors via OpenTimestamps; eas anchors on Base (needs farm-notary[chain])",
    )
    p_anc.add_argument("--pin", action="store_true", help="Upload the run directory to IPFS first")
    p_anc.add_argument("--ipfs-api", help="Kubo API URL (default: FARM_NOTARY_IPFS_API or http://127.0.0.1:5001)")
    p_anc.add_argument(
        "--calendar",
        action="append",
        help="OpenTimestamps calendar URL; repeatable (default: FARM_NOTARY_CALENDARS or the public pools)",
    )
    p_anc.add_argument("--cid", help="CID of the pinned run directory, stored alongside the hash")
    p_anc.add_argument(
        "--no-write",
        action="store_true",
        help="Do not write the anchor receipt back into manifest.json",
    )

    p_ver = sub.add_parser("verify", help="Rehash artifacts and check the anchor proof")
    p_ver.add_argument("--run-dir", help="Run directory containing manifest.json")
    p_ver.add_argument("--manifest", help=f"Path to a {MANIFEST_NAME} (artifacts checked next to it)")

    p_upg = sub.add_parser(
        "upgrade", help="Complete a pending OpenTimestamps proof with Bitcoin attestations"
    )
    p_upg.add_argument("--run-dir", required=True)

    p_rep = sub.add_parser(
        "reproduce",
        help="Re-run the manifest's recorded command and byte-compare the artifacts",
    )
    p_rep.add_argument("--run-dir", required=True)
    p_rep.add_argument(
        "--ignore",
        action="append",
        default=[],
        help="Glob for artifacts excluded from the comparison (e.g. '*.mp4'); repeatable",
    )
    p_rep.add_argument(
        "--fresh-dir",
        help="Directory for the re-run (default: a new temporary directory)",
    )
    p_rep.add_argument(
        "--anchor",
        action="store_true",
        help="Anchor the reproduction receipt via OpenTimestamps",
    )
    p_rep.add_argument("--calendar", action="append", help="Calendar URL; repeatable")

    sub.add_parser(
        "register-schema",
        help="Register the FarmNotary schema with the EAS SchemaRegistry (one-time per chain)",
    )

    return parser


def _cmd_manifest(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        print(f"error: {run_dir} is not a directory", file=sys.stderr)
        return 2
    manifest = build_manifest(
        run_dir,
        git_sha=args.git_sha,
        runner=args.runner,
        command=args.command,
        lockfile=Path(args.lockfile) if args.lockfile else None,
        config=_load_json_arg(args.config),
        official_record=_load_json_arg(args.official_record),
    )
    if manifest.git_dirty:
        print(
            "warning: git tree is dirty; the recorded sha does not identify the code that ran",
            file=sys.stderr,
        )
    path = write_manifest(manifest, run_dir)
    print(path)
    print("artifacts", len(manifest.artifacts))
    print("content_hash", manifest.content_hash())
    return 0


def _cmd_anchor(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    manifest_path = run_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        print(
            f"error: {manifest_path} not found; run `farm-notary manifest --run-dir {run_dir}` first",
            file=sys.stderr,
        )
        return 2
    try:
        manifest = load_manifest(manifest_path)
    except (ValueError, OSError) as exc:
        print(f"error: could not load manifest: {exc}", file=sys.stderr)
        return 2

    cid = getattr(args, "cid", None)
    if getattr(args, "pin", False):
        from farm_notary.ipfs import IpfsClient

        client = IpfsClient(api_url=args.ipfs_api)
        cid = client.add_run_dir(run_dir, list(manifest.artifacts) + [MANIFEST_NAME])

    backend = get_backend(args.backend, calendars=getattr(args, "calendar", None))
    receipt = anchor_run(manifest, cid=cid, backend=backend)
    proof_path = write_proof(receipt, run_dir)

    no_write = getattr(args, "no_write", False)
    if not no_write:
        write_manifest(manifest, run_dir)

    out = receipt.to_dict()
    if receipt.attestation_uid and args.backend == "eas":
        from farm_notary.eas import EASConfig, attestation_url

        out["easscan_url"] = attestation_url(EASConfig.from_env(), receipt.attestation_uid)
    print(json.dumps(out, indent=2))
    if proof_path:
        print(f"proof written to {proof_path}")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    if args.manifest:
        manifest_path = Path(args.manifest)
        run_dir = manifest_path.parent
    elif args.run_dir:
        run_dir = Path(args.run_dir)
        manifest_path = run_dir / MANIFEST_NAME
    else:
        print("error: pass --run-dir or --manifest", file=sys.stderr)
        return 2
    try:
        manifest = load_manifest(manifest_path)
    except (ValueError, OSError) as exc:
        print(f"error: could not load manifest: {exc}", file=sys.stderr)
        return 2

    problems = verify_run_dir(manifest, run_dir)
    problems += verify_anchor(manifest, run_dir)
    problems += verify_receipt(manifest, run_dir)

    if problems:
        for problem in problems:
            print("FAIL", problem)
        return 1

    print("OK", manifest.content_hash())
    if manifest.anchor and manifest.anchor.get("backend") == "opentimestamps":
        from farm_notary.ots import PROOF_NAME, proof_status

        proof_path = run_dir / manifest.anchor.get("detail", {}).get("proof", PROOF_NAME)
        for line in proof_status(proof_path.read_bytes()).summary():
            print(line)
    from farm_notary.manifest import RECEIPT_NAME

    receipt_path = run_dir / RECEIPT_NAME
    if receipt_path.is_file():
        from farm_notary.reproduce import load_receipt

        receipt = load_receipt(run_dir)
        print(
            f"reproduction receipt: {len(receipt.get('matched', []))} artifact(s) "
            f"bitwise-reproduced on {receipt.get('created_utc')}"
        )
    return 0


def _cmd_reproduce(args: argparse.Namespace) -> int:
    from farm_notary.reproduce import (
        ReproduceError,
        build_receipt,
        receipt_hash,
        reproduce_run,
        write_receipt,
    )

    run_dir = Path(args.run_dir)
    try:
        manifest = load_manifest(run_dir)
    except (ValueError, OSError) as exc:
        print(f"error: could not load manifest: {exc}", file=sys.stderr)
        return 2
    try:
        result = reproduce_run(
            manifest,
            fresh_dir=Path(args.fresh_dir) if args.fresh_dir else None,
            ignore=args.ignore,
        )
    except ReproduceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for line in result.summary():
        print(line)

    receipt = build_receipt(manifest, result)
    receipt_path = write_receipt(receipt, run_dir)
    print(f"receipt written to {receipt_path}")
    print("receipt_hash", receipt_hash(receipt))

    if args.anchor:
        from farm_notary.ots import stamp_digest
        from farm_notary.reproduce import RECEIPT_PROOF_NAME

        proof, accepted = stamp_digest(
            bytes.fromhex(receipt_hash(receipt)), calendars=args.calendar
        )
        proof_path = run_dir / RECEIPT_PROOF_NAME
        proof_path.write_bytes(proof)
        print(f"receipt anchored via {len(accepted)} calendar(s); proof at {proof_path}")

    return 0 if result.ok else 1


def _cmd_upgrade(args: argparse.Namespace) -> int:
    from farm_notary.ots import PROOF_NAME, upgrade_proof

    run_dir = Path(args.run_dir)
    proof_path = run_dir / PROOF_NAME
    if not proof_path.is_file():
        print(f"error: {proof_path} not found; anchor with --backend ots first", file=sys.stderr)
        return 2
    upgraded, status, errors = upgrade_proof(proof_path.read_bytes())
    proof_path.write_bytes(upgraded)
    for line in status.summary():
        print(line)
    for error in errors:
        print("note:", error, file=sys.stderr)
    return 0 if status.confirmed else 1


def _cmd_register_schema(_args: argparse.Namespace) -> int:
    from farm_notary.eas import FARM_NOTARY_SCHEMA, register_schema

    uid = register_schema()
    print(json.dumps({"schema": FARM_NOTARY_SCHEMA, "schema_uid": uid}, indent=2))
    return 0


def main(argv: Optional[list] = None) -> int:
    args = _build_parser().parse_args(argv)
    handlers = {
        "manifest": _cmd_manifest,
        "anchor": _cmd_anchor,
        "verify": _cmd_verify,
        "upgrade": _cmd_upgrade,
        "reproduce": _cmd_reproduce,
        "register-schema": _cmd_register_schema,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
