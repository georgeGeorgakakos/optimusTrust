"""
trust_store.py
==============
Trust Management System (TMS) persistence layer for Swarmchestrate, built on
top of the optimusPy `OptimusDBClient` (https://github.com/georgeGeorgakakos/optimusPy).

It persists and retrieves trust/reputation records in a dedicated OptimusDB
datastore (default: `kbtrust`). Because OptimusDB materialises an OrbitDB
docstore lazily on first write, "creating a new data store" is simply a matter
of writing to a new `dstype`. Records are CRDT-replicated across peers like any
other OptimusDB collection.

Trust scoring uses the Beta Reputation model (Jøsang & Ismail):

        score = alpha / (alpha + beta)

where every positive interaction increments `alpha` and every negative one
increments `beta`. Uniform priors (alpha0 = beta0 = 1) give a 0.5 score for an
unknown subject. An optional exponential aging factor `lambda_decay` lets older
evidence fade, which is useful as input to reputation-based coordinator election.

Dependencies: requests, PyYAML, colorlog (same as optimusPy) + optimusdb_client.py
on the import path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from optimusdb_client import OptimusDBClient


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TRUST_STORE = "kbtrust"          # dedicated datastore (dstype) for trust records
RECORD_TYPE = "trust_record"     # discriminator stored on every document
DEFAULT_CONTEXT = "default"      # trust is context-scoped (e.g. "storage", "compute")

# Beta-distribution priors for an unknown subject -> score 0.5
PRIOR_ALPHA = 1.0
PRIOR_BETA = 1.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Record schema
# ---------------------------------------------------------------------------

@dataclass
class TrustRecord:
    """One trust record per (subject, context) pair."""
    subject_id: str
    subject_type: str = "peer"          # peer | agent | resource | template
    context: str = DEFAULT_CONTEXT
    alpha: float = PRIOR_ALPHA           # accumulated positive evidence (+ prior)
    beta: float = PRIOR_BETA             # accumulated negative evidence (+ prior)
    interaction_count: int = 0
    last_outcome: Optional[str] = None   # "success" | "failure"
    source_peer: Optional[str] = None    # peer that asserted the latest evidence
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)
    type: str = RECORD_TYPE

    @property
    def _id(self) -> str:
        # Deterministic id -> OrbitDB docstore replaces-by-id, giving upsert semantics
        return f"trust:{self.context}:{self.subject_id}"

    @property
    def score(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def confidence(self) -> float:
        """How much evidence backs the score (0..1). More evidence -> closer to 1."""
        n = self.alpha + self.beta - (PRIOR_ALPHA + PRIOR_BETA)
        return n / (n + 2.0)

    def to_document(self) -> Dict[str, Any]:
        doc = asdict(self)
        doc["_id"] = self._id
        doc["trust_score"] = round(self.score, 6)
        doc["confidence"] = round(self.confidence, 6)
        return doc

    @classmethod
    def from_document(cls, doc: Dict[str, Any]) -> "TrustRecord":
        return cls(
            subject_id=doc["subject_id"],
            subject_type=doc.get("subject_type", "peer"),
            context=doc.get("context", DEFAULT_CONTEXT),
            alpha=float(doc.get("alpha", PRIOR_ALPHA)),
            beta=float(doc.get("beta", PRIOR_BETA)),
            interaction_count=int(doc.get("interaction_count", 0)),
            last_outcome=doc.get("last_outcome"),
            source_peer=doc.get("source_peer"),
            created_at=doc.get("created_at", _utc_now()),
            updated_at=doc.get("updated_at", _utc_now()),
        )


# ---------------------------------------------------------------------------
# Trust store
# ---------------------------------------------------------------------------

class TrustStore:
    """
    Persistence + scoring facade for the Trust Management System.

    Example
    -------
    >>> from optimusdb_client import OptimusDBClient
    >>> ts = TrustStore(OptimusDBClient())          # uses default 193.225.250.240
    >>> ts.record_interaction("peer-7", success=True, context="storage")
    >>> ts.get_trust("peer-7", context="storage").score
    0.666...
    >>> ts.most_trusted(context="storage", top=3)   # coordinator-election input
    """

    def __init__(self,
                 client: Optional[OptimusDBClient] = None,
                 store: str = TRUST_STORE,
                 lambda_decay: float = 1.0,
                 log_level: str = "INFO"):
        """
        Args:
            client: an existing OptimusDBClient (created with defaults if None)
            store:  the dstype / datastore name to persist trust records into
            lambda_decay: aging factor applied to prior evidence before adding new
                          evidence (1.0 = no aging; 0.95 = mild forgetting)
        """
        self.client = client or OptimusDBClient(log_level=log_level)
        self.store = store
        self.lambda_decay = lambda_decay
        self.logger = logging.getLogger("TrustStore")
        self.logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # -- low-level persistence ---------------------------------------------

    def _upsert(self, record: TrustRecord) -> Dict[str, Any]:
        """Replace-by-id write. OrbitDB docstore overwrites the existing _id."""
        record.updated_at = _utc_now()
        doc = record.to_document()
        self.logger.info("Upserting %s (score=%.3f, n=%d)",
                         doc["_id"], doc["trust_score"], record.interaction_count)
        # create() issues crudput; with a fixed _id this behaves as an upsert.
        return self.client.create(documents=[doc], dstype=self.store)

    def _fetch(self, subject_id: str, context: str) -> Optional[TrustRecord]:
        criteria = [{"_id": f"trust:{context}:{subject_id}"}]
        res = self.client.get(criteria=criteria, dstype=self.store)
        data = res.get("data") or []
        if isinstance(data, dict):
            data = [data]
        if not data:
            return None
        return TrustRecord.from_document(data[0])

    # -- public API: PERSIST -----------------------------------------------

    def record_interaction(self,
                            subject_id: str,
                            success: bool,
                            context: str = DEFAULT_CONTEXT,
                            subject_type: str = "peer",
                            weight: float = 1.0,
                            source_peer: Optional[str] = None) -> TrustRecord:
        """
        Record one (or `weight`) positive/negative interaction(s) and persist
        the updated Beta parameters. Creates the record on first sighting.
        """
        record = self._fetch(subject_id, context)
        if record is None:
            record = TrustRecord(subject_id=subject_id,
                                 subject_type=subject_type,
                                 context=context)
        else:
            # Apply aging to existing evidence (keep priors intact).
            if self.lambda_decay != 1.0:
                record.alpha = PRIOR_ALPHA + self.lambda_decay * (record.alpha - PRIOR_ALPHA)
                record.beta = PRIOR_BETA + self.lambda_decay * (record.beta - PRIOR_BETA)

        if success:
            record.alpha += weight
            record.last_outcome = "success"
        else:
            record.beta += weight
            record.last_outcome = "failure"

        record.interaction_count += 1
        record.source_peer = source_peer
        self._upsert(record)
        return record

    def set_trust(self,
                  subject_id: str,
                  score: float,
                  context: str = DEFAULT_CONTEXT,
                  subject_type: str = "peer",
                  evidence_weight: float = 10.0) -> TrustRecord:
        """
        Seed/override a subject's trust with an explicit score in [0, 1]
        (e.g. an out-of-band reputation import). `evidence_weight` controls how
        much accumulated evidence the score is treated as carrying.
        """
        score = max(0.0, min(1.0, score))
        record = self._fetch(subject_id, context) or TrustRecord(
            subject_id=subject_id, subject_type=subject_type, context=context)
        record.alpha = PRIOR_ALPHA + score * evidence_weight
        record.beta = PRIOR_BETA + (1.0 - score) * evidence_weight
        self._upsert(record)
        return record

    # -- public API: RETRIEVE ----------------------------------------------

    def get_trust(self,
                  subject_id: str,
                  context: str = DEFAULT_CONTEXT) -> Optional[TrustRecord]:
        """Return the trust record for a subject, or None if never seen."""
        return self._fetch(subject_id, context)

    def get_score(self,
                  subject_id: str,
                  context: str = DEFAULT_CONTEXT,
                  default: float = 0.5) -> float:
        """Convenience: trust score in [0,1], `default` for unknown subjects."""
        rec = self._fetch(subject_id, context)
        return rec.score if rec else default

    def list_all(self, context: Optional[str] = None) -> List[TrustRecord]:
        """List every trust record, optionally filtered by context."""
        criteria: List[Dict[str, Any]] = [{"type": RECORD_TYPE}]
        if context is not None:
            criteria = [{"type": RECORD_TYPE, "context": context}]
        res = self.client.get(criteria=criteria, dstype=self.store)
        data = res.get("data") or []
        if isinstance(data, dict):
            data = [data]
        return [TrustRecord.from_document(d) for d in data if d]

    def trustworthy(self,
                    threshold: float = 0.7,
                    context: Optional[str] = None,
                    min_confidence: float = 0.0) -> List[TrustRecord]:
        """
        Return subjects whose score >= threshold (and confidence >= min_confidence).
        Filtering is done client-side so it works regardless of which query
        operators the agent supports on a custom store.
        """
        return sorted(
            (r for r in self.list_all(context)
             if r.score >= threshold and r.confidence >= min_confidence),
            key=lambda r: r.score, reverse=True,
        )

    def most_trusted(self,
                     context: Optional[str] = None,
                     top: int = 1,
                     min_confidence: float = 0.0) -> List[TrustRecord]:
        """Top-N subjects by score — direct input to coordinator election."""
        ranked = sorted(
            (r for r in self.list_all(context) if r.confidence >= min_confidence),
            key=lambda r: r.score, reverse=True,
        )
        return ranked[:top]

    # -- public API: MAINTENANCE -------------------------------------------

    def forget(self, subject_id: str, context: str = DEFAULT_CONTEXT) -> Dict[str, Any]:
        """Delete a subject's trust record."""
        criteria = [{"_id": f"trust:{context}:{subject_id}"}]
        return self.client.delete(criteria=criteria, dstype=self.store)


# ---------------------------------------------------------------------------
# Smoke test / demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="TrustStore smoke test against OptimusDB")
    parser.add_argument("--url", default="http://193.225.250.240/optimusdb1")
    parser.add_argument("--context-path", default="swarmkb", help="OptimusDB API context")
    parser.add_argument("--store", default=TRUST_STORE)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    client = OptimusDBClient(base_url=args.url,
                             context=args.context_path,
                             log_level=args.log_level)

    if not client.health_check():
        raise SystemExit("OptimusDB agent is not healthy — aborting.")

    ts = TrustStore(client, store=args.store, log_level=args.log_level)

    # Simulate a handful of interactions
    ts.record_interaction("peer-7", success=True, context="storage")
    ts.record_interaction("peer-7", success=True, context="storage")
    ts.record_interaction("peer-7", success=False, context="storage")
    ts.record_interaction("peer-3", success=True, context="storage")

    rec = ts.get_trust("peer-7", context="storage")
    print(f"\npeer-7  score={rec.score:.3f}  confidence={rec.confidence:.3f}  "
          f"(alpha={rec.alpha}, beta={rec.beta}, n={rec.interaction_count})")

    print("\nMost trusted in 'storage':")
    for r in ts.most_trusted(context="storage", top=5):
        print(f"  {r.subject_id:<10} {r.score:.3f}")
