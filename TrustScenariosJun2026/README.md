# OptimusDB Trust — Persist & Retrieve Scenarios

A self-contained Python script that persists and retrieves the trust-domain
payloads (`providers`, `direct_trust`, `trust_scores`, `trust_vcs`, `monitoring`)
against OptimusDB, with **12 runnable scenarios**, a swappable storage backend,
and a CI-friendly runner.

- **`optimusdb_trust_scenarios.py`** — the script (store abstraction, repository,
  trust math, scenarios, CLI runner).
- **`README.md`** — this file.

Requires only the Python 3.9+ standard library. No third-party packages.

---

## Table of contents

1. [Quick start](#quick-start)
2. [Architecture](#architecture)
3. [Collections & data model](#collections--data-model)
4. [CLI reference](#cli-reference)
5. [Scenario catalogue](#scenario-catalogue) ← *description + command + anticipated output for each*
6. [Full-run output](#full-run-output)
7. [What ends up on disk](#what-ends-up-on-disk)
8. [Wiring to a live OptimusDB](#wiring-to-a-live-optimusdb)
9. [Extending](#extending)

---

## Quick start

```bash
# run all 12 scenarios on the offline LocalStore backend
python optimusdb_trust_scenarios.py

# wipe the store dir and preload the real uploaded JSON payloads first
python optimusdb_trust_scenarios.py --fresh --seed /path/to/uploads

# list scenarios without running
python optimusdb_trust_scenarios.py --list

# run a subset
python optimusdb_trust_scenarios.py --only 3 7 12
```

Exit code is `0` when every selected scenario passes, `1` otherwise.

---

## Architecture

Three layers, so storage can be swapped without touching scenario logic:

```
Scenarios  ──▶  TrustRepository  ──▶  OptimusStore (abstract)
                                        ├── LocalStore        (file-backed, default, offline)
                                        └── OptimusDBHTTPStore (adapter stub for live OptimusDB / KBClient)
```

- **`OptimusStore`** — the interface: `put / get / delete / list / query / append`.
- **`LocalStore`** — emulates OptimusDB semantics (namespaced collections,
  documents + append-only logs) as JSON files under `--store` (default
  `./optimusdb_data`). Runs offline out of the box.
- **`OptimusDBHTTPStore`** — a stub you fill in to reach a live OptimusDB service
  (e.g. `http://localhost:8000`) or your `KBClient`. Left as `NotImplementedError`
  so nothing silently no-ops.
- **`TrustRepository`** — domain operations mirroring the trust app endpoints.

---

## Collections & data model

| Collection      | Key                         | Holds                                   |
|-----------------|-----------------------------|-----------------------------------------|
| `providers`     | `provider_did`              | provider document                       |
| `direct_trust`  | `{subject_type}:{did}`      | `{trusted: bool, updated_at}`           |
| `trust_scores`  | `provider_did`              | latest trust-calculation doc            |
| `trust_history` | `provider_did` (log)        | append-only `{score, timestamp}` list   |
| `trust_vcs`     | `vc_id`                     | Trust VC document                       |
| `monitoring`    | `device_did`                | latest monitoring snapshot              |

Valid `subject_type` values (from `SubjectType`): `capacity`, `capacity_provider`,
`resource`, `resource_provider`.

Trust formula (per the source README): `trust_score = 0.8 * performance_trust + 0.2 * reputation`.

---

## CLI reference

| Flag | Default | Meaning |
|------|---------|---------|
| `--backend {local,http}` | `local` | storage backend |
| `--store PATH` | `./optimusdb_data` | LocalStore root directory |
| `--base-url URL` | `http://localhost:8000` | HTTP backend base URL |
| `--seed DIR` | — | preload the uploaded JSON payloads before running |
| `--only N [N ...]` | — | run only these scenario numbers |
| `--list` | — | print scenarios and exit |
| `--fresh` | — | wipe the local store dir first (LocalStore only) |

> **Note on output determinism.** Trust VC ids are random UUIDs and history
> timestamps are wall-clock, so the id/timestamp fragments in the anticipated
> outputs below will differ per run. Everything else (scores, counts, PASS/FAIL,
> provider DIDs) is deterministic.

---

## Scenario catalogue

Each scenario below lists **what it does**, the **command** to run it in
isolation, and the **anticipated output**. Prefix any command with `--seed
/path/to/uploads` to run it against the real uploaded payloads instead of the
built-in sample data.

---

### 1 — Persist + retrieve a single provider

**Description.** Writes one provider document (`provider-alpha`) into the
`providers` collection, then reads it back by DID and asserts the round-trip
preserved its fields. This is the baseline `put`/`get` sanity check.

**Command.**
```bash
python optimusdb_trust_scenarios.py --fresh --only 1
```

**Anticipated output.**
```
Running 1 scenario(s) on backend='local'
------------------------------------------------------------
[PASS]  1. Persist + retrieve a single provider
        -> stored+read provider did:swarm:provider-alpha
------------------------------------------------------------
1/1 scenarios passed
```

---

### 2 — Bulk-persist + list providers

**Description.** Writes both sample providers (`alpha`, `beta`) and calls
`list_providers()` to retrieve every stored provider — exercises multi-write plus
the collection `list` operation that backs `GET /providers/`.

**Command.**
```bash
python optimusdb_trust_scenarios.py --fresh --only 2
```

**Anticipated output.**
```
[PASS]  2. Bulk-persist + list providers
        -> listed 2 providers
```

---

### 3 — Grant + check direct trust

**Description.** Grants direct trust for `provider-alpha` as a
`capacity_provider` (mirrors `POST /trust/direct`), then reads it back via the
direct-trust lookup (mirrors `GET /trust/direct/{subject_type}/{did}`) and
confirms the flag is `true`.

**Command.**
```bash
python optimusdb_trust_scenarios.py --fresh --only 3
```

**Anticipated output.**
```
[PASS]  3. Grant + check direct trust
        -> granted + verified direct trust for provider-alpha
```

---

### 4 — Revoke direct trust + sync provider flag

**Description.** Grants then revokes direct trust and asserts two things: the
`direct_trust` record flips to `false`, **and** the provider document's own
`direct_trust` field is kept in sync (the repository updates both). Guards
against the two stores drifting apart.

**Command.**
```bash
python optimusdb_trust_scenarios.py --fresh --only 4
```

**Anticipated output.**
```
[PASS]  4. Revoke direct trust + sync provider flag
        -> revoked direct trust; provider flag synced to False
```

---

### 5 — Reject invalid subject_type

**Description.** Attempts to set direct trust with a `subject_type` that is not
in the `SubjectType` enum and asserts a `ValueError` is raised. Negative-path
validation test — proves bad input is rejected rather than silently stored.

**Command.**
```bash
python optimusdb_trust_scenarios.py --fresh --only 5
```

**Anticipated output.**
```
[PASS]  5. Reject invalid subject_type
        -> invalid subject_type correctly rejected
```

---

### 6 — Persist + retrieve full trust calculation

**Description.** Stores the complete trust-calculation document (the shape
returned by `GET /trust/calculate`: provider-level score, performance trust,
reputation component, aggregated metrics, and per-device breakdown), then reads
back the latest score for the provider.

**Command.**
```bash
python optimusdb_trust_scenarios.py --fresh --only 6
```

**Anticipated output.**
```
[PASS]  6. Persist + retrieve full trust calculation
        -> persisted trust_score=0.8214
```

---

### 7 — Append-only trust history round-trip

**Description.** Saves the trust-calculation doc three times with changing scores
(`0.80 → 0.81 → 0.8214`); each save appends a `{score, timestamp}` entry to the
provider's append-only `trust_history` log. Then reads the log back and asserts
ordering (latest = `0.8214`) and length.

> The reported `history len` reflects how many appends happened *in this store*.
> On `--only 7` from a fresh store it is `3`; within a full run earlier scenarios
> add entries, so you may see `4`+.

**Command.**
```bash
python optimusdb_trust_scenarios.py --fresh --only 7
```

**Anticipated output.**
```
[PASS]  7. Append-only trust history round-trip
        -> history len=3, latest=0.8214
```

---

### 8 — Recompute trust (`0.8*perf + 0.2*rep`) + persist

**Description.** Recomputes the trust score from `performance_trust` (0.8637) and
`reputation` (0.65) using the documented weighted formula, then persists the
fresh value. `0.8·0.8637 + 0.2·0.65 = 0.82096 ≈ 0.821`. The stored sample value
`0.8214` uses volume-weighted internals, so the assertion allows a small rounding
delta (`< 5e-3`).

**Command.**
```bash
python optimusdb_trust_scenarios.py --fresh --only 8
```

**Anticipated output.**
```
[PASS]  8. Recompute trust (0.8*perf + 0.2*rep) + persist
        -> recomputed 0.8*0.8637+0.2*0.65=0.821
```

---

### 9 — Persist + retrieve device monitoring

**Description.** Saving a trust-calculation doc also persists each device's latest
monitoring snapshot into the `monitoring` collection (keyed by `device_did`).
This scenario reads back `capacity-vm-01` and asserts its uptime/latency values.

**Command.**
```bash
python optimusdb_trust_scenarios.py --fresh --only 9
```

**Anticipated output.**
```
[PASS]  9. Persist + retrieve device monitoring
        -> vm-01 uptime=0.9821, latency=118.42ms
```

---

### 10 — Issue + store + retrieve a Trust VC

**Description.** Reads the provider's computed trust score, issues a W3C-style
Trust Verifiable Credential carrying that score in
`vc.credentialSubject.trustScore` (mirrors `POST /trust/vc`), persists it under
its `vc_id`, and reads it back to confirm the score survived the round-trip.

> The VC id is a random UUID, so the `urn:uuid:…` fragment differs each run.

**Command.**
```bash
python optimusdb_trust_scenarios.py --fresh --only 10
```

**Anticipated output.**
```
[PASS] 10. Issue + store + retrieve a Trust VC
        -> issued+stored VC urn:uuid:538b71ae-b6… score=0.8214
```

---

### 11 — Query all VCs for a subject

**Description.** Issues two VCs for `provider-alpha`, then queries the `trust_vcs`
collection by `credentialSubject.id` to retrieve every credential belonging to
that subject. Exercises the predicate-based `query` path.

> Count is `2` on `--only 11`; in a full run, scenario 10 adds one more, so you
> may see `3`+.

**Command.**
```bash
python optimusdb_trust_scenarios.py --fresh --only 11
```

**Anticipated output.**
```
[PASS] 11. Query all VCs for a subject
        -> found 2 VCs for provider-alpha
```

---

### 12 — E2E: calculate only directly-trusted providers

**Description.** The full pipeline that mirrors `GET /trust/calculate`'s gating
rule. Persists both providers, grants direct trust to `alpha` and denies it to
`beta`, then iterates every provider: for each directly-trusted one it aggregates
device metrics, recomputes trust, and persists the score; providers **without**
direct trust are skipped. Asserts `alpha` is processed and `beta` is skipped.

**Command.**
```bash
python optimusdb_trust_scenarios.py --fresh --only 12
```

**Anticipated output.**
```
[PASS] 12. E2E: calculate only directly-trusted providers
        -> processed=['did:swarm:provider-alpha'], skipped=['did:swarm:provider-beta']
```

---

## Full-run output

```bash
python optimusdb_trust_scenarios.py --fresh
```

```
Running 12 scenario(s) on backend='local'
------------------------------------------------------------
[PASS]  1. Persist + retrieve a single provider
        -> stored+read provider did:swarm:provider-alpha
[PASS]  2. Bulk-persist + list providers
        -> listed 2 providers
[PASS]  3. Grant + check direct trust
        -> granted + verified direct trust for provider-alpha
[PASS]  4. Revoke direct trust + sync provider flag
        -> revoked direct trust; provider flag synced to False
[PASS]  5. Reject invalid subject_type
        -> invalid subject_type correctly rejected
[PASS]  6. Persist + retrieve full trust calculation
        -> persisted trust_score=0.8214
[PASS]  7. Append-only trust history round-trip
        -> history len=4, latest=0.8214
[PASS]  8. Recompute trust (0.8*perf + 0.2*rep) + persist
        -> recomputed 0.8*0.8637+0.2*0.65=0.821
[PASS]  9. Persist + retrieve device monitoring
        -> vm-01 uptime=0.9821, latency=118.42ms
[PASS] 10. Issue + store + retrieve a Trust VC
        -> issued+stored VC urn:uuid:…  score=0.8214
[PASS] 11. Query all VCs for a subject
        -> found 3 VCs for provider-alpha
[PASS] 12. E2E: calculate only directly-trusted providers
        -> processed=['did:swarm:provider-alpha'], skipped=['did:swarm:provider-beta']
------------------------------------------------------------
12/12 scenarios passed
```

> In a full run, history length (scenario 7) and VC count (scenario 11) are
> higher than in isolated `--only` runs because earlier scenarios write into the
> same store. Use `--only N` for the isolated counts shown in the catalogue.

---

## What ends up on disk

After a run, the LocalStore root (`./optimusdb_data` by default) contains one
JSON file per collection, plus `.log.json` files for append-only logs:

```
optimusdb_data/
├── providers.json          # {provider_did: {...}}
├── direct_trust.json       # {"capacity_provider:did:...": {trusted, updated_at}}
├── trust_scores.json       # {provider_did: {trust_score, devices, ...}}
├── trust_history.log.json  # {provider_did: [{score, timestamp}, ...]}
├── trust_vcs.json          # {vc_id: {vc: {credentialSubject: {trustScore}}}}
└── monitoring.json         # {device_did: {uptime, error_rate, latency_ms, timestamp}}
```

Example — `providers.json` (excerpt):
```json
{
  "did:swarm:provider-alpha": {
    "did": "did:swarm:provider-alpha",
    "name": "Alpha Cloud Provider",
    "owner": "org:alpha-labs",
    "direct_trust": false,
    "metrics": { "uptime": 0.9781, "error_rate": 0.0123, "latency_ms": 142.37 },
    "reputation": 0.65
  }
}
```

Example — `trust_history.log.json` (excerpt):
```json
{
  "did:swarm:provider-alpha": [
    { "score": 0.8214, "timestamp": "2026-07-07T19:43:33.989101+00:00" },
    { "score": 0.80,   "timestamp": "2026-07-07T19:43:33.989721+00:00" },
    { "score": 0.81,   "timestamp": "2026-07-07T19:43:33.990415+00:00" }
  ]
}
```

Inspect any collection directly:
```bash
cat optimusdb_data/trust_scores.json | python -m json.tool
```

---

## Wiring to a live OptimusDB

Implement the four methods in `OptimusDBHTTPStore` (or wrap your `KBClient`),
then run with `--backend http`:

```python
class OptimusDBHTTPStore(OptimusStore):
    def put(self, collection, key, value):
        # e.g. self.kb.put(namespace=collection, key=key, doc=value)
        ...
    def get(self, collection, key):
        # e.g. return self.kb.get(namespace=collection, key=key)
        ...
    def list(self, collection):
        # e.g. return self.kb.scan(namespace=collection)
        ...
    def append(self, collection, key, item):
        # e.g. monitoring / trust-history append into OptimusDB
        ...
```

```bash
python optimusdb_trust_scenarios.py --backend http --base-url http://localhost:8000
```

The repository, trust math, and all scenarios stay unchanged — only the storage
adapter differs.

---

## Extending

Add a scenario in three steps:

1. Write `def scenario_13(repo): ... return (ok: bool, detail: str)`.
2. Use `_assert(cond, msg)` for checks.
3. Register it: `Scenario(13, "My new scenario", scenario_13)` in `ALL_SCENARIOS`.

It is automatically picked up by `--list`, `--only`, and the runner.
