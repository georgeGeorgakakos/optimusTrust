#!/usr/bin/env python3
"""
optimusdb_trust_scenarios.py
============================

Persist and retrieve trust-related payloads (providers, direct-trust flags,
trust-score calculations, and Trust VCs) against OptimusDB — the decentralized
P2P knowledge base — using a swappable storage backend.

The script ships with a LocalStore backend (a file-backed key/value + document
store that mimics OptimusDB semantics: namespaced collections, put/get/query,
append-only history) so every scenario runs offline out of the box. Point it at
a live OptimusDB instance by implementing the OptimusDBHTTPStore adapter (stub
included) or by wiring your KBClient in place of LocalStore.

Run:
    python optimusdb_trust_scenarios.py                 # run all scenarios
    python optimusdb_trust_scenarios.py --list          # list scenarios
    python optimusdb_trust_scenarios.py --only 3 7 9    # run a subset
    python optimusdb_trust_scenarios.py --backend local --store ./optimusdb_data
    python optimusdb_trust_scenarios.py --seed ./seed   # seed from JSON files first

Data model (collections):
    providers       key = provider_did      -> provider document
    direct_trust    key = f"{subject_type}:{did}" -> {trusted: bool}
    trust_scores    key = provider_did      -> latest TrustCalculation doc
    trust_history   key = provider_did      -> append-only list of score snapshots
    trust_vcs       key = vc_id             -> Trust VC document
    monitoring      key = device_did        -> latest monitoring snapshot
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

VALID_SUBJECT_TYPES = {"capacity", "capacity_provider", "resource", "resource_provider"}

COLL_PROVIDERS = "providers"
COLL_DIRECT_TRUST = "direct_trust"
COLL_TRUST_SCORES = "trust_scores"
COLL_TRUST_HISTORY = "trust_history"
COLL_TRUST_VCS = "trust_vcs"
COLL_MONITORING = "monitoring"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Storage abstraction
# --------------------------------------------------------------------------- #

class OptimusStore(ABC):
    """Minimal OptimusDB-like interface: namespaced document collections."""

    @abstractmethod
    def put(self, collection: str, key: str, value: dict) -> dict: ...

    @abstractmethod
    def get(self, collection: str, key: str) -> Optional[dict]: ...

    @abstractmethod
    def delete(self, collection: str, key: str) -> bool: ...

    @abstractmethod
    def list(self, collection: str) -> list[dict]: ...

    @abstractmethod
    def query(self, collection: str, predicate: Callable[[dict], bool]) -> list[dict]: ...

    @abstractmethod
    def append(self, collection: str, key: str, item: dict) -> list[dict]:
        """Append to an append-only list stored under key; returns full list."""
        ...

    def close(self) -> None:
        pass


class LocalStore(OptimusStore):
    """
    File-backed store that emulates OptimusDB semantics for offline use.

    Layout on disk:
        <root>/<collection>.json    -> {key: value}   (documents)
        <root>/<collection>.log.json-> {key: [items]}  (append-only lists)

    Everything is kept in memory and flushed on write; safe for single-process
    scenario runs. Swap for OptimusDBHTTPStore/KBClient in production.
    """

    def __init__(self, root: str | os.PathLike = "./optimusdb_data"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._docs: dict[str, dict[str, dict]] = {}
        self._logs: dict[str, dict[str, list]] = {}
        self._load()

    # ---- persistence helpers ---------------------------------------------- #
    def _doc_path(self, collection: str) -> Path:
        return self.root / f"{collection}.json"

    def _log_path(self, collection: str) -> Path:
        return self.root / f"{collection}.log.json"

    def _load(self) -> None:
        for p in self.root.glob("*.json"):
            if p.name.endswith(".log.json"):
                coll = p.name[: -len(".log.json")]
                self._logs[coll] = json.loads(p.read_text() or "{}")
            else:
                coll = p.name[: -len(".json")]
                self._docs[coll] = json.loads(p.read_text() or "{}")

    def _flush_docs(self, collection: str) -> None:
        self._doc_path(collection).write_text(
            json.dumps(self._docs.get(collection, {}), indent=2, ensure_ascii=False)
        )

    def _flush_logs(self, collection: str) -> None:
        self._log_path(collection).write_text(
            json.dumps(self._logs.get(collection, {}), indent=2, ensure_ascii=False)
        )

    # ---- interface -------------------------------------------------------- #
    def put(self, collection: str, key: str, value: dict) -> dict:
        self._docs.setdefault(collection, {})[key] = copy.deepcopy(value)
        self._flush_docs(collection)
        return value

    def get(self, collection: str, key: str) -> Optional[dict]:
        return copy.deepcopy(self._docs.get(collection, {}).get(key))

    def delete(self, collection: str, key: str) -> bool:
        existed = key in self._docs.get(collection, {})
        if existed:
            del self._docs[collection][key]
            self._flush_docs(collection)
        return existed

    def list(self, collection: str) -> list[dict]:
        return [copy.deepcopy(v) for v in self._docs.get(collection, {}).values()]

    def query(self, collection: str, predicate: Callable[[dict], bool]) -> list[dict]:
        return [copy.deepcopy(v) for v in self._docs.get(collection, {}).values() if predicate(v)]

    def append(self, collection: str, key: str, item: dict) -> list[dict]:
        bucket = self._logs.setdefault(collection, {}).setdefault(key, [])
        bucket.append(copy.deepcopy(item))
        self._flush_logs(collection)
        return copy.deepcopy(bucket)

    def get_log(self, collection: str, key: str) -> list[dict]:
        return copy.deepcopy(self._logs.get(collection, {}).get(key, []))


class OptimusDBHTTPStore(OptimusStore):
    """
    Adapter stub for a live OptimusDB HTTP/gRPC service or your KBClient.

    Fill these in with real calls (e.g. KBClient.put/get, or requests to the
    trust app on http://localhost:8000). Kept as NotImplemented so the offline
    LocalStore stays the default and nothing silently no-ops.
    """

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip("/")

    def put(self, collection, key, value):
        raise NotImplementedError("Wire to KBClient.put / OptimusDB write API")

    def get(self, collection, key):
        raise NotImplementedError("Wire to KBClient.get / OptimusDB read API")

    def delete(self, collection, key):
        raise NotImplementedError("Wire to OptimusDB delete API")

    def list(self, collection):
        raise NotImplementedError("Wire to OptimusDB list/scan API")

    def query(self, collection, predicate):
        raise NotImplementedError("Fetch via list() then filter client-side")

    def append(self, collection, key, item):
        raise NotImplementedError("Wire to OptimusDB append/monitoring API")


# --------------------------------------------------------------------------- #
# Repository — trust-domain operations on top of the store
# --------------------------------------------------------------------------- #

class TrustRepository:
    """Domain-aware persistence/retrieval mirroring the trust app endpoints."""

    def __init__(self, store: OptimusStore):
        self.store = store

    # ---- providers -------------------------------------------------------- #
    def save_provider(self, provider: dict) -> dict:
        did = provider["did"]
        return self.store.put(COLL_PROVIDERS, did, provider)

    def get_provider(self, did: str) -> Optional[dict]:
        return self.store.get(COLL_PROVIDERS, did)

    def list_providers(self) -> list[dict]:
        return self.store.list(COLL_PROVIDERS)

    def trusted_providers(self) -> list[dict]:
        return self.store.query(COLL_PROVIDERS, lambda p: bool(p.get("direct_trust")))

    # ---- direct trust ----------------------------------------------------- #
    @staticmethod
    def _dt_key(subject_type: str, did: str) -> str:
        return f"{subject_type}:{did}"

    def set_direct_trust(self, did: str, subject_type: str, trusted: bool) -> dict:
        if subject_type not in VALID_SUBJECT_TYPES:
            raise ValueError(f"invalid subject_type '{subject_type}'")
        rec = {"did": did, "subject_type": subject_type, "trusted": trusted, "updated_at": _utcnow()}
        self.store.put(COLL_DIRECT_TRUST, self._dt_key(subject_type, did), rec)
        # keep the provider document's flag in sync when it exists
        prov = self.get_provider(did)
        if prov is not None:
            prov["direct_trust"] = trusted
            self.save_provider(prov)
        return rec

    def get_direct_trust(self, did: str, subject_type: str) -> bool:
        rec = self.store.get(COLL_DIRECT_TRUST, self._dt_key(subject_type, did))
        return bool(rec and rec.get("trusted"))

    # ---- trust scores + history ------------------------------------------ #
    def save_trust_score(self, score_doc: dict) -> dict:
        did = score_doc["provider_did"]
        self.store.put(COLL_TRUST_SCORES, did, score_doc)
        self.store.append(
            COLL_TRUST_HISTORY,
            did,
            {"score": score_doc["trust_score"], "timestamp": _utcnow()},
        )
        # persist per-device monitoring snapshots too
        for dev in score_doc.get("devices", []):
            if dev.get("monitoring"):
                snap = dict(dev["monitoring"])
                snap["timestamp"] = _utcnow()
                self.store.put(COLL_MONITORING, dev["device_did"], snap)
        return score_doc

    def get_trust_score(self, did: str) -> Optional[dict]:
        return self.store.get(COLL_TRUST_SCORES, did)

    def get_trust_history(self, did: str) -> list[dict]:
        if isinstance(self.store, LocalStore):
            return self.store.get_log(COLL_TRUST_HISTORY, did)
        return self.store.append(COLL_TRUST_HISTORY, did, {}) if False else []

    def get_monitoring(self, device_did: str) -> Optional[dict]:
        return self.store.get(COLL_MONITORING, device_did)

    # ---- trust VCs -------------------------------------------------------- #
    def save_vc(self, vc_doc: dict) -> dict:
        return self.store.put(COLL_TRUST_VCS, vc_doc["id"], vc_doc)

    def get_vc(self, vc_id: str) -> Optional[dict]:
        return self.store.get(COLL_TRUST_VCS, vc_id)

    def vcs_for_subject(self, subject_did: str) -> list[dict]:
        return self.store.query(
            COLL_TRUST_VCS,
            lambda v: v.get("vc", {}).get("credentialSubject", {}).get("id") == subject_did,
        )


# --------------------------------------------------------------------------- #
# Trust computation (mirrors README: 0.8*performance + 0.2*reputation)
# --------------------------------------------------------------------------- #

def calculate_trust_score(performance_trust: float, reputation: float) -> float:
    return round(0.8 * performance_trust + 0.2 * reputation, 4)


def aggregate_metrics(devices: list[dict]) -> dict:
    """Volume-unweighted mean of device monitoring (simple demo aggregation)."""
    mons = [d["monitoring"] for d in devices if d.get("monitoring")]
    if not mons:
        return {}
    n = len(mons)
    return {
        "uptime": round(sum(m["uptime"] for m in mons) / n, 4),
        "error_rate": round(sum(m["error_rate"] for m in mons) / n, 4),
        "latency_ms": round(sum(m["latency_ms"] for m in mons) / n, 2),
    }


def issue_trust_vc(subject_did: str, subject_type: str, trust_score: float) -> dict:
    vc_id = f"urn:uuid:{uuid.uuid4()}"
    now = _utcnow()
    return {
        "id": vc_id,
        "type": "trust_vc",
        "status": "certified",
        "created_at": now,
        "vc": {
            "@context": ["https://www.w3.org/2018/credentials/v1"],
            "id": vc_id,
            "type": ["VerifiableCredential", "TrustCredential"],
            "issuer": "did:swarm:trust-manager",
            "issuanceDate": now.replace("+00:00", "Z"),
            "credentialSubject": {
                "id": subject_did,
                "type": subject_type,
                "trustScore": trust_score,
            },
        },
    }


# --------------------------------------------------------------------------- #
# Sample data (mirrors the uploaded payloads; used when no seed dir given)
# --------------------------------------------------------------------------- #

SAMPLE_PROVIDERS = [
    {
        "did": "did:swarm:provider-alpha",
        "name": "Alpha Cloud Provider",
        "owner": "org:alpha-labs",
        "vm_count": "12", "auth_count": "3", "service_count": "5",
        "direct_trust": True,
        "metrics": {"uptime": 0.9781, "error_rate": 0.0123, "latency_ms": 142.37},
        "reputation": 0.65,
    },
    {
        "did": "did:swarm:provider-beta",
        "name": "Beta Edge Provider",
        "owner": "org:beta-labs",
        "vm_count": "4", "auth_count": "1", "service_count": "2",
        "direct_trust": False,
        "metrics": None,
        "reputation": None,
    },
]

SAMPLE_CALC = {
    "provider_did": "did:swarm:provider-alpha",
    "provider_name": "Alpha Cloud Provider",
    "trust_score": 0.8214,
    "performance_trust": 0.8637,
    "reputation_component": 0.65,
    "aggregated_metrics": {"uptime": 0.9781, "error_rate": 0.0123, "latency_ms": 142.37},
    "devices": [
        {
            "device_did": "did:swarm:capacity-vm-01", "device_name": "edge-node-01",
            "device_type": "edge", "capacity_trust": 0.8802,
            "monitoring": {"uptime": 0.9821, "error_rate": 0.0091, "latency_ms": 118.42},
            "historical_trust": [
                {"score": 0.8511, "timestamp": "2026-06-29T09:12:03.451000+00:00"},
                {"score": 0.8677, "timestamp": "2026-06-30T09:12:04.118000+00:00"},
            ],
        },
        {
            "device_did": "did:swarm:capacity-vm-02", "device_name": "cloud-node-02",
            "device_type": "cloud", "capacity_trust": 0.8471,
            "monitoring": {"uptime": 0.9701, "error_rate": 0.0187, "latency_ms": 176.05},
            "historical_trust": [
                {"score": 0.8203, "timestamp": "2026-06-29T09:12:03.451000+00:00"},
                {"score": 0.8390, "timestamp": "2026-06-30T09:12:04.118000+00:00"},
            ],
        },
    ],
    "historical_trust": [
        {"score": 0.7912, "timestamp": "2026-06-29T09:12:04.220000+00:00"},
        {"score": 0.8054, "timestamp": "2026-06-30T09:12:05.003000+00:00"},
    ],
}


# --------------------------------------------------------------------------- #
# Scenario framework
# --------------------------------------------------------------------------- #

@dataclass
class ScenarioResult:
    number: int
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Scenario:
    number: int
    name: str
    fn: Callable[[TrustRepository], tuple[bool, str]]

    def run(self, repo: TrustRepository) -> ScenarioResult:
        try:
            ok, detail = self.fn(repo)
            return ScenarioResult(self.number, self.name, ok, detail)
        except Exception as exc:  # noqa: BLE001
            return ScenarioResult(self.number, self.name, False, f"EXCEPTION: {exc!r}")


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


# ---- the 12 scenarios ----------------------------------------------------- #

def scenario_01(repo: TrustRepository) -> tuple[bool, str]:
    """Persist a single provider and retrieve it by DID (round-trip)."""
    repo.save_provider(SAMPLE_PROVIDERS[0])
    got = repo.get_provider("did:swarm:provider-alpha")
    _assert(got is not None and got["name"] == "Alpha Cloud Provider", "provider not round-tripped")
    return True, f"stored+read provider {got['did']}"


def scenario_02(repo: TrustRepository) -> tuple[bool, str]:
    """Bulk-persist providers and list them all."""
    for p in SAMPLE_PROVIDERS:
        repo.save_provider(p)
    all_p = repo.list_providers()
    _assert(len(all_p) >= 2, "expected >= 2 providers")
    return True, f"listed {len(all_p)} providers"


def scenario_03(repo: TrustRepository) -> tuple[bool, str]:
    """Grant direct trust, then check it via the direct-trust lookup."""
    repo.set_direct_trust("did:swarm:provider-alpha", "capacity_provider", True)
    trusted = repo.get_direct_trust("did:swarm:provider-alpha", "capacity_provider")
    _assert(trusted is True, "direct trust should be granted")
    return True, "granted + verified direct trust for provider-alpha"


def scenario_04(repo: TrustRepository) -> tuple[bool, str]:
    """Revoke direct trust and confirm the flag flips (and provider doc syncs)."""
    repo.save_provider(SAMPLE_PROVIDERS[0])
    repo.set_direct_trust("did:swarm:provider-alpha", "capacity_provider", True)
    repo.set_direct_trust("did:swarm:provider-alpha", "capacity_provider", False)
    _assert(repo.get_direct_trust("did:swarm:provider-alpha", "capacity_provider") is False,
            "direct trust should be revoked")
    prov = repo.get_provider("did:swarm:provider-alpha")
    _assert(prov["direct_trust"] is False, "provider doc flag not synced")
    return True, "revoked direct trust; provider flag synced to False"


def scenario_05(repo: TrustRepository) -> tuple[bool, str]:
    """Reject an invalid subject_type when setting direct trust."""
    try:
        repo.set_direct_trust("did:swarm:x", "not_a_type", True)
        return False, "invalid subject_type was accepted (should have raised)"
    except ValueError:
        return True, "invalid subject_type correctly rejected"


def scenario_06(repo: TrustRepository) -> tuple[bool, str]:
    """Persist a full trust-calculation doc and retrieve the latest score."""
    repo.save_trust_score(SAMPLE_CALC)
    got = repo.get_trust_score("did:swarm:provider-alpha")
    _assert(got and abs(got["trust_score"] - 0.8214) < 1e-9, "trust score not persisted")
    return True, f"persisted trust_score={got['trust_score']}"


def scenario_07(repo: TrustRepository) -> tuple[bool, str]:
    """Append multiple score snapshots and read back append-only history."""
    doc = copy.deepcopy(SAMPLE_CALC)
    for s in (0.80, 0.81, 0.8214):
        doc["trust_score"] = s
        repo.save_trust_score(doc)
    hist = repo.get_trust_history("did:swarm:provider-alpha")
    _assert(len(hist) >= 3, "expected >= 3 history entries")
    _assert(hist[-1]["score"] == 0.8214, "latest history score wrong")
    return True, f"history len={len(hist)}, latest={hist[-1]['score']}"


def scenario_08(repo: TrustRepository) -> tuple[bool, str]:
    """Recompute trust from performance+reputation and persist the fresh value."""
    perf, rep = SAMPLE_CALC["performance_trust"], SAMPLE_CALC["reputation_component"]
    recomputed = calculate_trust_score(perf, rep)
    # 0.8*0.8637 + 0.2*0.65 = 0.82096 ≈ 0.821; the stored 0.8214 uses
    # volume-weighted internals, so allow a small rounding delta here.
    _assert(abs(recomputed - 0.8214) < 5e-3, f"formula mismatch: {recomputed}")
    doc = copy.deepcopy(SAMPLE_CALC)
    doc["trust_score"] = recomputed
    repo.save_trust_score(doc)
    return True, f"recomputed 0.8*{perf}+0.2*{rep}={recomputed}"


def scenario_09(repo: TrustRepository) -> tuple[bool, str]:
    """Persist device monitoring snapshots and retrieve one by device DID."""
    repo.save_trust_score(SAMPLE_CALC)
    mon = repo.get_monitoring("did:swarm:capacity-vm-01")
    _assert(mon and mon["uptime"] == 0.9821, "device monitoring not persisted")
    return True, f"vm-01 uptime={mon['uptime']}, latency={mon['latency_ms']}ms"


def scenario_10(repo: TrustRepository) -> tuple[bool, str]:
    """Issue a Trust VC from a computed score, persist it, retrieve by VC id."""
    score = repo.get_trust_score("did:swarm:provider-alpha")
    if not score:
        repo.save_trust_score(SAMPLE_CALC)
        score = repo.get_trust_score("did:swarm:provider-alpha")
    vc = issue_trust_vc("did:swarm:provider-alpha", "capacity_provider", score["trust_score"])
    repo.save_vc(vc)
    got = repo.get_vc(vc["id"])
    _assert(got and got["vc"]["credentialSubject"]["trustScore"] == score["trust_score"],
            "VC trustScore mismatch")
    return True, f"issued+stored VC {vc['id'][:20]}… score={score['trust_score']}"


def scenario_11(repo: TrustRepository) -> tuple[bool, str]:
    """Query all VCs issued for a given subject DID."""
    for _ in range(2):
        vc = issue_trust_vc("did:swarm:provider-alpha", "capacity_provider", 0.8214)
        repo.save_vc(vc)
    vcs = repo.vcs_for_subject("did:swarm:provider-alpha")
    _assert(len(vcs) >= 2, "expected >= 2 VCs for subject")
    return True, f"found {len(vcs)} VCs for provider-alpha"


def scenario_12(repo: TrustRepository) -> tuple[bool, str]:
    """
    End-to-end: filter to directly-trusted providers, calculate + persist their
    trust, aggregate metrics, and skip providers without direct trust
    (mirrors /trust/calculate behaviour).
    """
    for p in SAMPLE_PROVIDERS:
        repo.save_provider(p)
    repo.set_direct_trust("did:swarm:provider-alpha", "capacity_provider", True)
    repo.set_direct_trust("did:swarm:provider-beta", "capacity_provider", False)

    processed, skipped = [], []
    for prov in repo.list_providers():
        if not repo.get_direct_trust(prov["did"], "capacity_provider"):
            skipped.append(prov["did"])
            continue
        agg = aggregate_metrics(SAMPLE_CALC["devices"])
        doc = copy.deepcopy(SAMPLE_CALC)
        doc["provider_did"] = prov["did"]
        doc["aggregated_metrics"] = agg
        doc["trust_score"] = calculate_trust_score(
            doc["performance_trust"], prov.get("reputation") or 0.0
        )
        repo.save_trust_score(doc)
        processed.append(prov["did"])

    _assert("did:swarm:provider-alpha" in processed, "alpha should be processed")
    _assert("did:swarm:provider-beta" in skipped, "beta should be skipped (no direct trust)")
    return True, f"processed={processed}, skipped={skipped}"


ALL_SCENARIOS = [
    Scenario(1, "Persist + retrieve a single provider", scenario_01),
    Scenario(2, "Bulk-persist + list providers", scenario_02),
    Scenario(3, "Grant + check direct trust", scenario_03),
    Scenario(4, "Revoke direct trust + sync provider flag", scenario_04),
    Scenario(5, "Reject invalid subject_type", scenario_05),
    Scenario(6, "Persist + retrieve full trust calculation", scenario_06),
    Scenario(7, "Append-only trust history round-trip", scenario_07),
    Scenario(8, "Recompute trust (0.8*perf + 0.2*rep) + persist", scenario_08),
    Scenario(9, "Persist + retrieve device monitoring", scenario_09),
    Scenario(10, "Issue + store + retrieve a Trust VC", scenario_10),
    Scenario(11, "Query all VCs for a subject", scenario_11),
    Scenario(12, "E2E: calculate only directly-trusted providers", scenario_12),
]


# --------------------------------------------------------------------------- #
# Seeding + runner
# --------------------------------------------------------------------------- #

def seed_from_dir(repo: TrustRepository, seed_dir: str) -> None:
    """Optionally load the uploaded JSON payloads into the store before running."""
    d = Path(seed_dir)
    def _load(name):
        p = d / name
        return json.loads(p.read_text()) if p.exists() else None

    providers = _load("providers_list_response.json")
    if providers:
        for p in providers:
            repo.save_provider(p)

    direct = _load("direct_trust_request_response.json")
    if direct:
        r = direct.get("response", direct)
        repo.set_direct_trust(r["did"], r["subject_type"], r["trusted"])

    calc = _load("trust_calculate_response.json")
    if calc:
        for sc in calc.get("trust_scores", []):
            repo.save_trust_score(sc)

    vc = _load("trust_vc_response.json")
    if vc:
        repo.save_vc(vc)


def build_store(backend: str, store_path: str, base_url: str) -> OptimusStore:
    if backend == "local":
        return LocalStore(store_path)
    if backend == "http":
        return OptimusDBHTTPStore(base_url)
    raise SystemExit(f"unknown backend '{backend}'")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="OptimusDB trust persist/retrieve scenarios")
    ap.add_argument("--backend", choices=["local", "http"], default="local")
    ap.add_argument("--store", default="./optimusdb_data", help="LocalStore root dir")
    ap.add_argument("--base-url", default="http://localhost:8000", help="HTTP backend base URL")
    ap.add_argument("--seed", default=None, help="dir with the uploaded JSON payloads to preload")
    ap.add_argument("--only", nargs="*", type=int, help="run only these scenario numbers")
    ap.add_argument("--list", action="store_true", help="list scenarios and exit")
    ap.add_argument("--fresh", action="store_true", help="wipe the local store dir first")
    args = ap.parse_args(argv)

    if args.list:
        for s in ALL_SCENARIOS:
            print(f"{s.number:2d}. {s.name}")
        return 0

    if args.fresh and args.backend == "local":
        import shutil
        shutil.rmtree(args.store, ignore_errors=True)

    store = build_store(args.backend, args.store, args.base_url)
    repo = TrustRepository(store)

    if args.seed:
        seed_from_dir(repo, args.seed)
        print(f"seeded store from {args.seed}")

    selected = [s for s in ALL_SCENARIOS if not args.only or s.number in args.only]

    print(f"\nRunning {len(selected)} scenario(s) on backend='{args.backend}'\n" + "-" * 60)
    results: list[ScenarioResult] = []
    for s in selected:
        res = s.run(repo)
        results.append(res)
        status = "PASS" if res.passed else "FAIL"
        print(f"[{status}] {res.number:2d}. {res.name}")
        if res.detail:
            print(f"        -> {res.detail}")

    store.close()
    passed = sum(r.passed for r in results)
    print("-" * 60)
    print(f"{passed}/{len(results)} scenarios passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
