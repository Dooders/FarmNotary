from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from farm_notary.anchor import anchor_run, get_backend, write_proof
from farm_notary.manifest import (
    MANIFEST_NAME,
    DirtyTreeError,
    build_manifest,
    detect_git_status,
    load_manifest,
    require_clean_identity,
    write_manifest,
)
from farm_notary.verify import evaluate_claims


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
        "--profile",
        choices=("consensus", "rl-sweep", "evolution-run"),
        help=(
            "Named publish profile of official artifacts. Prefer this over "
            "inventing globs. The denylist still applies. Combine with "
            "--publish to add extra files."
        ),
    )
    p_man.add_argument(
        "--publish",
        action="append",
        dest="publish",
        metavar="GLOB",
        help=(
            "Glob pattern for files to include in the manifest; repeatable. "
            "Nothing is hashed or uploaded unless declared here, via "
            "--profile, or via 'notary.profile' / 'notary.publish' in the "
            "run config."
        ),
    )
    p_man.add_argument(
        "--official-record",
        help="Path to a JSON file with aggregate results (never per-agent choices)",
    )
    p_man.add_argument(
        "--precommit",
        help="Path to a precommit.json produced by `farm-notary precommit`; "
        "binds the manifest to the pre-run specification",
    )

    p_pre = sub.add_parser(
        "precommit",
        help="Anchor config + command + code hash before the run",
    )
    p_pre.add_argument("--config", help="Path to the run configuration JSON file")
    p_pre.add_argument("--command", help="Exact command (with {run_dir} placeholder) for the run")
    p_pre.add_argument("--git-sha", help="Code identity; auto-detected from cwd if omitted")
    p_pre.add_argument(
        "--lockfile",
        help="Dependency lockfile to hash into the precommit",
    )
    p_pre.add_argument(
        "--out",
        default=".",
        help="Directory (or file path) where precommit.json is written (default: current directory)",
    )
    p_pre.add_argument(
        "--backend",
        choices=("dry-run", "ots"),
        default="dry-run",
        help="dry-run prints the payload; ots anchors via OpenTimestamps",
    )
    p_pre.add_argument(
        "--calendar",
        action="append",
        help="OpenTimestamps calendar URL; repeatable",
    )
    p_pre.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow a dirty git tree (the recorded sha will not identify the code)",
    )

    p_anc = sub.add_parser("anchor", help="Pin (optional) and anchor an existing manifest")
    p_anc.add_argument("--run-dir", required=True)
    p_anc.add_argument(
        "--backend",
        choices=("dry-run", "ots", "eas"),
        default="dry-run",
        help="dry-run prints the payload; ots anchors via OpenTimestamps (recommended); eas anchors on Base (experimental — requires a funded key and costs gas; needs farm-notary[chain])",
    )
    p_anc.add_argument(
        "--pin",
        action="store_true",
        help=(
            "Upload the run directory to local Kubo (lab convenience; not "
            "archival). For a paper or academy citation use --pin-remote."
        ),
    )
    p_anc.add_argument("--ipfs-api", help="Kubo API URL (default: FARM_NOTARY_IPFS_API or http://127.0.0.1:5001)")
    p_anc.add_argument(
        "--pin-remote",
        metavar="SERVICE",
        help=(
            "Durable pin via a registered pinning service (Pinata, "
            "web3.storage, or any IPFS Pinning Service API). This is the "
            "published path for anything you cite. Implies --pin. Register "
            "the service with `ipfs pin remote service add`."
        ),
    )
    p_anc.add_argument(
        "--calendar",
        action="append",
        help="OpenTimestamps calendar URL; repeatable (default: FARM_NOTARY_CALENDARS or the public pools)",
    )
    p_anc.add_argument(
        "--no-check-gateway",
        action="store_true",
        help="Skip the public-gateway reachability check after pinning",
    )
    p_anc.add_argument("--cid", help="CID of the pinned run directory, stored alongside the hash")
    p_anc.add_argument(
        "--no-write",
        action="store_true",
        help="Do not write the anchor receipt back into manifest.json",
    )
    p_anc.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow anchoring a manifest whose git tree was dirty (the sha does not identify the code)",
    )

    p_ver = sub.add_parser(
        "verify",
        help="Print a CLAIMS.md claim card for a run (rehash, timestamp, precommit, receipt)",
    )
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
        "--cwd",
        help="Working directory for the recorded command (the experiment repo)",
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
    config = _load_json_arg(args.config)
    import warnings

    _, detected_dirty = detect_git_status()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            manifest = build_manifest(
                run_dir,
                publish_patterns=args.publish or [],
                publish_profile=args.profile,
                git_sha=args.git_sha,
                git_dirty=detected_dirty,
                runner=args.runner,
                command=args.command,
                lockfile=Path(args.lockfile) if args.lockfile else None,
                config=config,
                official_record=_load_json_arg(args.official_record),
                precommit_path=Path(args.precommit) if args.precommit else None,
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    for w in caught:
        print(f"warning: {w.message}", file=sys.stderr)
    if manifest.git_dirty:
        print(
            "warning: git tree is dirty; the recorded sha does not identify the code that ran",
            file=sys.stderr,
        )
    path = write_manifest(manifest, run_dir)
    print(path)
    if manifest.publish_profile:
        print("profile", manifest.publish_profile)
    print("artifacts", len(manifest.artifacts))
    print("unmatched", manifest.unmatched_count)
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
    try:
        require_clean_identity(manifest.git_dirty, allow_dirty=args.allow_dirty)
    except DirtyTreeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    cid = getattr(args, "cid", None)
    pin_remote_service = getattr(args, "pin_remote", None)
    do_pin = getattr(args, "pin", False) or bool(pin_remote_service)
    if do_pin:
        from datetime import datetime, timezone

        from farm_notary.ipfs import IpfsClient, check_gateway_reachability

        client = IpfsClient(api_url=args.ipfs_api)
        cid = client.add_run_dir(run_dir, list(manifest.artifacts) + [MANIFEST_NAME])
        manifest.cid = cid
        manifest.cid_reachable = None
        manifest.cid_reachable_checked_utc = None
        manifest.pin_service = pin_remote_service or "local"

        if pin_remote_service:
            try:
                client.pin_remote(cid, pin_remote_service)
            except Exception as exc:
                print(f"error: remote pin to '{pin_remote_service}' failed: {exc}", file=sys.stderr)
                return 1
        else:
            print(
                "warning: pinned to local Kubo only — not archival. "
                "For a paper or academy citation use --pin-remote "
                "(Pinata / web3.storage / pinning-service API).",
                file=sys.stderr,
            )

        # Check that the CID is resolvable through a public gateway.
        if not getattr(args, "no_check_gateway", False):
            print(f"checking gateway reachability for {cid} …", file=sys.stderr)
            reachable = check_gateway_reachability(cid)
            manifest.cid_reachable = reachable
            manifest.cid_reachable_checked_utc = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            if not reachable:
                print(
                    f"warning: CID {cid} is not yet reachable via the public gateway. "
                    "A local pin is not archival — the content will become "
                    "unreachable when the daemon is offline.",
                    file=sys.stderr,
                )

    backend = get_backend(args.backend, calendars=getattr(args, "calendar", None))
    receipt = anchor_run(
        manifest, cid=cid, backend=backend, allow_dirty=args.allow_dirty
    )
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
    import warnings

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
        manifest = load_manifest(manifest_path, validate=False)
    except (ValueError, OSError) as exc:
        print(f"error: could not load manifest: {exc}", file=sys.stderr)
        return 2

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        card = evaluate_claims(manifest, run_dir)
    for w in caught:
        print(f"warning: {w.message}", file=sys.stderr)

    print(card.render(), end="")
    if card.notes:
        print()
        for note in card.notes:
            print(note)
    if card.problems:
        print()
        for problem in card.problems:
            print("FAIL", problem)
        return 1
    return 0


def _cmd_precommit(args: argparse.Namespace) -> int:
    from farm_notary.precommit import (
        PRECOMMIT_NAME,
        PRECOMMIT_PROOF_NAME,
        build_precommit,
        precommit_hash,
        write_precommit,
    )

    out = Path(args.out)
    # Always treat --out as a directory: create it if it does not exist yet so
    # that `--out ./run_dir` works before the run directory is created, rather
    # than writing a bare file named "run_dir".
    out.mkdir(parents=True, exist_ok=True)
    dest = out / PRECOMMIT_NAME

    _, detected_dirty = detect_git_status()
    try:
        pc = build_precommit(
            config=_load_json_arg(args.config),
            command=args.command,
            git_sha=args.git_sha,
            git_dirty=detected_dirty,
            lockfile=Path(args.lockfile) if args.lockfile else None,
            allow_dirty=args.allow_dirty,
        )
    except DirtyTreeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if pc.get("git_dirty"):
        print(
            "warning: git tree is dirty; the recorded sha does not identify the code that will run",
            file=sys.stderr,
        )
    write_precommit(pc, dest)
    print(f"precommit written to {dest}")
    pc_hash = precommit_hash(pc)
    print("precommit_hash", pc_hash)

    if args.backend == "ots":
        from farm_notary.ots import stamp_digest

        proof_dest = dest.parent / PRECOMMIT_PROOF_NAME
        proof, accepted = stamp_digest(
            bytes.fromhex(pc_hash), calendars=args.calendar
        )
        proof_dest.write_bytes(proof)
        print(f"precommit anchored via {len(accepted)} calendar(s); proof at {proof_dest}")
    else:
        print("backend dry-run: precommit not anchored (use --backend ots to anchor)")
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
            original_dir=run_dir,
            cwd=Path(args.cwd) if args.cwd else None,
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

    from farm_notary.scope import format_bitwise_status

    compared = len(result.matched) + len(result.mismatched) + len(result.missing)
    score = f"{len(result.matched)}/{compared}"
    if result.ignore:
        score = f"{score}, ignored: {', '.join(result.ignore)}"
    elif result.ignored:
        score = f"{score}, ignored: {', '.join(result.ignored)}"
    print(
        "bitwise reproducible (scoped) —",
        format_bitwise_status(score, receipt.get("environment") or {}, ok=result.ok),
    )

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
        "precommit": _cmd_precommit,
        "register-schema": _cmd_register_schema,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
