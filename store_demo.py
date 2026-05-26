#!/usr/bin/env python3
"""
store_demo.py
=============
Generic JSON persistence + criteria-based retrieval against an OptimusDB
datastore, using optimusPy's OptimusDBClient. No scoring, no domain logic:
you give it a JSON file, it stores the document(s); you give it criteria,
it returns the matching documents.

Subcommands
-----------
  persist    Store the document(s) from a JSON file into a datastore.
  retrieve   Return documents matching field criteria (exact / operator / raw).
  delete     Delete documents matching criteria.
  health     Check the agent is reachable.

Criteria syntax (same convention as the optimusPy CLI)
------------------------------------------------------
  --where field:value              exact match     -> {"field": value}
  --where field:value:gt           operator        -> {"field": {"$gt": value}}
       operators: gt gte lt lte ne regex
  --raw  '<json>'                  raw criteria dict, merged in (for $or/$and/nested)

Multiple --where flags are AND-combined into a single criteria object.

Examples
--------
  python store_demo.py persist  --file sample_documents.json --store kbtrust
  python store_demo.py retrieve --store kbtrust --where context:storage
  python store_demo.py retrieve --store kbtrust --where trust_level:0.6:gte
  python store_demo.py retrieve --store kbtrust --where role:follower --where verified:true
  python store_demo.py retrieve --store kbtrust --raw '{"$or":[{"context":"storage"},{"context":"compute"}]}'
  python store_demo.py retrieve --store kbtrust            # no criteria -> all docs
  python store_demo.py delete   --store kbtrust --where _id:peer:QmVg
"""

import argparse
import json
import sys
import uuid

from optimusdb_client import OptimusDBClient

DEFAULT_STORE = "kbtrust"
_OPERATORS = {"gt", "gte", "lt", "lte", "ne", "regex"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_client(args):
    return OptimusDBClient(base_url=args.url,
                           context=args.api_context,
                           log_level=args.log_level)


def _coerce(value: str):
    """Turn a CLI string into int / float / bool / str as appropriate."""
    low = value.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            pass
    return value


def parse_where(where_items, raw):
    """
    Build the criteria list OptimusDB expects: a list with one criteria dict.
    Empty list means "match everything".
    """
    criteria = {}
    for item in (where_items or []):
        parts = item.split(":", 2)
        if len(parts) == 2:
            field, value = parts
            criteria[field] = _coerce(value)
        elif len(parts) == 3:
            field, value, op = parts
            if op not in _OPERATORS:
                raise ValueError(f"Unknown operator '{op}' in --where {item} "
                                 f"(use one of: {', '.join(sorted(_OPERATORS))})")
            criteria[field] = {f"${op}": _coerce(value)}
        else:
            raise ValueError(f"Invalid --where '{item}' (expected field:value[:operator])")

    if raw:
        criteria.update(json.loads(raw))

    return [criteria] if criteria else []


def load_documents(path):
    """Load a JSON file as a list of documents (single object -> 1-element list)."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("JSON must be an object or an array of objects.")
    # Ensure every document has an _id so it can be addressed / upserted.
    for doc in data:
        doc.setdefault("_id", uuid.uuid4().hex)
    return data


def print_documents(docs):
    if not docs:
        print("  (no documents matched)")
        return
    for d in docs:
        print(json.dumps(d, ensure_ascii=False, indent=2, sort_keys=True))
        print("  " + "-" * 60)


def extract_docs(result):
    data = result.get("data") if isinstance(result, dict) else None
    if data is None:
        return []
    if isinstance(data, dict):
        return [data]
    return data


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_health(args):
    client = build_client(args)
    if not client.health_check():
        print("✗ Agent not reachable.")
        return 1
    s = client.get_agent_status().get("agent", {})
    print(f"✓ Agent reachable — role={s.get('role')} peer_id={s.get('peer_id')}")
    return 0


def cmd_persist(args):
    client = build_client(args)
    docs = load_documents(args.file)
    print(f"Persisting {len(docs)} document(s) into store '{args.store}'...")
    client.create(documents=docs, dstype=args.store)
    for d in docs:
        print(f"  stored _id={d['_id']}")
    print("Done.")
    return 0


def cmd_retrieve(args):
    client = build_client(args)
    criteria = parse_where(args.where, args.raw)
    print(f"Querying store '{args.store}' with criteria: {json.dumps(criteria)}")
    result = client.get(criteria=criteria, dstype=args.store)
    docs = extract_docs(result)
    print(f"Retrieved {len(docs)} document(s):\n")
    print_documents(docs)
    return 0


def cmd_delete(args):
    client = build_client(args)
    criteria = parse_where(args.where, args.raw)
    if not criteria:
        print("Refusing to delete with empty criteria. Use --where to scope the delete.")
        return 1
    print(f"Deleting from store '{args.store}' where: {json.dumps(criteria)}")
    result = client.delete(criteria=criteria, dstype=args.store)
    print(f"Result: {json.dumps(result) if isinstance(result, dict) else result}")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    # Common flags live on a parent parser so they are accepted *after* the
    # subcommand, e.g.  `store_demo.py persist --file x.json --store kbtrust`.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--url", default="http://193.225.250.240/optimusdb1")
    common.add_argument("--api-context", default="swarmkb", help="OptimusDB API context path")
    common.add_argument("--store", default=DEFAULT_STORE, help="datastore (dstype)")
    common.add_argument("--log-level", default="WARNING")

    p = argparse.ArgumentParser(description="Persist / retrieve JSON in OptimusDB by criteria")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("health", parents=[common])
    sp.set_defaults(func=cmd_health)

    sp = sub.add_parser("persist", parents=[common])
    sp.add_argument("--file", required=True)
    sp.set_defaults(func=cmd_persist)

    sp = sub.add_parser("retrieve", parents=[common])
    sp.add_argument("--where", action="append", help="field:value[:operator] (repeatable)")
    sp.add_argument("--raw", help="raw criteria JSON, merged in (for $or/$and/nested)")
    sp.set_defaults(func=cmd_retrieve)

    sp = sub.add_parser("delete", parents=[common])
    sp.add_argument("--where", action="append")
    sp.add_argument("--raw")
    sp.set_defaults(func=cmd_delete)

    args = p.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
