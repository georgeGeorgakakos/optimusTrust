# optimusTrust — TMS ↔ OptimusDB Integration Test

A small, self-contained test harness that persists and retrieves **trust /
reputation records** for the Swarmchestrate cluster, using a dedicated
[OptimusDB](http://193.225.250.240/optimusdb1) datastore via the
[`optimusPy`](https://github.com/georgeGeorgakakos/optimusPy) client.

Trust records live in their own datastore (`dstype = kbtrust`) and are
CRDT-replicated across peers like any other OptimusDB collection. Scoring uses
the **Beta Reputation model** (Jøsang & Ismail): `score = α / (α + β)`, where
each positive interaction increments `α` and each negative one increments `β`.
Unknown subjects start at 0.5.

## Repository layout

```
optimusTrust/
├── trust_store.py          # the TMS persistence layer (reusable library)
├── tms_demo.py             # CLI runner: health / persist / retrieve / wipe
├── sample_trust_data.json  # sample data wired to the live cluster peer IDs
├── requirements.txt
├── setup.sh                # Linux / macOS bootstrap
├── setup.ps1               # Windows / PowerShell bootstrap
└── README.md
```

## Prerequisites (all platforms)

- Python 3.8+
- `git`
- Network access to an OptimusDB agent (default `http://193.225.250.240/optimusdb1`)
- `optimusdb_client.py` from optimusPy — **not committed here**; the setup script
  for your OS fetches it automatically.

---

# 🐧 Linux / macOS

### Setup

```bash
chmod +x setup.sh
./setup.sh
```

`setup.sh` clones optimusPy, copies `optimusdb_client.py` into the project root,
and installs the dependencies. Manual equivalent:

```bash
pip3 install -r requirements.txt
git clone --depth 1 https://github.com/georgeGeorgakakos/optimusPy.git .optimuspy
cp .optimuspy/optimusdb_client.py .
```

### 1. Check the agent

```bash
python3 tms_demo.py health
```

### 2. Persist data

```bash
python3 tms_demo.py persist --file sample_trust_data.json
```

### 3. Retrieve data

```bash
python3 tms_demo.py retrieve --context storage --threshold 0.7
```

### 4. Clean up (optional)

```bash
python3 tms_demo.py wipe --context storage
```

---

# 🪟 Windows (PowerShell)

### Setup

```powershell
.\setup.ps1
```

If PowerShell blocks the script with an execution-policy error, allow it for the
current session and re-run:

```powershell
Set-ExecutionPolicy -Scope Process -Bypass
.\setup.ps1
```

`setup.ps1` clones optimusPy, copies `optimusdb_client.py` into the project root,
and installs the dependencies. Manual equivalent:

```powershell
pip install -r requirements.txt
git clone --depth 1 https://github.com/georgeGeorgakakos/optimusPy.git .optimuspy
Copy-Item .\.optimuspy\optimusdb_client.py .\optimusdb_client.py -Force
```

### 1. Check the agent

```powershell
python tms_demo.py health
```

### 2. Persist data

```powershell
python tms_demo.py persist --file sample_trust_data.json
```

### 3. Retrieve data

```powershell
python tms_demo.py retrieve --context storage --threshold 0.7
```

### 4. Clean up (optional)

```powershell
python tms_demo.py wipe --context storage
```

---

## Expected output (any platform)

`health`:

```
✓ Agent reachable
  peer_id        : QmaqAyTizLPzFSsDxNnteTGHZf3o5CVt9NfpSVDMSYbEZy
  role           : Coordinator  (coordinator=True, leader=True)
  health score   : 63.23  (Good)
  cluster peers  : total=3 coordinators=1 followers=2
```

`retrieve`:

```
All trust records in context 'storage':
  subject_id                                          score   conf    n     last
  QmaqAyTizLPzFSsDxNnteTGHZf3o5CVt9NfpSVDMSYbEZy      0.800   0.867   3  success
  QmTXRdGdVYWpNEub9Ud1sxnHjwWfQrsWMeJ9YPeDGHc6S4      0.533   0.867   3  success
  QmVgdCZ5T6UAkp474jd2Tt3Xb7WhYqixkMbuhAMpXZAg42      0.467   0.867   3  success

Most trusted (election candidates):
  QmaqAyTizLPzFSsDxNnteTGHZf3…  0.800

Trustworthy (score >= 0.7):
  QmaqAyTizLPzFSsDxNnteTGHZf3…  0.800
```

> **Note on the datastore.** `agent/status` confirms reachability but does *not*
> list which `dstype` names the agent accepts. The first `persist` run is also
> the test of whether `kbtrust` is allowed: if writes round-trip into `retrieve`,
> lazy store creation works; if not, register `kbtrust` in the Go agent config.

## Using the library directly

```python
from optimusdb_client import OptimusDBClient
from trust_store import TrustStore

ts = TrustStore(OptimusDBClient())

# PERSIST
ts.record_interaction("peer-A", success=True, context="storage")
ts.set_trust("peer-B", score=0.9, context="storage")

# RETRIEVE
ts.get_score("peer-A", context="storage")          # float in [0, 1]
ts.most_trusted(context="storage", top=3)          # ranked candidates
ts.trustworthy(threshold=0.7, context="storage")   # filtered, ranked
```

## Common options

| Flag | Default | Meaning |
|------|---------|---------|
| `--url` | `http://193.225.250.240/optimusdb1` | OptimusDB base URL |
| `--api-context` | `swarmkb` | OptimusDB API context (the `{context}` path segment) |
| `--store` | `kbtrust` | datastore (`dstype`) for trust records |
| `--log-level` | `WARNING` | `DEBUG` to see raw requests/responses |

## Record schema

One record per subject per context, keyed `trust:<context>:<subject_id>` so
repeated writes upsert in place:

| Field | Description |
|-------|-------------|
| `subject_id`, `subject_type` | who the trust is about (peer / agent / resource) |
| `context` | trust is context-scoped (e.g. `storage`, `compute`) |
| `alpha`, `beta` | Beta-distribution evidence counters |
| `trust_score`, `confidence` | derived score and evidence-weight |
| `interaction_count`, `last_outcome` | interaction history summary |
| `source_peer`, `created_at`, `updated_at` | provenance |

## License

MIT — free to use and modify for Swarmchestrate testing.
