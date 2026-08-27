from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

from farm_notary.anchor import anchor_run, get_backend
from farm_notary.manifest import (
    MANIFEST_NAME,
    build_manifest,
    detect_git_sha,
    load_manifest,
    write_manifest,
)
from farm_notary.verify import verify_chain, verify_run_dir


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
        description="Notarize AgentFarm runs: manifest, optional IPFS pin, on-chain anchor.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_man = sub.add_parser("manifest", help=f"Write {MANIFEST_NAME} for a run directory")
    p_man.add_argument("--run-dir", required=True)
    p_man.add_argument("--git-sha", help="Code identity; auto-detected from cwd if omitted")
    p_man.add_argument("--runner", help="Name of the runner that produced the artifacts")
    p_man.add_argument("--config", help="Path to a JSON file with the run configuration")
    p_man.add_argument(
        "--official-record",
        help="Path to a JSON file with aggregate results (never per-agent choices)",
    )

    p_anc = sub.add_parser("anchor", help="Pin (optional) and anchor an existing manifest")
    p_anc.add_argument("--run-dir", required=True)
    p_anc.add_argument(
        "--backend",
        choices=("dry-run", "registry"),
        default="dry-run",
        help="dry-run prints the payload; registry submits a transaction (needs farm-notary[chain])",
    )
    p_anc.add_argument("--pin", action="store_true", help="Upload the run directory to IPFS first")
    p_anc.add_argument("--ipfs-api", help="Kubo API URL (default: FARM_NOTARY_IPFS_API or http://127.0.0.1:5001)")
    p_anc.add_argument("--rpc-url", help="Ethereum JSON-RPC URL (default: FARM_NOTARY_RPC_URL)")
    p_anc.add_argument("--contract", help="SimulationRegistry address (default: FARM_NOTARY_CONTRACT)")

    p_ver = sub.add_parser("verify", help="Rehash artifacts; optionally match the chain record")
    p_ver.add_argument("--run-dir", help="Run directory containing manifest.json")
    p_ver.add_argument("--manifest", help=f"Path to a {MANIFEST_NAME} (artifacts checked next to it)")
    p_ver.add_argument("--chain", action="store_true", help="Also check the on-chain record")
    p_ver.add_argument("--rpc-url", help="Ethereum JSON-RPC URL (default: FARM_NOTARY_RPC_URL)")
    p_ver.add_argument("--contract", help="SimulationRegistry address (default: FARM_NOTARY_CONTRACT)")

    return parser


def _cmd_manifest(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        print(f"error: {run_dir} is not a directory", file=sys.stderr)
        return 2
    git_sha = args.git_sha or detect_git_sha()
    manifest = build_manifest(
        run_dir,
        git_sha=git_sha,
        runner=args.runner,
        config=_load_json_arg(args.config),
        official_record=_load_json_arg(args.official_record),
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
    manifest = load_manifest(manifest_path)

    cid = None
    if args.pin:
        from farm_notary.ipfs import IpfsClient

        client = IpfsClient(api_url=args.ipfs_api)
        cid = client.add_run_dir(run_dir, list(manifest.artifacts) + [MANIFEST_NAME])

    backend = get_backend(args.backend, rpc_url=args.rpc_url, contract=args.contract)
    receipt = anchor_run(manifest, cid=cid, backend=backend)
    write_manifest(manifest, run_dir)
    print(json.dumps(receipt.to_dict(), indent=2))
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
    manifest = load_manifest(manifest_path)

    problems = verify_run_dir(manifest, run_dir)
    if args.chain:
        rpc_url = args.rpc_url or os.environ.get("FARM_NOTARY_RPC_URL")
        contract = args.contract or os.environ.get("FARM_NOTARY_CONTRACT")
        if not rpc_url or not contract:
            print(
                "error: --chain needs --rpc-url and --contract (or FARM_NOTARY_RPC_URL / FARM_NOTARY_CONTRACT)",
                file=sys.stderr,
            )
            return 2
        problems += verify_chain(manifest, rpc_url=rpc_url, contract=contract)

    if problems:
        for problem in problems:
            print("FAIL", problem)
        return 1
    print("OK", manifest.content_hash())
    return 0


def main(argv: Optional[list] = None) -> int:
    args = _build_parser().parse_args(argv)
    handlers = {
        "manifest": _cmd_manifest,
        "anchor": _cmd_anchor,
        "verify": _cmd_verify,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
