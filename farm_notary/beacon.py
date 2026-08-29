"""Public-randomness beacon binding for L2 (drand).

A seed plan on the precommit records how many seeds will be derived and
which beacon round (``min_round``) must supply the randomness. After the
plan is stamped, ``derive-seeds`` fetches that exact round and computes

    seed_i = uint64_be(SHA256(canonical_json({
        chain_hash, round, index, config_hash
    }) || randomness)[:8])

``verify`` recomputes the same function. Live verify compares recorded
randomness to the configured drand HTTP endpoint (TLS; threshold
signatures are not checked). Tests use :class:`FixedBeacon`. A fetch
failure leaves L2 unearned (missing), not a hard verify failure.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence

from farm_notary.manifest import SEED_KEYS, config_hash_excluding_seed, hash_json
from farm_notary.ots import OtsError, verify_proof

DERIVATION_SHA256_V1 = "sha256-v1"
SEEDS_NAME = "seeds.json"

# drand quicknet (3s). Recorded on the plan so verify uses the same chain.
DRAND_QUICKNET_HASH = (
    "52db9ba70e0cc0f6eaf7803dd07447a1f5477735fd3f661792ba94600c84e971"
)
DEFAULT_DRAND_URL = "https://api.drand.sh"
BEACON_FIXTURE_ENV = "FARM_NOTARY_BEACON_FIXTURE"


class BeaconError(Exception):
    """Beacon fetch, fixture, or derivation failed."""


class BeaconRoundUnavailable(BeaconError):
    """The requested round is not published yet (safe to poll)."""


INCLUSION_PRESETS = frozenset({"all_in_campaign", "primary_endpoint"})


@dataclass(frozen=True)
class BeaconChain:
    chain_hash: str
    genesis_time: int
    period: int


@dataclass(frozen=True)
class BeaconRound:
    chain_hash: str
    round: int
    randomness: bytes
    unix_time: int
    genesis_time: int
    period: int


class BeaconClient(Protocol):
    def chain_info(self) -> BeaconChain:
        ...

    def latest(self) -> BeaconRound:
        ...

    def get_round(self, round_id: int) -> BeaconRound:
        ...


def round_unix_time(genesis_time: int, period: int, round_id: int) -> int:
    if round_id < 1:
        raise BeaconError(f"beacon round must be >= 1, got {round_id}")
    if period < 1:
        raise BeaconError(f"beacon period must be >= 1, got {period}")
    return int(genesis_time) + (int(round_id) - 1) * int(period)


def derive_seed_v1(
    *,
    chain_hash: str,
    round: int,
    index: int,
    config_hash: str,
    randomness: bytes,
) -> int:
    """Return the ``sha256-v1`` integer seed for one committed index."""
    material = {
        "chain_hash": chain_hash,
        "config_hash": config_hash,
        "index": int(index),
        "round": int(round),
    }
    blob = json.dumps(material, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    digest = hashlib.sha256(blob + bytes(randomness)).digest()
    return int.from_bytes(digest[:8], "big")


def derive_seeds(
    seed_plan: Mapping[str, Any],
    config: Optional[Mapping[str, Any]],
    randomness: bytes,
    *,
    round: Optional[int] = None,
) -> List[int]:
    """Derive ``count`` integer seeds from a plan and beacon randomness."""
    derivation = seed_plan.get("derivation")
    if derivation != DERIVATION_SHA256_V1:
        raise BeaconError(
            f"unsupported seed derivation {derivation!r}, expected {DERIVATION_SHA256_V1!r}"
        )
    count = int(seed_plan["count"])
    if count < 1:
        raise BeaconError(f"seed_plan.count must be >= 1, got {count}")
    used_round = int(round if round is not None else seed_plan["min_round"])
    chain_hash = str(seed_plan["chain_hash"])
    cfg_hash = config_hash_excluding_seed(config)
    return [
        derive_seed_v1(
            chain_hash=chain_hash,
            round=used_round,
            index=i,
            config_hash=cfg_hash,
            randomness=randomness,
        )
        for i in range(count)
    ]


def derive_seed_single(
    seed_plan: Mapping[str, Any],
    config: Optional[Mapping[str, Any]],
    randomness: bytes,
    index: int,
    *,
    round: Optional[int] = None,
) -> int:
    """Derive a single integer seed for ``index`` without materialising all seeds."""
    derivation = seed_plan.get("derivation")
    if derivation != DERIVATION_SHA256_V1:
        raise BeaconError(
            f"unsupported seed derivation {derivation!r}, expected {DERIVATION_SHA256_V1!r}"
        )
    count = int(seed_plan["count"])
    if count < 1:
        raise BeaconError(f"seed_plan.count must be >= 1, got {count}")
    used_round = int(round if round is not None else seed_plan["min_round"])
    chain_hash = str(seed_plan["chain_hash"])
    cfg_hash = config_hash_excluding_seed(config)
    return derive_seed_v1(
        chain_hash=chain_hash,
        round=used_round,
        index=index,
        config_hash=cfg_hash,
        randomness=randomness,
    )


def config_has_seed(config: Optional[Mapping[str, Any]]) -> bool:
    if not config:
        return False
    return any(key in config for key in SEED_KEYS)


def strip_seed_keys(config: Optional[Mapping[str, Any]]) -> dict:
    return {k: v for k, v in dict(config or {}).items() if k not in SEED_KEYS}


def config_seed_key(config: Optional[Mapping[str, Any]]) -> Optional[str]:
    for key in SEED_KEYS:
        if config and key in config:
            return key
    return None


def validate_inclusion(inclusion: str) -> str:
    text = str(inclusion).strip()
    if text in INCLUSION_PRESETS or text.startswith("other:"):
        return text
    raise ValueError(
        "--inclusion must be all_in_campaign, primary_endpoint, or other:<label>"
    )


def parse_created_utc(value: Any) -> Optional[int]:
    if not value or not str(value).strip():
        return None
    text = str(value).strip()
    try:
        parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None
    return int(parsed.timestamp())


def build_seed_plan(
    *,
    count: int,
    inclusion: str,
    client: BeaconClient,
    delay_rounds: int = 1,
) -> dict:
    """Build a ``seed_plan`` dict from a live (or fixed) beacon client."""
    if count < 1:
        raise ValueError(f"--seed-count must be >= 1, got {count}")
    if delay_rounds < 1:
        raise ValueError(f"--delay-rounds must be >= 1, got {delay_rounds}")
    inclusion = validate_inclusion(inclusion)
    info = client.chain_info()
    latest = client.latest()
    return {
        "beacon": "drand",
        "chain_hash": info.chain_hash,
        "genesis_time": info.genesis_time,
        "period": info.period,
        "count": int(count),
        "min_round": int(latest.round) + int(delay_rounds),
        "delay_rounds": int(delay_rounds),
        "derivation": DERIVATION_SHA256_V1,
        "inclusion": str(inclusion).strip(),
    }


def randomness_hex(randomness: bytes) -> str:
    return bytes(randomness).hex()


def parse_randomness(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    text = str(value).strip()
    try:
        raw = bytes.fromhex(text)
    except ValueError as exc:
        raise BeaconError(f"invalid beacon randomness hex: {value!r}") from exc
    if len(raw) != 32:
        raise BeaconError(
            f"beacon randomness must be 32 bytes, got {len(raw)}"
        )
    return raw


def seeds_record(
    seed_plan: Mapping[str, Any],
    fetched: BeaconRound,
    seeds: Sequence[int],
) -> dict:
    return {
        "chain_hash": fetched.chain_hash,
        "round": fetched.round,
        "randomness": randomness_hex(fetched.randomness),
        "seeds": [{"index": i, "seed": int(seed)} for i, seed in enumerate(seeds)],
    }


def write_seeds(record: Mapping[str, Any], dest: Path) -> Path:
    dest = Path(dest)
    dest.write_text(json.dumps(dict(record), indent=2) + "\n", encoding="utf-8")
    return dest


def load_seeds(path: Path) -> dict:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BeaconError(f"{path}: expected a JSON object")
    return data


def fetch_plan_round(
    seed_plan: Mapping[str, Any],
    client: BeaconClient,
    *,
    wait: bool = False,
    timeout: float = 60.0,
    interval: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> BeaconRound:
    """Fetch exactly ``seed_plan.min_round`` (the v1 anti-shopping rule)."""
    min_round = int(seed_plan["min_round"])
    if wait:
        return wait_for_round(
            client, min_round, timeout=timeout, interval=interval, sleep=sleep
        )
    return client.get_round(min_round)


def wait_for_round(
    client: BeaconClient,
    round_id: int,
    *,
    timeout: float = 60.0,
    interval: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> BeaconRound:
    deadline = time.monotonic() + timeout
    while True:
        try:
            return client.get_round(round_id)
        except BeaconRoundUnavailable:
            if time.monotonic() >= deadline:
                raise BeaconError(
                    f"beacon round {round_id} not available within {timeout}s"
                )
            sleep(interval)


def bind_run_seed(
    *,
    config: Optional[Mapping[str, Any]],
    seed_plan: Mapping[str, Any],
    seed_index: int,
    client: Optional[BeaconClient] = None,
    seeds_path: Optional[Path] = None,
) -> tuple[dict, dict]:
    """Fill ``config.seed`` and a manifest ``beacon`` block for one index."""
    count = int(seed_plan["count"])
    if seed_index < 0 or seed_index >= count:
        raise BeaconError(
            f"seed_index {seed_index} is outside 0..{count - 1}"
        )
    fetched, randomness = _resolve_plan_randomness(
        seed_plan, client=client, seeds_path=seeds_path
    )
    if fetched.round != int(seed_plan["min_round"]):
        raise BeaconError(
            f"beacon round {fetched.round} does not equal min_round "
            f"{seed_plan['min_round']}"
        )
    derived = derive_seed_single(seed_plan, config, randomness, seed_index, round=fetched.round)
    bound = dict(config or {})
    present_aliases = [k for k in SEED_KEYS if k in bound]
    if len(present_aliases) > 1:
        raise BeaconError(
            f"config contains multiple seed aliases {present_aliases!r}; "
            "only one seed key is permitted"
        )
    seed_key = present_aliases[0] if present_aliases else None
    existing = bound[seed_key] if seed_key else None
    if existing is not None:
        try:
            existing_int = int(existing)
        except (TypeError, ValueError):
            raise BeaconError(
                f"config seed {existing!r} is not an integer"
            )
        if existing_int != int(derived):
            raise BeaconError(
                f"config seed {existing!r} does not match derived seed {derived} "
                f"at index {seed_index}"
            )
    bound[seed_key or "seed"] = derived
    beacon = {
        "chain_hash": seed_plan["chain_hash"],
        "round": fetched.round,
        "randomness": randomness_hex(randomness),
        "seed_index": seed_index,
        "seed_count": count,
        "derived_seed": derived,
    }
    return bound, beacon


def _resolve_plan_randomness(
    seed_plan: Mapping[str, Any],
    *,
    client: Optional[BeaconClient],
    seeds_path: Optional[Path],
) -> tuple[BeaconRound, bytes]:
    min_round = int(seed_plan["min_round"])
    if seeds_path is not None and Path(seeds_path).is_file():
        record = load_seeds(seeds_path)
        if str(record.get("chain_hash")) != str(seed_plan["chain_hash"]):
            raise BeaconError(
                f"{seeds_path}: chain_hash {record.get('chain_hash')!r} "
                f"does not match seed_plan {seed_plan['chain_hash']!r}"
            )
        if int(record.get("round", -1)) != min_round:
            raise BeaconError(
                f"{seeds_path}: round {record.get('round')!r} does not equal "
                f"min_round {min_round}"
            )
        randomness = parse_randomness(record.get("randomness"))
        return (
            BeaconRound(
                chain_hash=str(seed_plan["chain_hash"]),
                round=min_round,
                randomness=randomness,
                unix_time=round_unix_time(
                    int(seed_plan["genesis_time"]),
                    int(seed_plan["period"]),
                    min_round,
                ),
                genesis_time=int(seed_plan["genesis_time"]),
                period=int(seed_plan["period"]),
            ),
            randomness,
        )
    if client is None:
        raise BeaconError("beacon client or seeds.json is required to bind a seed")
    fetched = client.get_round(min_round)
    return fetched, fetched.randomness


@dataclass
class BeaconCheck:
    """Outcome of the L2 beacon binding check.

    ``problems`` fail verify when an attempted authenticity/recompute check
    disagreed. ``gaps`` block L2 (missing is not failure). ``notes`` are
    reader-visible subset/gap lines.
    """

    problems: List[str] = field(default_factory=list)
    gaps: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def earned(self) -> bool:
        return not self.problems and not self.gaps


def _gap(check: BeaconCheck, message: str) -> None:
    if message not in check.gaps:
        check.gaps.append(message)


def _problem(check: BeaconCheck, message: str) -> None:
    if message not in check.problems:
        check.problems.append(message)
    _gap(check, message)


def _read_precommit_json(run_dir: Path) -> Optional[dict]:
    path = Path(run_dir) / "precommit.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def verify_beacon_binding(
    manifest: Any,
    run_dir: Path,
    *,
    client: Optional[BeaconClient] = None,
    precommit: Optional[Mapping[str, Any]] = None,
    proof_ok: Optional[bool] = None,
    precommit_bound: Optional[bool] = None,
) -> BeaconCheck:
    """Recompute the seed binding and check plan proof / round ordering."""
    check = BeaconCheck()
    run_dir = Path(run_dir)
    pc = dict(precommit) if precommit is not None else _read_precommit_json(run_dir)
    seed_plan = (pc or {}).get("seed_plan") if pc else None
    beacon = getattr(manifest, "beacon", None)
    if not seed_plan or pc is None:
        _gap(check, "missing: seed_plan")
        return check
    if precommit_bound is False:
        _gap(check, "precommit not bound")
    elif precommit_bound is None:
        recorded = getattr(manifest, "precommit_hash", None)
        if recorded != hash_json(pc):
            _gap(check, "precommit not bound")
    proof_path = run_dir / "precommit.ots"
    if not proof_path.is_file():
        _gap(check, "missing: precommit proof")
    elif proof_ok is False:
        _gap(check, "precommit proof failed")
    elif proof_ok is not True:
        try:
            for problem in verify_proof(proof_path.read_bytes(), hash_json(pc)):
                _problem(check, f"precommit proof: {problem}")
        except OtsError as exc:
            _problem(check, f"precommit proof: {exc}")
    if not isinstance(beacon, Mapping) or not beacon:
        _gap(check, "missing: beacon binding")
        return check

    try:
        min_round = int(seed_plan["min_round"])
        used_round = int(beacon["round"])
        seed_index = int(beacon["seed_index"])
        count = int(seed_plan["count"])
        recorded_seed = int(beacon["derived_seed"])
        randomness = parse_randomness(beacon.get("randomness"))
    except (KeyError, TypeError, ValueError, BeaconError) as exc:
        _problem(check, f"invalid beacon record: {exc}")
        return check

    if used_round != min_round:
        _problem(
            check,
            f"beacon round {used_round} does not equal min_round {min_round}",
        )
    if str(beacon.get("chain_hash")) != str(seed_plan.get("chain_hash")):
        _problem(
            check,
            f"beacon chain_hash {beacon.get('chain_hash')!r} does not match "
            f"seed_plan {seed_plan.get('chain_hash')!r}",
        )
    if seed_index < 0 or seed_index >= count:
        _problem(check, f"seed_index {seed_index} is outside 0..{count - 1}")
        return check

    try:
        expected = derive_seed_single(
            seed_plan, getattr(manifest, "config", None), randomness, seed_index,
            round=used_round,
        )
    except BeaconError as exc:
        _problem(check, str(exc))
        return check

    if recorded_seed != expected:
        _problem(
            check,
            f"derived_seed {recorded_seed} does not recompute to {expected}",
        )
    config = getattr(manifest, "config", None) or {}
    for alias in SEED_KEYS:
        if alias not in config:
            continue
        try:
            config_seed_val = int(config[alias])
        except (TypeError, ValueError):
            _problem(
                check,
                f"manifest seed key {alias!r} is not an integer: {config[alias]!r}",
            )
            continue
        if config_seed_val != expected:
            _problem(
                check,
                f"manifest seed {alias}={config[alias]!r} does not match derived seed {expected}",
            )
    if not any(alias in config for alias in SEED_KEYS):
        _problem(
            check,
            f"manifest config has no seed key; derived seed {expected} is not bound",
        )

    if client is not None:
        try:
            fetched = client.get_round(used_round)
        except BeaconError:
            _gap(check, "unauthenticated randomness")
        else:
            if fetched.randomness != randomness:
                _problem(
                    check,
                    "beacon randomness does not match the authenticated round",
                )
            if fetched.chain_hash != str(seed_plan.get("chain_hash")):
                _problem(
                    check,
                    f"authenticated chain_hash {fetched.chain_hash!r} does not "
                    f"match seed_plan {seed_plan.get('chain_hash')!r}",
                )
    else:
        _gap(check, "unauthenticated randomness")

    declared = parse_created_utc(pc.get("created_utc"))
    try:
        round_time = round_unix_time(
            int(seed_plan["genesis_time"]),
            int(seed_plan["period"]),
            used_round,
        )
    except (KeyError, TypeError, ValueError, BeaconError):
        round_time = None
    if declared is None:
        _gap(check, "missing: plan created_utc")
    elif round_time is not None and declared > round_time:
        _gap(check, "plan created after the beacon round")

    if count > 1:
        check.notes.append(
            subset_note(count, [seed_index], single_record=True)
        )
    return check


def subset_note(
    count: int, published: Sequence[int], *, single_record: bool = False
) -> str:
    unique = sorted({int(i) for i in published})
    missing = [i for i in range(count) if i not in unique]
    published_bit = ", ".join(str(i) for i in unique) if unique else "none"
    if missing:
        missing_bit = ", ".join(str(i) for i in missing)
        extra = "; missing members not on this record" if single_record else f"; missing: {missing_bit}"
        if single_record:
            return (
                f"published {len(unique)} of {count} committed seeds "
                f"(indices: {published_bit}){extra}"
            )
        return (
            f"published {len(unique)} of {count} committed seeds "
            f"(indices: {published_bit}); missing: {missing_bit}"
        )
    return f"published {len(unique)} of {count} committed seeds (indices: {published_bit})"


class FixedBeacon:
    """In-memory beacon for tests and ``--beacon-fixture``. Never networks."""

    def __init__(
        self,
        *,
        chain_hash: str,
        genesis_time: int,
        period: int,
        rounds: Mapping[int, bytes],
        latest_round: Optional[int] = None,
    ) -> None:
        self._chain = BeaconChain(
            chain_hash=chain_hash, genesis_time=genesis_time, period=period
        )
        self._rounds: Dict[int, bytes] = {int(k): bytes(v) for k, v in rounds.items()}
        if latest_round is None:
            latest_round = max(self._rounds) if self._rounds else 0
        self._latest = int(latest_round)

    def chain_info(self) -> BeaconChain:
        return self._chain

    def latest(self) -> BeaconRound:
        if self._latest < 1:
            raise BeaconError("fixed beacon has no latest round")
        return self.get_round(self._latest)

    def get_round(self, round_id: int) -> BeaconRound:
        round_id = int(round_id)
        if round_id not in self._rounds:
            raise BeaconRoundUnavailable(f"fixed beacon has no round {round_id}")
        randomness = self._rounds[round_id]
        return BeaconRound(
            chain_hash=self._chain.chain_hash,
            round=round_id,
            randomness=randomness,
            unix_time=round_unix_time(
                self._chain.genesis_time, self._chain.period, round_id
            ),
            genesis_time=self._chain.genesis_time,
            period=self._chain.period,
        )


def load_fixed_beacon(path: Path) -> FixedBeacon:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BeaconError(f"{path}: expected a JSON object")
    rounds_raw = data.get("rounds") or {}
    if not isinstance(rounds_raw, Mapping):
        raise BeaconError(f"{path}: rounds must be an object")
    rounds = {int(k): parse_randomness(v) for k, v in rounds_raw.items()}
    return FixedBeacon(
        chain_hash=str(data["chain_hash"]),
        genesis_time=int(data["genesis_time"]),
        period=int(data["period"]),
        rounds=rounds,
        latest_round=data.get("latest"),
    )


def write_fixed_beacon_fixture(
    dest: Path,
    *,
    chain_hash: str = "test-chain",
    genesis_time: int = 1_700_000_000,
    period: int = 3,
    latest: int = 50,
    rounds: Optional[Mapping[int, bytes]] = None,
) -> Path:
    """Write a ``FixedBeacon`` fixture JSON (for tests and CLI)."""
    if rounds is None:
        rounds = {latest: bytes.fromhex("aa" * 32), latest + 1: bytes.fromhex("bb" * 32)}
    payload = {
        "chain_hash": chain_hash,
        "genesis_time": genesis_time,
        "period": period,
        "latest": latest,
        "rounds": {str(k): randomness_hex(v) for k, v in rounds.items()},
    }
    dest = Path(dest)
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return dest


class DrandHttpClient:
    """stdlib HTTP client for the public drand REST API."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_DRAND_URL,
        chain_hash: str = DRAND_QUICKNET_HASH,
        opener: Callable[..., Any] = urllib.request.urlopen,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.chain_hash = chain_hash
        self._opener = opener
        self.timeout = timeout
        self._info: Optional[BeaconChain] = None

    def _get(self, path: str) -> dict:
        url = f"{self.base_url}/{self.chain_hash}/{path.lstrip('/')}"
        try:
            with self._opener(url, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise BeaconRoundUnavailable(
                    f"drand round not found: {url}"
                ) from exc
            raise BeaconError(f"drand fetch failed: {url}: {exc}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BeaconError(f"drand fetch failed: {url}: {exc}") from exc
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BeaconError(f"drand response is not JSON: {url}") from exc
        if not isinstance(data, dict):
            raise BeaconError(f"drand response is not an object: {url}")
        return data

    def chain_info(self) -> BeaconChain:
        if self._info is None:
            data = self._get("info")
            try:
                chain_hash = str(data.get("hash") or self.chain_hash)
                self._info = BeaconChain(
                    chain_hash=chain_hash,
                    genesis_time=int(data["genesis_time"]),
                    period=int(data["period"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise BeaconError(f"drand info missing fields: {exc}") from exc
        return self._info

    def latest(self) -> BeaconRound:
        return self._round_from_payload(self._get("public/latest"))

    def get_round(self, round_id: int) -> BeaconRound:
        return self._round_from_payload(self._get(f"public/{int(round_id)}"))

    def _round_from_payload(self, data: Mapping[str, Any]) -> BeaconRound:
        info = self.chain_info()
        try:
            round_id = int(data["round"])
            randomness = parse_randomness(data["randomness"])
        except (KeyError, TypeError, ValueError, BeaconError) as exc:
            raise BeaconError(f"drand round payload invalid: {exc}") from exc
        return BeaconRound(
            chain_hash=info.chain_hash,
            round=round_id,
            randomness=randomness,
            unix_time=round_unix_time(info.genesis_time, info.period, round_id),
            genesis_time=info.genesis_time,
            period=info.period,
        )


def _http_chain_hash(chain_hash: Optional[str]) -> str:
    text = str(chain_hash or DRAND_QUICKNET_HASH).strip().lower()
    if len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
        raise BeaconError(
            f"invalid drand chain_hash {chain_hash!r}; expected 64 hex characters"
        )
    return text


def resolve_beacon_client(
    *,
    url: Optional[str] = None,
    fixture: Optional[Path] = None,
    chain_hash: Optional[str] = None,
    live: bool = False,
) -> Optional[BeaconClient]:
    """Fixture client, live HTTP drand, or ``None`` (offline)."""
    path = fixture
    if path is None:
        env = os.environ.get(BEACON_FIXTURE_ENV)
        if env:
            path = Path(env)
    if path is not None:
        return load_fixed_beacon(Path(path))
    if not live and not url:
        return None
    return DrandHttpClient(
        base_url=url or DEFAULT_DRAND_URL,
        chain_hash=_http_chain_hash(chain_hash),
    )
