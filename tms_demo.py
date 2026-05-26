#!/usr/bin/env python3
"""
tms_demo.py
===========
End-to-end demonstration of the Trust Management System persistence layer
against a live OptimusDB agent, using optimusPy's OptimusDBClient.

Subcommands
-----------
  health     Check the OptimusDB agent is reachable and print a status summary.
  persist    Load a sample JSON file and write trust evidence into the store.
  retrieve   Read trust records back and print scores + rankings.
  wipe       Delete every trust record in the store (test cleanup).

Examples
--------
  python tms_demo.py health
  python tms_demo.py persist  --file sample_trust_data.json
  python tms_demo.py retrieve --context storage
  python tms_demo.py wipe     --context storage
"""

import argparse
import json
import sys

from optimusdb_client import OptimusDBClient
from trust_store import TrustStore, TRUST_STORE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_clients(args):
    client = OptimusDBClient(base_url=args.url,
                             context=args.api_context,
                             log_level=args.log_level)
    ts = TrustStore(client, store=args.store, log_level=args.log_level)
    return client, ts


def load_sample(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_health(args):
    client, _ = build_clients(args)
    if not client.health_check():
        print("✗ Agent is NOT healthy / not reachable.")
        return 1
    s = client.get_agent_status()
    agent = s.get("agent", {})
    cluster = s.get("cluster", {})
    print("✓ Agent reachable")
    print(f"  peer_id        : {agent.get('peer_id')}")
    print(f"  role           : {agent.get('role')}  "
          f"(coordinator={agent.get('is_coordinator')}, leader={agent.get('is_current_leader')})")
    print(f"  health score   : {agent.get('health', {}).get('score')}  "
          f"({agent.get('health', {}).get('status')})")
    print(f"  cluster peers  : total={cluster.get('total_peers')} "
          f"coordinators={cluster.get('coordinators')} followers={cluster.get('followers')}")
    return 0


def cmd_persist(args):
    client, ts = build_clients(args)
    if not client.health_check():
        print("✗ Agent not reachable — aborting persist.")
        return 1

    data = load_sample(args.file)
    context = data.get("context", "default")

    seeds = data.get("seed_scores", [])
    print(f"\nSeeding {len(seeds)} initial score(s) in context '{context}'...")
    for s in seeds:
        rec = ts.set_trust(subject_id=s["subject_id"],
                           score=float(s["score"]),
                           context=context,
                           subject_type=s.get("subject_type", "peer"))
        print(f"  seed  {s['subject_id'][:16]}…  score={rec.score:.3f}")

    interactions = data.get("interactions", [])
    print(f"\nReplaying {len(interactions)} interaction(s)...")
    for i in interactions:
        rec = ts.record_interaction(subject_id=i["subject_id"],
                                    success=bool(i["success"]),
                                    context=context,
                                    subject_type=i.get("subject_type", "peer"),
                                    source_peer=i.get("source_peer"))
        outcome = "✓" if i["success"] else "✗"
        print(f"  {outcome}  {i['subject_id'][:16]}…  -> score={rec.score:.3f} (n={rec.interaction_count})")

    print("\nPersist complete.")
    return 0


def cmd_retrieve(args):
    client, ts = build_clients(args)
    context = args.context

    records = ts.list_all(context=context)
    if not records:
        print(f"No trust records found in context '{context}'. Run `persist` first.")
        return 0

    print(f"\nAll trust records in context '{context}':")
    print(f"  {'subject_id':<50} {'score':>6} {'conf':>6} {'n':>4} {'last':>8}")
    for r in sorted(records, key=lambda x: x.score, reverse=True):
        print(f"  {r.subject_id:<50} {r.score:>6.3f} {r.confidence:>6.3f} "
              f"{r.interaction_count:>4} {str(r.last_outcome):>8}")

    print(f"\nMost trusted (election candidates):")
    for r in ts.most_trusted(context=context, top=3):
        print(f"  {r.subject_id[:24]}…  {r.score:.3f}")

    thr = args.threshold
    print(f"\nTrustworthy (score >= {thr}):")
    tw = ts.trustworthy(threshold=thr, context=context)
    if not tw:
        print("  (none)")
    for r in tw:
        print(f"  {r.subject_id[:24]}…  {r.score:.3f}")
    return 0


def cmd_wipe(args):
    _, ts = build_clients(args)
    records = ts.list_all(context=args.context)
    for r in records:
        ts.forget(r.subject_id, context=r.context)
    print(f"Deleted {len(records)} record(s) from context '{args.context}'.")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="Trust Management System demo for OptimusDB")
    p.add_argument("--url", default="http://193.225.250.240/optimusdb1",
                   help="OptimusDB base URL")
    p.add_argument("--api-context", default="swarmkb",
                   help="OptimusDB API context path (the {context} segment)")
    p.add_argument("--store", default=TRUST_STORE,
                   help="dstype / datastore name for trust records")
    p.add_argument("--log-level", default="WARNING",
                   help="DEBUG, INFO, WARNING, ERROR")

    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("health", help="check agent reachability")
    sp.set_defaults(func=cmd_health)

    sp = sub.add_parser("persist", help="write sample trust data")
    sp.add_argument("--file", default="sample_trust_data.json")
    sp.set_defaults(func=cmd_persist)

    sp = sub.add_parser("retrieve", help="read trust records back")
    sp.add_argument("--context", default="storage")
    sp.add_argument("--threshold", type=float, default=0.7)
    sp.set_defaults(func=cmd_retrieve)

    sp = sub.add_parser("wipe", help="delete all trust records in a context")
    sp.add_argument("--context", default="storage")
    sp.set_defaults(func=cmd_wipe)

    args = p.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
