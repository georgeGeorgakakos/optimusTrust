# optimusTrust — Persist & Retrieve JSON in OptimusDB

Store JSON documents in a dedicated [OptimusDB](http://193.225.250.240/optimusdb1)
datastore and retrieve them by **criteria** (field match, comparison operators,
or raw `$and`/`$or`), using the [`optimusPy`](https://github.com/georgeGeorgakakos/optimusPy)
client. Documents land in their own datastore (`dstype = kbtrust`) and are
CRDT-replicated across peers like any other OptimusDB collection.

There is no scoring or domain logic here: you give it a JSON file, it persists the
document(s); you give it criteria, it returns the matching documents.

## Repository layout

```
optimusTrust/
├── store_demo.py           # CLI: persist / retrieve / delete / health
├── sample_documents.json   # sample documents to store and query
├── requirements.txt
├── setup.sh                # Linux / macOS bootstrap
├── setup.ps1               # Windows / PowerShell bootstrap
└── README.md
```

## Prerequisites (all platforms)

- Python 3.8+, `git`
- Network access to an OptimusDB agent (default `http://193.225.250.240/optimusdb1`)
- `optimusdb_client.py` from optimusPy — **not committed here**; the setup script
  for your OS fetches it automatically.

## Criteria syntax

Used by `retrieve` and `delete`. Repeat `--where` to AND multiple conditions.

| Form | Example | Sent to OptimusDB |
|------|---------|-------------------|
| exact | `--where context:storage` | `{"context": "storage"}` |
| greater / less | `--where trust_level:0.6:gte` | `{"trust_level": {"$gte": 0.6}}` |
| not equal | `--where verified:false:ne` | `{"verified": {"$ne": false}}` |
| regex | `--where peer_id:^Qmaq.*:regex` | `{"peer_id": {"$regex": "^Qmaq.*"}}` |
| raw (for `$or`/`$and`) | `--raw '{"$or":[{"context":"storage"},{"context":"compute"}]}'` | merged in |

Operators: `gt`, `gte`, `lt`, `lte`, `ne`, `regex`. Values are auto-typed
(`true`/`false` → boolean, numbers → int/float, otherwise string).

---

# 🐧 Linux / macOS

### Setup
```bash
chmod +x setup.sh
./setup.sh
```

### Persist
```bash
python3 store_demo.py persist --file sample_documents.json --store kbtrust
```

### Retrieve by criteria
```bash
python3 store_demo.py retrieve --store kbtrust --where context:storage
python3 store_demo.py retrieve --store kbtrust --where trust_level:0.6:gte
python3 store_demo.py retrieve --store kbtrust --where role:follower --where verified:true
python3 store_demo.py retrieve --store kbtrust --raw '{"$or":[{"context":"storage"},{"context":"compute"}]}'
python3 store_demo.py retrieve --store kbtrust            # no criteria -> all docs
```

### Delete by criteria
```bash
python3 store_demo.py delete --store kbtrust --where _id:peer:QmVgdCZ5T6UAkp474jd2Tt3Xb7WhYqixkMbuhAMpXZAg42
```

---

# 🪟 Windows (PowerShell)

### Setup
```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

### Persist
```powershell
python store_demo.py persist --file sample_documents.json --store kbtrust
```

### Retrieve by criteria
```powershell
python store_demo.py retrieve --store kbtrust --where context:storage
python store_demo.py retrieve --store kbtrust --where trust_level:0.6:gte
python store_demo.py retrieve --store kbtrust --where role:follower --where verified:true
python store_demo.py retrieve --store kbtrust --raw '{\"$or\":[{\"context\":\"storage\"},{\"context\":\"compute\"}]}'
python store_demo.py retrieve --store kbtrust            # no criteria -> all docs
```

> PowerShell needs the inner quotes in `--raw` escaped as `\"` (shown above).

### Delete by criteria
```powershell
python store_demo.py delete --store kbtrust --where _id:peer:QmVgdCZ5T6UAkp474jd2Tt3Xb7WhYqixkMbuhAMpXZAg42
```

---

## Example

```
$ python store_demo.py persist --file sample_documents.json --store kbtrust
Persisting 3 document(s) into store 'kbtrust'...
  stored _id=peer:QmaqAyTizLPzFSsDxNnteTGHZf3o5CVt9NfpSVDMSYbEZy
  stored _id=peer:QmTXRdGdVYWpNEub9Ud1sxnHjwWfQrsWMeJ9YPeDGHc6S4
  stored _id=peer:QmVgdCZ5T6UAkp474jd2Tt3Xb7WhYqixkMbuhAMpXZAg42
Done.

$ python store_demo.py retrieve --store kbtrust --where trust_level:0.6:gte
Querying store 'kbtrust' with criteria: [{"trust_level": {"$gte": 0.6}}]
Retrieved 1 document(s):

{
  "_id": "peer:QmaqAyTizLPzFSsDxNnteTGHZf3o5CVt9NfpSVDMSYbEZy",
  "context": "storage",
  "role": "coordinator",
  "trust_level": 0.8,
  "verified": true,
  ...
}
```

## Notes

- **Document IDs.** Any document without an `_id` gets a generated one on persist.
  Because OrbitDB's docstore replaces by `_id`, persisting a document whose `_id`
  already exists **updates** it (upsert) rather than duplicating.
- **The datastore.** The first `persist` run is also the test of whether `kbtrust`
  is accepted: if writes round-trip into `retrieve`, lazy store creation works; if
  not, register `kbtrust` in the Go agent config.
- **`$or` / `$and`.** Passed through `--raw` and evaluated server-side by OptimusDB.

## Library / programmatic use

```python
from optimusdb_client import OptimusDBClient
client = OptimusDBClient()

# persist
client.create(documents=[{"_id": "peer:X", "context": "storage", "trust_level": 0.8}],
              dstype="kbtrust")

# retrieve by criteria
client.get(criteria=[{"context": "storage"}], dstype="kbtrust")
client.get(criteria=[{"trust_level": {"$gte": 0.6}}], dstype="kbtrust")
```

## License

MIT — free to use and modify for Swarmchestrate testing.
