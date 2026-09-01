from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union

from farm_notary.anchor import (
    anchor_run,
    get_backend,
    write_cid_binding_proof,
    write_proof,
)
from farm_notary.campaign import Campaign
from farm_notary.manifest import (
    MANIFEST_NAME,
    DirtyTreeError,
    Manifest,
    build_manifest,
    detect_git_status,
    load_manifest,
    require_clean_identity,
    resolve_run_path,
    write_manifest,
)
from farm_notary.sigstore import (
    SigstoreError,
    read_identity_token_cli,
    sign_receipt,
)
from farm_notary.verify import (
    evaluate_claims,
    verify_anchor,
    verify_derived_artifacts,
    verify_identity_record,
)


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
    p_man.add_argument(
        "--seed-index",
        type=int,
        help="Committed seed index to bind from the precommit seed_plan",
    )
    p_man.add_argument(
        "--seeds",
        help="Path to seeds.json from `farm-notary derive-seeds` "
        "(default: next to the precommit)",
    )
    _add_beacon_args(p_man)

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
    p_pre.add_argument(
        "--seed-count",
        type=int,
        help="Commit this many beacon-derived seeds (writes seed_plan)",
    )
    p_pre.add_argument(
        "--inclusion",
        help="Declared rule for which committed seeds are published "
        "(required with --seed-count)",
    )
    p_pre.add_argument(
        "--delay-rounds",
        type=int,
        default=1,
        help="min_round = latest + this (default: 1). Derive uses exactly min_round.",
    )
    _add_beacon_args(p_pre)

    p_anc = sub.add_parser("anchor", help="Pin (optional) and anchor an existing manifest")
    p_anc.add_argument("--run-dir", required=True)
    p_anc.add_argument(
        "--backend",
        choices=("dry-run", "ots", "eas"),
        default="dry-run",
        help="dry-run prints the payload; ots anchors via OpenTimestamps (recommended); eas is deprecated (requires a funded key and costs gas; use ots instead)",
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
        help=(
            "Print a CLAIMS.md claim card for a run "
            "(exit 0 = attempted checks passed; ladder strength is separate)"
        ),
    )
    p_ver.add_argument("--run-dir", help="Run directory containing manifest.json")
    p_ver.add_argument("--manifest", help=f"Path to a {MANIFEST_NAME} (artifacts checked next to it)")
    p_ver.add_argument(
        "--verify-derived",
        action="store_true",
        help=(
            "Execute the derivation commands recorded in the manifest. "
            "Only use this flag for manifests you trust, since commands "
            "are run as-is via the shell."
        ),
    )

    p_upg = sub.add_parser(
        "upgrade", help="Complete a pending OpenTimestamps proof with Bitcoin attestations"
    )
    p_upg.add_argument("--run-dir", required=True)

    p_rep = sub.add_parser(
        "reproduce",
        help="Re-run the manifest's recorded shell command and byte-compare the artifacts",
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
        "--i-accept-untrusted-command",
        dest="accept_untrusted_command",
        action="store_true",
        help=(
            "Required when the manifest does not match the local checkout or "
            "current GitHub Actions repo/SHA. `reproduce` executes the "
            "recorded command via the shell; sandboxing (container/VM, no "
            "network) is your responsibility."
        ),
    )
    p_rep.add_argument(
        "--anchor",
        action="store_true",
        help="Anchor the reproduction receipt via OpenTimestamps",
    )
    p_rep.add_argument("--calendar", action="append", help="Calendar URL; repeatable")
    p_rep.add_argument(
        "--sign",
        action="store_true",
        help=(
            "Sign the reproduction receipt with Sigstore keyless (cosign sign-blob). "
            "Skipped when the re-run fails. Identity is recorded, not proven "
            "independent. Token via COSIGN_IDENTITY_TOKEN / SIGSTORE_ID_TOKEN "
            "or --identity-token @PATH (never a raw JWT on argv)."
        ),
    )
    p_rep.add_argument(
        "--identity-token",
        dest="identity_token",
        metavar="@PATH",
        help=(
            "OIDC identity token file as @PATH. Prefer COSIGN_IDENTITY_TOKEN "
            "or SIGSTORE_ID_TOKEN. A raw JWT is rejected."
        ),
    )

    sub.add_parser(
        "register-schema",
        help="Register the FarmNotary schema with the EAS SchemaRegistry (one-time per chain)",
    )

    p_camp = sub.add_parser(
        "campaign",
        help="Build a parent sweep manifest from child run directories",
    )
    p_camp.add_argument(
        "--run-dir",
        action="append",
        dest="run_dirs",
        required=True,
        metavar="DIR",
        help="Child run directory containing manifest.json; repeatable",
    )
    p_camp.add_argument("--name", help="Experiment / sweep name")
    p_camp.add_argument("--config", help="Shared sweep configuration JSON")
    p_camp.add_argument("--git-sha", help="Code identity; taken from the first child if omitted")
    p_camp.add_argument("--command", help="Recorded command template (with {run_dir})")
    p_camp.add_argument("--lockfile", help="Dependency lockfile to hash into the environment")
    p_camp.add_argument(
        "--out",
        required=True,
        help="Directory (or campaign.json path) where the parent manifest is written",
    )

    p_sign = sub.add_parser(
        "sign",
        help="Attach a minisign or SSH signature of the content hash (optional identity)",
    )
    p_sign.add_argument("--run-dir", help="Run directory containing manifest.json")
    p_sign.add_argument("--campaign", help="Path to campaign.json (or its directory)")
    p_sign.add_argument(
        "--scheme",
        choices=("ssh", "minisign"),
        default="ssh",
        help="ssh (default) or minisign; EAS is not used here",
    )
    p_sign.add_argument("--key", required=True, help="Path to the private key")
    p_sign.add_argument(
        "--principal",
        help="Identity label recorded with the signature (SSH allowed-signers principal)",
    )

    p_paper = sub.add_parser(
        "paper-pack",
        help="Write a PDF appendix snippet (CID, hash, attestation, scoped claim)",
    )
    p_paper.add_argument("--run-dir", help="Run directory containing manifest.json")
    p_paper.add_argument("--campaign", help="Path to campaign.json (or its directory)")
    p_paper.add_argument(
        "--out",
        help="Output markdown path (default: <dir>/appendix.md)",
    )
    p_paper.add_argument("--name", help="Experiment name override for the appendix heading")
    p_paper.add_argument(
        "--verify-derived",
        action="store_true",
        help=(
            "Execute the derivation commands recorded in the manifest to confirm "
            "statistics recompute exactly. Only use for manifests you trust."
        ),
    )

    p_idx = sub.add_parser(
        "index",
        help="Add a published run to the static public registry (no scores or rankings)",
    )
    p_idx.add_argument(
        "--registry",
        required=True,
        help="Registry markdown, JSON, or directory (writes index.md + registry.json)",
    )
    p_idx.add_argument("--run-dir", help="Run directory to add")
    p_idx.add_argument("--campaign", help="Campaign to add (one row per child run)")
    p_idx.add_argument("--name", help="Experiment name override")
    p_idx.add_argument(
        "--claim",
        dest="claim_level",
        help="Claim level override (bytes, derived, bitwise, bitwise+derived)",
    )

    p_ver.add_argument(
        "--campaign",
        help="Path to campaign.json (or its directory) instead of a single-run manifest",
    )
    p_ver.add_argument(
        "--require-local",
        action="store_true",
        help="When verifying a campaign, fail if a child run directory is not present",
    )
    _add_beacon_args(p_ver)

    p_chk = sub.add_parser(
        "check",
        help=(
            "Quick reviewer check: read claim level and anchor status from a manifest "
            "without rehashing artifacts. Zero-install path: "
            "uvx farm-notary check --manifest manifest.json"
        ),
    )
    p_chk.add_argument(
        "--manifest",
        required=True,
        help="Path to manifest.json (artifacts are not required)",
    )

    p_reveal = sub.add_parser(
        "reveal-withheld",
        help=(
            "Reveal a subset of withheld files against the salted Merkle "
            "commitment. Names are never listed unless you pass --path."
        ),
    )
    p_reveal.add_argument("--run-dir", required=True)
    p_reveal.add_argument(
        "--path",
        action="append",
        dest="paths",
        metavar="REL",
        help="Relative path to reveal; repeatable. Required.",
    )
    p_reveal.add_argument(
        "--out",
        help="Write the reveal JSON here (default: stdout summary only)",
    )
    p_reveal.add_argument(
        "--verify",
        action="store_true",
        help="Verify an existing reveal JSON against withheld_root",
    )
    p_reveal.add_argument(
        "--reveal",
        help="Path to a reveal JSON (with --verify)",
    )

    p_der = sub.add_parser(
        "derive-seeds",
        help="Derive committed seeds from the precommit seed_plan's min_round",
    )
    p_der.add_argument(
        "--precommit",
        required=True,
        help="Path to precommit.json (or its directory)",
    )
    p_der.add_argument(
        "--out",
        help="Where to write seeds.json (default: next to the precommit)",
    )
    p_der.add_argument(
        "--wait",
        action="store_true",
        help="Poll the beacon until min_round exists",
    )
    _add_beacon_args(p_der)

    # ------------------------------------------------------------------
    # emit-interop
    # ------------------------------------------------------------------
    p_interop = sub.add_parser(
        "emit-interop",
        help=(
            "Emit unsigned interop JSON summaries (SLSA/in-toto vocabulary, "
            "RO-Crate, C2PA-style). Not verifiable provenance. Does not "
            "overwrite the FarmNotary manifest."
        ),
    )
    p_interop.add_argument("run_dir", help="Run directory containing manifest.json")
    p_interop.add_argument(
        "--format",
        dest="formats",
        action="append",
        metavar="FORMAT",
        help=(
            "Format to emit: slsa, ro-crate, c2pa. Repeatable. Defaults to "
            "all three. SLSA and C2PA files are named *.unsigned.json."
        ),
    )

    # ------------------------------------------------------------------
    # archive
    # ------------------------------------------------------------------
    p_arc = sub.add_parser(
        "archive",
        help=(
            "Optional durable-storage helpers (Zenodo draft/DOI, Software "
            "Heritage lookup). IDs are not claim-card rows and are not "
            "written to the manifest."
        ),
    )
    p_arc.add_argument("run_dir", help="Run directory containing manifest.json")
    p_arc.add_argument(
        "--zenodo",
        action="store_true",
        help="Deposit manifest.json to Zenodo and print the deposit URL",
    )
    p_arc.add_argument(
        "--zenodo-sandbox",
        action="store_true",
        help="Use the Zenodo sandbox instead of production",
    )
    p_arc.add_argument(
        "--zenodo-token",
        metavar="TOKEN",
        help="Zenodo personal access token (default: ZENODO_TOKEN env var)",
    )
    p_arc.add_argument(
        "--zenodo-publish",
        action="store_true",
        help="Publish the deposit immediately (assigns a DOI)",
    )
    p_arc.add_argument(
        "--zenodo-creator",
        metavar="NAME",
        required=False,
        help=(
            "Creator name for the Zenodo deposit (e.g. 'Smith, Jane'). "
            "Required for any --zenodo deposit (draft or publish)."
        ),
    )
    p_arc.add_argument(
        "--zenodo-files",
        metavar="FILE",
        nargs="+",
        help=(
            "Relative paths (within RUN_DIR) of artifact files to upload alongside "
            "manifest.json.  Defaults to manifest.json only."
        ),
    )
    p_arc.add_argument(
        "--swh",
        action="store_true",
        help="Look up the git SHA in Software Heritage and print the SWH identifier",
    )

    # ------------------------------------------------------------------
    # chain
    # ------------------------------------------------------------------
    p_chain = sub.add_parser(
        "chain",
        help=(
            "Build or verify a multi-stage hash lineage of manifests "
            "(not input/output data flow)."
        ),
    )
    p_chain.add_argument(
        "run_dirs",
        nargs="*",
        metavar="RUN_DIR",
        help="Run directories in pipeline order (earliest stage first)",
    )
    p_chain.add_argument(
        "--labels",
        nargs="+",
        metavar="LABEL",
        help="Stage labels (must match the number of run_dirs)",
    )
    p_chain.add_argument(
        "--out",
        metavar="DIR",
        help="Directory for provenance-chain.json (default: last run_dir)",
    )
    p_chain.add_argument(
        "--verify",
        action="store_true",
        help="Verify an existing provenance-chain.json instead of creating one",
    )
    p_chain.add_argument(
        "--chain-file",
        metavar="FILE",
        help="Path to provenance-chain.json for --verify (default: <last run_dir>/provenance-chain.json)",
    )

    return parser


def _add_beacon_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--beacon-url",
        help="drand HTTP base URL (implies a live fetch; default https://api.drand.sh)",
    )
    parser.add_argument(
        "--beacon-fixture",
        help="Fixed-beacon JSON for tests / offline use (or FARM_NOTARY_BEACON_FIXTURE)",
    )
    parser.add_argument(
        "--live-beacon",
        action="store_true",
        help="Fetch the recorded drand chain over HTTP (TLS; signatures not checked)",
    )


def _beacon_client_from_args(
    args: argparse.Namespace,
    *,
    chain_hash: Optional[str] = None,
    require: bool = False,
):
    from farm_notary.beacon import BeaconError, resolve_beacon_client

    fixture = getattr(args, "beacon_fixture", None)
    url = getattr(args, "beacon_url", None)
    live = bool(getattr(args, "live_beacon", False) or url or (require and not fixture))
    try:
        client = resolve_beacon_client(
            url=url,
            fixture=Path(fixture) if fixture else None,
            chain_hash=chain_hash,
            live=live,
        )
    except (BeaconError, OSError, ValueError) as exc:
        raise SystemExit(f"error: {exc}") from exc
    if require and client is None:
        raise SystemExit(
            "error: pass --beacon-fixture or --live-beacon / --beacon-url"
        )
    return client


def _seed_plan_chain_hash(precommit_path: Optional[str]) -> Optional[str]:
    if not precommit_path:
        return None
    from farm_notary.precommit import PRECOMMIT_NAME, load_precommit

    raw = Path(precommit_path)
    path = raw / PRECOMMIT_NAME if raw.is_dir() else raw
    if not path.is_file():
        return None
    try:
        pc = load_precommit(path)
    except (ValueError, OSError):
        return None
    plan = pc.get("seed_plan")
    if isinstance(plan, dict) and plan.get("chain_hash"):
        return str(plan["chain_hash"])
    return None


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
            from farm_notary.beacon import BeaconError as _BeaconError
            seed_index = getattr(args, "seed_index", None)
            chain_hash = _seed_plan_chain_hash(getattr(args, "precommit", None))
            beacon_client = (
                _beacon_client_from_args(args, chain_hash=chain_hash)
                if seed_index is not None or getattr(args, "beacon_fixture", None)
                else None
            )
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
                seed_index=seed_index,
                beacon_client=beacon_client,
                seeds_path=Path(args.seeds) if getattr(args, "seeds", None) else None,
            )
        except (ValueError, _BeaconError) as exc:
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
    if manifest.withheld_root:
        classes = manifest.withheld_classes or {}
        parts = [
            f"{name}={spec.get('count')}"
            for name, spec in classes.items()
            if isinstance(spec, dict)
        ]
        print("withheld_root", manifest.withheld_root)
        if parts:
            print("withheld_classes", " ".join(parts))
    print("content_hash", manifest.content_hash())
    return 0


def _cmd_anchor(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    writer: Callable[[Any, Path], Path] = write_manifest
    manifest: Union[Manifest, Campaign]
    manifest_path = run_dir / MANIFEST_NAME
    if manifest_path.is_file():
        try:
            manifest = load_manifest(manifest_path)
        except (ValueError, OSError) as exc:
            print(f"error: could not load manifest: {exc}", file=sys.stderr)
            return 2
    else:
        from farm_notary.campaign import CAMPAIGN_NAME, load_campaign, write_campaign

        campaign_path = run_dir / CAMPAIGN_NAME
        if not campaign_path.is_file():
            print(
                f"error: {manifest_path} not found; run `farm-notary manifest --run-dir {run_dir}` first",
                file=sys.stderr,
            )
            return 2
        try:
            manifest = load_campaign(campaign_path)
        except (ValueError, OSError) as exc:
            print(f"error: could not load campaign: {exc}", file=sys.stderr)
            return 2
        writer = write_campaign
    try:
        require_clean_identity(getattr(manifest, "git_dirty", None), allow_dirty=args.allow_dirty)
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
        artifacts = list(getattr(manifest, "artifacts", []) or [])
        record_name = MANIFEST_NAME if (run_dir / MANIFEST_NAME).is_file() else "campaign.json"
        pin_names = artifacts + [record_name]
        cid = client.add_run_dir(run_dir, pin_names)
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
    if cid is not None and receipt.backend == "opentimestamps":
        write_cid_binding_proof(manifest.content_hash(), cid, run_dir, receipt)

    no_write = getattr(args, "no_write", False)
    if not no_write:
        writer(manifest, run_dir)

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

    if getattr(args, "campaign", None):
        return _cmd_verify_campaign(args)

    if args.manifest:
        manifest_path = Path(args.manifest)
        run_dir = manifest_path.parent
    elif args.run_dir:
        run_dir = Path(args.run_dir)
        manifest_path = run_dir / MANIFEST_NAME
        if not manifest_path.is_file() and (run_dir / "campaign.json").is_file():
            args.campaign = str(run_dir)
            return _cmd_verify_campaign(args)
    else:
        print("error: pass --run-dir, --manifest, or --campaign", file=sys.stderr)
        return 2
    from farm_notary.precommit import PRECOMMIT_NAME, load_precommit

    pc = None
    pc_path = run_dir / PRECOMMIT_NAME
    chain_hash = None
    if pc_path.is_file():
        try:
            pc = load_precommit(pc_path)
            plan = pc.get("seed_plan")
            if isinstance(plan, dict) and plan.get("chain_hash"):
                chain_hash = str(plan["chain_hash"])
        except (ValueError, OSError):
            pc = None
    try:
        manifest = load_manifest(manifest_path, validate=False)
    except (ValueError, OSError) as exc:
        print(f"error: could not load manifest: {exc}", file=sys.stderr)
        return 2

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        card = evaluate_claims(
            manifest,
            run_dir,
            beacon_client=_beacon_client_from_args(args, chain_hash=chain_hash),
            precommit=pc,
        )
    for w in caught:
        print(f"warning: {w.message}", file=sys.stderr)
    problems = []
    ran_derived = bool(getattr(args, "verify_derived", False))
    if ran_derived:
        problems += verify_derived_artifacts(manifest, run_dir, allow_execute=True)
    else:
        from farm_notary.derive import validate_derived_rules

        problems += validate_derived_rules(manifest)

    print(card.render(), end="")
    if card.notes:
        print()
        for note in card.notes:
            print(note)
    all_problems = list(card.problems)
    for problem in problems:
        if problem not in all_problems:
            all_problems.append(problem)
    if all_problems:
        print()
        for problem in all_problems:
            print("FAIL", problem)
        return 1
    print("OK", manifest.content_hash())
    if ran_derived and getattr(manifest, "derived_from", None):
        print("claim: statistics recompute exactly from recorded sources")
    elif getattr(manifest, "derived_from", None):
        print(
            "note: derivation rules are recorded but were not executed "
            "(pass --verify-derived to check; only for manifests you trust)"
        )
    identity = getattr(manifest, "identity", None)
    if identity:
        scheme = identity.get("scheme")
        principal = identity.get("principal") or "lab key"
        print(f"identity: {scheme} signature by {principal} verified")
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
        seed_count = getattr(args, "seed_count", None)
        if seed_count is not None and not getattr(args, "inclusion", None):
            print("error: --inclusion is required with --seed-count", file=sys.stderr)
            return 2
        beacon_client = None
        if seed_count is not None:
            beacon_client = _beacon_client_from_args(args, require=True)
        pc = build_precommit(
            config=_load_json_arg(args.config),
            command=args.command,
            git_sha=args.git_sha,
            git_dirty=detected_dirty,
            lockfile=Path(args.lockfile) if args.lockfile else None,
            allow_dirty=args.allow_dirty,
            seed_count=seed_count,
            inclusion=getattr(args, "inclusion", None),
            delay_rounds=getattr(args, "delay_rounds", 1),
            beacon_client=beacon_client,
        )
    except (DirtyTreeError, ValueError) as exc:
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
    if pc.get("seed_plan"):
        plan = pc["seed_plan"]
        print("seed_plan count", plan.get("count"))
        print("seed_plan min_round", plan.get("min_round"))
        print("seed_plan inclusion", plan.get("inclusion"))

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


def _cmd_derive_seeds(args: argparse.Namespace) -> int:
    from farm_notary.beacon import (
        SEEDS_NAME,
        BeaconError,
        derive_seeds,
        fetch_plan_round,
        seeds_record,
        write_seeds,
    )
    from farm_notary.precommit import (
        PRECOMMIT_NAME,
        load_precommit,
        require_precommit_proof,
    )

    raw = Path(args.precommit)
    pc_path = raw / PRECOMMIT_NAME if raw.is_dir() else raw
    try:
        pc = load_precommit(pc_path)
    except (ValueError, OSError) as exc:
        print(f"error: could not load precommit: {exc}", file=sys.stderr)
        return 2
    plan = pc.get("seed_plan")
    if not plan:
        print("error: precommit has no seed_plan (pass --seed-count)", file=sys.stderr)
        return 2
    try:
        require_precommit_proof(pc, pc_path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    client = _beacon_client_from_args(
        args, chain_hash=str(plan.get("chain_hash") or "") or None, require=True
    )
    try:
        fetched = fetch_plan_round(plan, client, wait=bool(args.wait))
        seeds = derive_seeds(plan, pc.get("config"), fetched.randomness, round=fetched.round)
    except BeaconError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    dest = Path(args.out) if args.out else pc_path.parent / SEEDS_NAME
    if dest.is_dir() or dest.suffix == "":
        dest.mkdir(parents=True, exist_ok=True)
        dest = dest / SEEDS_NAME
    write_seeds(seeds_record(plan, fetched, seeds), dest)
    print(dest)
    print("round", fetched.round)
    print("count", len(seeds))
    for i, seed in enumerate(seeds):
        print(f"seed {i} {seed}")
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
    cwd = Path(args.cwd) if args.cwd else None
    try:
        manifest = load_manifest(run_dir)
    except (ValueError, OSError) as exc:
        print(f"error: could not load manifest: {exc}", file=sys.stderr)
        return 2
    trust_note = _trusted_reproduce_source(manifest, cwd=cwd, run_dir=run_dir)
    print(
        "warning: `reproduce` executes the manifest's recorded command via the shell.",
        file=sys.stderr,
    )
    if trust_note is None and not args.accept_untrusted_command:
        print(
            "warning: this manifest is outside the local checkout / CI context "
            "already being trusted; treat it as untrusted code execution.",
            file=sys.stderr,
        )
        print(
            "error: refusing to run an untrusted recorded command without "
            "--i-accept-untrusted-command. Automatic trust is limited to the "
            "same local checkout (matching git_sha) or the same GitHub Actions "
            "repo/SHA already running this tool.",
            file=sys.stderr,
        )
        print(
            "warning: if you choose to proceed, do it inside your own sandbox "
            "(for example: container or VM, with no network).",
            file=sys.stderr,
        )
        return 2
    if trust_note == "ci":
        print(
            "warning: trusted context matched the current GitHub Actions repo/SHA.",
            file=sys.stderr,
        )
    elif trust_note == "local":
        print(
            "warning: trusted context matched the local checkout git_sha.",
            file=sys.stderr,
        )
    else:
        print(
            "warning: proceeding only because --i-accept-untrusted-command was "
            "given; treat this as untrusted code execution. Sandboxing "
            "(container/VM, no network) is your responsibility.",
            file=sys.stderr,
        )
    try:
        result = reproduce_run(
            manifest,
            fresh_dir=Path(args.fresh_dir) if args.fresh_dir else None,
            ignore=args.ignore,
            original_dir=run_dir,
            cwd=cwd,
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

    if args.sign:
        if not result.ok:
            print(
                "warning: skipping Sigstore sign because reproduction failed",
                file=sys.stderr,
            )
        else:
            try:
                token = read_identity_token_cli(getattr(args, "identity_token", None))
                bundle = sign_receipt(receipt, identity_token=token)
                receipt["sigstore"] = bundle
                write_receipt(receipt, run_dir)
                print("receipt signed with Sigstore keyless; bundle embedded in receipt")
            except SigstoreError as exc:
                print(f"sigstore signing failed: {exc}", file=sys.stderr)
                return 2

    return 0 if result.ok else 1


def _trusted_reproduce_source(
    manifest, *, cwd: Optional[Path], run_dir: Path
) -> Optional[str]:
    probe_dirs = [cwd] if cwd is not None else [run_dir]
    for probe_dir in probe_dirs:
        try:
            local_sha, _ = detect_git_status(cwd=probe_dir)
        except OSError:
            continue
        if manifest.git_sha and local_sha and manifest.git_sha == local_sha:
            return "local"
    if _running_in_same_ci_context(manifest):
        return "ci"
    return None


def _running_in_same_ci_context(manifest) -> bool:
    """True when this process is the same GitHub Actions repo/SHA as the manifest.

    Trust requires ``GITHUB_ACTIONS=true`` plus a ``GITHUB_REPOSITORY`` /
    ``GITHUB_SHA`` pair that matches the manifest's recorded
    ``ci_provenance`` (or, absent that, its ``git_sha``) exactly. This never
    trusts a manifest downloaded from elsewhere and run in a different CI
    job or repo.
    """
    import os

    if os.environ.get("GITHUB_ACTIONS") != "true":
        return False
    current_repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    current_sha = os.environ.get("GITHUB_SHA", "").strip()
    if not current_repo or not current_sha:
        return False
    prov = manifest.ci_provenance
    if isinstance(prov, dict):
        # ci_provenance is present: it must fully agree (repo *and* sha), or
        # this manifest is not trusted. Do not fall back to a bare git_sha
        # match on a partially-filled record — that would let an attacker
        # spoof an empty/blank repository or sha field to dodge the repo
        # check.
        prov_repo = str(prov.get("repository", "")).strip()
        prov_sha = str(prov.get("sha", "")).strip()
        return (
            bool(prov_repo)
            and bool(prov_sha)
            and prov_repo == current_repo
            and prov_sha == current_sha
        )
    # No recorded ci_provenance (e.g. manifest built outside CI, or the
    # detail was intentionally omitted): fall back to matching git_sha
    # against the current run's SHA. Still requires a live GITHUB_ACTIONS
    # context, so it cannot be satisfied by a downloaded manifest run
    # locally.
    return bool(manifest.git_sha) and manifest.git_sha == current_sha


def _cmd_upgrade(args: argparse.Namespace) -> int:
    from farm_notary.ots import CID_BINDING_PROOF_NAME, PROOF_NAME, upgrade_proof

    run_dir = Path(args.run_dir)
    manifest_proof_path = run_dir / PROOF_NAME
    if not manifest_proof_path.is_file():
        print(
            f"error: {manifest_proof_path} not found; anchor with --backend ots first",
            file=sys.stderr,
        )
        return 2
    proof_paths = [manifest_proof_path]
    cid_binding_proof_path = run_dir / CID_BINDING_PROOF_NAME
    if cid_binding_proof_path.is_file():
        proof_paths.append(cid_binding_proof_path)

    all_confirmed = True
    for proof_path in proof_paths:
        upgraded, status, errors = upgrade_proof(proof_path.read_bytes())
        proof_path.write_bytes(upgraded)
        for line in status.summary():
            print(f"{proof_path.name}: {line}")
        for error in errors:
            print(f"note: {proof_path.name}: {error}", file=sys.stderr)
        all_confirmed = all_confirmed and status.confirmed
    return 0 if all_confirmed else 1


def _cmd_register_schema(_args: argparse.Namespace) -> int:
    from farm_notary.eas import FARM_NOTARY_SCHEMA, register_schema

    uid = register_schema()
    print(json.dumps({"schema": FARM_NOTARY_SCHEMA, "schema_uid": uid}, indent=2))
    return 0


def _cmd_verify_campaign(args: argparse.Namespace) -> int:
    from farm_notary.campaign import load_campaign, verify_campaign

    campaign_arg = Path(args.campaign)
    campaign_dir = campaign_arg if campaign_arg.is_dir() else campaign_arg.parent
    try:
        campaign = load_campaign(campaign_arg, validate=False)
    except (ValueError, OSError) as exc:
        print(f"error: could not load campaign: {exc}", file=sys.stderr)
        return 2
    problems = verify_campaign(
        campaign,
        campaign_dir,
        require_local=getattr(args, "require_local", False),
    )
    problems += verify_anchor(campaign, campaign_dir)
    problems += verify_identity_record(campaign, campaign_dir)
    if problems:
        for problem in problems:
            print("FAIL", problem)
        return 1
    print("OK", campaign.content_hash())
    print(f"campaign: {len(campaign.runs)} child run(s)")
    from farm_notary.campaign import campaign_seed_coverage_note

    coverage = campaign_seed_coverage_note(campaign)
    if coverage:
        print(coverage)
    if campaign.config_hash:
        print("config_hash", campaign.config_hash)
    if campaign.identity:
        scheme = campaign.identity.get("scheme")
        principal = campaign.identity.get("principal") or "lab key"
        print(f"identity: {scheme} signature by {principal} verified")
    return 0


def _cmd_campaign(args: argparse.Namespace) -> int:
    from farm_notary.campaign import build_campaign, write_campaign

    run_dirs = [Path(p) for p in args.run_dirs]
    missing = [p for p in run_dirs if not (p / MANIFEST_NAME).is_file()]
    if missing:
        print(
            "error: child run is missing manifest.json: "
            + ", ".join(str(p) for p in missing),
            file=sys.stderr,
        )
        return 2
    out = Path(args.out)
    campaign_dir = out if out.suffix == "" or out.is_dir() else out.parent
    try:
        campaign = build_campaign(
            run_dirs,
            name=args.name,
            config=_load_json_arg(args.config),
            git_sha=args.git_sha,
            command=args.command,
            lockfile=Path(args.lockfile) if args.lockfile else None,
            campaign_dir=campaign_dir,
        )
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    hashes = {run.get("config_hash") for run in campaign.runs}
    if len(hashes) > 1:
        print(
            "warning: child runs do not share a seed-excluded config hash; "
            "the campaign records per-run hashes",
            file=sys.stderr,
        )
    path = write_campaign(campaign, out)
    print(path)
    print("runs", len(campaign.runs))
    if campaign.config_hash:
        print("config_hash", campaign.config_hash)
    print("content_hash", campaign.content_hash())
    return 0


def _load_signable(args: argparse.Namespace):
    """Return (record, directory, writer) for a manifest or campaign."""
    if getattr(args, "campaign", None):
        from farm_notary.campaign import load_campaign, write_campaign

        path = Path(args.campaign)
        directory = path if path.is_dir() else path.parent
        return load_campaign(path), directory, write_campaign
    run_dir = Path(args.run_dir) if getattr(args, "run_dir", None) else None
    if run_dir is None:
        raise SystemExit("error: pass --run-dir or --campaign")
    if (run_dir / MANIFEST_NAME).is_file():
        return load_manifest(run_dir), run_dir, write_manifest
    from farm_notary.campaign import CAMPAIGN_NAME, load_campaign, write_campaign

    if (run_dir / CAMPAIGN_NAME).is_file():
        return load_campaign(run_dir), run_dir, write_campaign
    raise SystemExit(f"error: no manifest.json or campaign.json in {run_dir}")


def _cmd_sign(args: argparse.Namespace) -> int:
    from farm_notary.identity import IdentityError, sign_record

    try:
        record, directory, writer = _load_signable(args)
    except (ValueError, OSError) as exc:
        print(f"error: could not load record: {exc}", file=sys.stderr)
        return 2
    before = record.content_hash()
    try:
        identity = sign_record(
            record,
            scheme=args.scheme,
            key_path=Path(args.key),
            principal=args.principal,
        )
    except IdentityError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if record.content_hash() != before:
        print("error: signing must not change the content hash", file=sys.stderr)
        return 1
    writer(record, directory)
    print("scheme", identity["scheme"])
    if identity.get("principal"):
        print("principal", identity["principal"])
    print("content_hash", before)
    return 0


def _cmd_paper_pack(args: argparse.Namespace) -> int:
    from farm_notary.paper import build_paper_pack, write_paper_pack

    try:
        record, directory, _writer = _load_signable(args)
    except (ValueError, OSError) as exc:
        print(f"error: could not load record: {exc}", file=sys.stderr)
        return 2
    derived_ok = None
    if getattr(record, "derived_from", None):
        derived_problems = verify_derived_artifacts(record, directory, allow_execute=getattr(args, "verify_derived", False))
        derived_ok = not derived_problems if getattr(args, "verify_derived", False) else None
    markdown = build_paper_pack(
        record,
        directory,
        derived_ok=derived_ok,
        experiment=args.name,
    )
    dest = Path(args.out) if args.out else directory
    path = write_paper_pack(markdown, dest)
    print(path)
    print(markdown, end="" if markdown.endswith("\n") else "\n")
    return 0


def _cmd_index(args: argparse.Namespace) -> int:
    from farm_notary.registry import (
        RegistryError,
        add_to_registry,
        entries_from_campaign,
        entry_from_manifest,
    )

    incoming = []
    try:
        if args.campaign:
            from farm_notary.campaign import load_campaign

            campaign = load_campaign(Path(args.campaign))
            incoming = entries_from_campaign(campaign, experiment=args.name)
            if args.claim_level:
                for row in incoming:
                    row["claim_level"] = args.claim_level
        elif args.run_dir:
            run_dir = Path(args.run_dir)
            manifest = load_manifest(run_dir)
            incoming = [
                entry_from_manifest(
                    manifest,
                    run_dir,
                    experiment=args.name,
                    claim_level=args.claim_level,
                )
            ]
        else:
            print("error: pass --run-dir or --campaign", file=sys.stderr)
            return 2
        md_path, _json_path, added = add_to_registry(Path(args.registry), incoming)
    except (ValueError, OSError, RegistryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(md_path)
    print("added", added)
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    """Quick reviewer check: claim level + anchor status from manifest only.

    No artifact rehash. Works with zero extras (base install). Passes ``[ots]``
    proof bytes through the OTS library when it is installed; otherwise reports
    that the proof cannot be verified and exits 0 so a missing extra is not a
    false failure.
    """
    manifest_path = Path(args.manifest)
    try:
        from farm_notary.schema import MANIFEST_VERSION, REQUIRED_KEYS

        raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(raw_manifest, dict):
            raise ValueError("manifest must be a JSON object")
        missing = [key for key in REQUIRED_KEYS if key not in raw_manifest]
        if missing:
            raise ValueError(f"manifest missing keys: {missing}")
        if raw_manifest.get("schema") != MANIFEST_VERSION:
            raise ValueError(
                f"unsupported manifest schema {raw_manifest.get('schema')!r}, "
                f"expected {MANIFEST_VERSION!r}"
            )
        manifest = load_manifest(manifest_path)
    except (ValueError, OSError) as exc:
        print(f"error: could not load manifest: {exc}", file=sys.stderr)
        return 2

    run_dir = manifest_path.parent
    problems: list = []
    content_hash = manifest.content_hash()

    print(f"content_hash: {content_hash}")
    if manifest.cid:
        print(f"cid:          {manifest.cid}")

    from farm_notary.claims import infer_claim_level

    print(f"claim_level:  {infer_claim_level(manifest)}")
    if manifest.withheld_root:
        classes = manifest.withheld_classes or {}
        parts = [
            f"{name}={spec.get('count')}"
            for name, spec in classes.items()
            if isinstance(spec, dict)
        ]
        print(f"withheld:     root {manifest.withheld_root[:12]}… ({', '.join(parts)})")

    # Identity/issuer — display only, not validated (public key not required)
    identity = getattr(manifest, "identity", None)
    if identity:
        scheme = identity.get("scheme", "unknown")
        principal = identity.get("principal") or "lab key"
        print(f"identity:     {scheme} signature by {principal} (declared, not validated here)")

    # Anchor
    if manifest.anchor is None:
        print("anchor:       missing")
    else:
        anchored_hash = manifest.anchor.get("manifest_hash")
        if anchored_hash != content_hash:
            problems.append(
                f"anchored hash {anchored_hash!r} does not match "
                f"content hash {content_hash!r}"
            )
            print("anchor:       FAIL (hash mismatch)")
        else:
            backend = manifest.anchor.get("backend")
            if backend == "opentimestamps":
                _check_ots_anchor(manifest, run_dir, content_hash, problems)
            else:
                print(f"anchor:       {backend or 'unknown backend'}")

    if problems:
        print()
        for problem in problems:
            print("FAIL", problem)
        return 1
    return 0


def _check_ots_anchor(
    manifest: Manifest,
    run_dir: Path,
    content_hash: str,
    problems: list,
) -> None:
    """Print OTS anchor status; append to *problems* on hard failures."""
    missing_ots_msg = (
        "anchor:       OTS proof present but farm-notary[ots] not installed; "
        "install it to verify the proof commits to this hash"
    )
    try:
        from farm_notary.ots import PROOF_NAME, OtsError, proof_status, verify_proof
    except ImportError:
        anchor = manifest.anchor or {}
        detail = anchor.get("detail", {}) or {}
        proof_name = detail.get("proof", "manifest.ots") if isinstance(detail, dict) else "manifest.ots"
        try:
            proof_path = resolve_run_path(run_dir, proof_name)
        except ValueError:
            print(f"anchor:       invalid OTS proof file path ({proof_name!r})")
            return
        if not proof_path.is_file():
            print("anchor:       OTS proof file absent (install farm-notary[ots] to verify)")
            return
        print(missing_ots_msg)
        return

    anchor = manifest.anchor or {}
    detail = anchor.get("detail", {}) or {}
    proof_name = detail.get("proof", PROOF_NAME) if isinstance(detail, dict) else PROOF_NAME
    try:
        proof_path = resolve_run_path(run_dir, proof_name)
    except ValueError:
        print(f"anchor:       invalid proof file path ({proof_name!r})")
        return
    if not proof_path.is_file():
        print(f"anchor:       proof file missing ({proof_name!r})")
        return

    proof_bytes = proof_path.read_bytes()
    try:
        proof_problems = verify_proof(proof_bytes, content_hash)
    except ImportError:
        print(missing_ots_msg)
        return
    if proof_problems:
        if all("install farm-notary[ots]" in problem for problem in proof_problems):
            print(missing_ots_msg)
            return
        problems.extend(proof_problems)
        print("anchor:       FAIL (proof does not commit to this hash)")
        return

    try:
        status = proof_status(proof_bytes)
    except ImportError:
        print(missing_ots_msg)
        return
    except (OtsError, ValueError, OSError) as exc:
        print(f"anchor:       OTS proof present (status unknown: {exc})", file=sys.stderr)
        print("anchor:       OTS proof present (status unknown)")
        return

    if status.bitcoin_heights:
        print(f"anchor:       Bitcoin height {min(status.bitcoin_heights)}")
    elif status.public_pending_calendars:
        cals = ", ".join(status.public_pending_calendars)
        print(
            f"anchor:       pending on public OpenTimestamps calendars: {cals} "
            "(unverified claim; not yet Bitcoin-attested)"
        )
    elif status.unknown_pending_calendars:
        cals = ", ".join(status.unknown_pending_calendars)
        print(
            f"anchor:       pending at user-supplied calendars: {cals} "
            "(unverified claim; untrusted until Bitcoin)"
        )
    else:
        print("anchor:       OTS proof present (pending / status unknown)")


def _cmd_emit_interop(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"error: {manifest_path} not found", file=sys.stderr)
        return 2
    from farm_notary.interop import INTEROP_FORMATS, emit_interop
    from farm_notary.manifest import load_manifest

    try:
        manifest = load_manifest(manifest_path)
    except (ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    formats = tuple(args.formats) if args.formats else INTEROP_FORMATS
    try:
        written = emit_interop(manifest, run_dir, formats=formats)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    for fmt, path in written.items():
        print(f"{fmt}: {path}")
    return 0


def _cmd_archive(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"error: {manifest_path} not found", file=sys.stderr)
        return 2
    from farm_notary.manifest import load_manifest

    try:
        manifest = load_manifest(manifest_path)
    except (ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    rc = 0
    if args.zenodo:
        from farm_notary.archive import ZenodoError, deposit_manifest

        try:
            zenodo_creator = getattr(args, "zenodo_creator", None)
            zenodo_publish = getattr(args, "zenodo_publish", False)
            if not zenodo_creator:
                print(
                    "error: --zenodo-creator is required for any Zenodo deposit "
                    "(draft or publish)",
                    file=sys.stderr,
                )
                return 2
            metadata: Optional[Dict[str, Any]] = None
            if zenodo_creator:
                metadata = {"creators": [{"name": zenodo_creator}]}
            zenodo_files = getattr(args, "zenodo_files", None) or None
            result = deposit_manifest(
                manifest,
                str(run_dir),
                token=getattr(args, "zenodo_token", None) or None,
                sandbox=getattr(args, "zenodo_sandbox", False),
                publish=zenodo_publish,
                files=zenodo_files,
                metadata=metadata,
            )
            if "doi" in result:
                print(f"zenodo doi: {result['doi']}")
            else:
                print(f"zenodo deposit id: {result.get('id')}")
                links = result.get("links", {})
                if "html" in links:
                    print(f"zenodo url: {links['html']}")
        except (ZenodoError, ValueError) as exc:
            print(f"error: zenodo: {exc}", file=sys.stderr)
            rc = 1

    if args.swh:
        from farm_notary.archive import SoftwareHeritageError, swh_lookup

        try:
            swh_id = swh_lookup(manifest)
            if swh_id:
                print(f"swh: {swh_id}")
            else:
                print("swh: not found (commit may not be archived yet)")
        except SoftwareHeritageError as exc:
            print(f"error: swh: {exc}", file=sys.stderr)
            rc = 1

    if not args.zenodo and not args.swh:
        print("error: specify --zenodo and/or --swh", file=sys.stderr)
        return 2

    return rc


def _cmd_chain(args: argparse.Namespace) -> int:
    from farm_notary.chain import (
        CHAIN_FILE_NAME,
        chain_run_dirs,
        load_chain,
        verify_chain,
    )

    if getattr(args, "verify", False):
        # Verify mode: load and check an existing chain.
        chain_path: Optional[Path]
        if getattr(args, "chain_file", None):
            chain_path = Path(args.chain_file)
        elif getattr(args, "run_dirs", None):
            chain_path = Path(args.run_dirs[-1]) / CHAIN_FILE_NAME
        else:
            print("error: --verify requires run_dirs or --chain-file", file=sys.stderr)
            return 2
        if not chain_path.exists():
            print(f"error: {chain_path} not found", file=sys.stderr)
            return 2
        try:
            chain = load_chain(chain_path)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if not chain:
            print("error: chain file is empty", file=sys.stderr)
            return 2
        errors = verify_chain(chain, chain_dir=chain_path.parent)
        if errors:
            for err in errors:
                print(f"chain error: {err}", file=sys.stderr)
            return 1
        print(f"ok: chain {chain_path} ({len(chain)} links, tip {chain[-1].link_hash[:12]})")
        return 0

    # Build mode.
    if not args.run_dirs:
        print("error: at least one RUN_DIR is required when not using --verify", file=sys.stderr)
        return 2
    run_dirs = [Path(d) for d in args.run_dirs]
    for d in run_dirs:
        if not (d / "manifest.json").exists():
            print(f"error: {d}/manifest.json not found", file=sys.stderr)
            return 2

    out_dir = Path(args.out) if getattr(args, "out", None) else None
    labels = args.labels if getattr(args, "labels", None) else None

    try:
        chain = chain_run_dirs(run_dirs, stage_labels=labels, output_dir=out_dir)
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    dest = (out_dir or run_dirs[-1]) / CHAIN_FILE_NAME
    print(f"chain: {dest} ({len(chain)} links)")
    print(f"tip link_hash: {chain[-1].link_hash}")
    return 0


def _cmd_reveal_withheld(args: argparse.Namespace) -> int:
    from farm_notary.manifest import list_withheld, load_manifest
    from farm_notary.withheld import (
        load_reveal,
        reveal_withheld,
        verify_reveal,
        write_reveal,
    )

    run_dir = Path(args.run_dir)
    try:
        manifest = load_manifest(run_dir)
    except (ValueError, OSError) as exc:
        print(f"error: could not load manifest: {exc}", file=sys.stderr)
        return 2
    if not manifest.withheld_root or not manifest.withheld_salt:
        print("error: manifest has no withheld commitment", file=sys.stderr)
        return 2

    if args.verify:
        reveal_path = Path(args.reveal) if args.reveal else None
        if reveal_path is None:
            print("error: --verify requires --reveal PATH", file=sys.stderr)
            return 2
        try:
            entries = load_reveal(reveal_path)
            problems = verify_reveal(
                entries,
                salt_hex=manifest.withheld_salt,
                root_hex=manifest.withheld_root,
                run_dir=run_dir,
            )
        except (ValueError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if problems:
            for problem in problems:
                print("FAIL", problem)
            return 1
        print("ok", len(entries), "revealed file(s) commit to withheld_root")
        return 0

    if not args.paths:
        print(
            "error: pass --path REL for each file to reveal "
            "(withheld names are not listed)",
            file=sys.stderr,
        )
        return 2
    try:
        withheld = list_withheld(run_dir, manifest.publish_patterns)
        entries = reveal_withheld(
            withheld, args.paths, salt_hex=manifest.withheld_salt
        )
        problems = verify_reveal(
            entries,
            salt_hex=manifest.withheld_salt,
            root_hex=manifest.withheld_root,
            run_dir=run_dir,
        )
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if problems:
        for problem in problems:
            print("FAIL", problem)
        return 1
    if args.out:
        dest_path = Path(args.out).resolve()
        resolved_run_dir = run_dir.resolve()
        try:
            dest_path.relative_to(resolved_run_dir)
            is_inside = True
        except ValueError:
            is_inside = False
        if is_inside:
            print(
                "error: --out destination must not be inside the run directory; "
                "the reveal file would invalidate the withheld_root commitment",
                file=sys.stderr,
            )
            return 2
        dest = write_reveal(entries, dest_path)
        print(dest)
    print("revealed", len(entries))
    print("withheld_root", manifest.withheld_root)
    return 0


def main(argv: Optional[list] = None) -> int:
    args = _build_parser().parse_args(argv)
    handlers = {
        "manifest": _cmd_manifest,
        "anchor": _cmd_anchor,
        "verify": _cmd_verify,
        "check": _cmd_check,
        "upgrade": _cmd_upgrade,
        "reproduce": _cmd_reproduce,
        "derive-seeds": _cmd_derive_seeds,
        "precommit": _cmd_precommit,
        "register-schema": _cmd_register_schema,
        "campaign": _cmd_campaign,
        "sign": _cmd_sign,
        "paper-pack": _cmd_paper_pack,
        "index": _cmd_index,
        "emit-interop": _cmd_emit_interop,
        "archive": _cmd_archive,
        "chain": _cmd_chain,
        "reveal-withheld": _cmd_reveal_withheld,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
