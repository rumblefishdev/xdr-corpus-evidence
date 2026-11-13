# Reproducing wallet-backend #729

**The bug** ([stellar/wallet-backend#729](https://github.com/stellar/wallet-backend/pull/729)): since CAP-40 (protocol 19),
an account can add a **signed-payload signer**. Its key is written `P…` and is 40–100 bytes (a 32-byte ed25519 key plus a payload),
not 32 like a normal `G…` key. wallet-backend stored signer keys in a column that only accepts 32-byte keys. When it met
such a signer, the database write failed, the whole ledger was rolled back, and ingestion retried the same ledger forever.

**Result: reproduced.** The same ledger is fed to wallet-backend at the commit before the fix and at the fix:

| | Pre-fix `2a0a84ea` | Fixed `2c873353` (#729 merged) |
| --- | --- | --- |
| Backfill log | `Batch [4437357-4437357] failed` after 5 attempts | `0/1 batches failed` |
| Error | `converting signer_account_id: … PBCK2RYY…AANMS has a 40-byte key payload; the account_id column stores only 32-byte account/contract keys (SQLSTATE 57014)` | none |
| Transactions of ledger 4437357 in the DB | **0 of 12**: the whole ledger is lost, not just this tx | 12 of 12 |
| Signer state change | none | `signer_account_id` = 41 bytes, decoding to `PBCK2RYYLIYOSHBEL2FQLAVRSHRH3GP3ID476SGKBV7BEZHMQ6NSAAAAAAB55LN6AANMS`, account `GBCK2RYY…BB7B`, weight 1 |
| Process exit code | **0** (the failure is only in the log and the DB) | 0 |
| **Verdict** | **`dropped`** | **`pass`** |

## How this relates to the corpus

The input ledger is corpus entry [`cap0040-signed-payload-signer-01`](../corpus/entries/cap-0040/cap0040-signed-payload-signer-01/).
This folder is a first, hand-built version of the RFP's **replay harness**. It takes a corpus entry, delivers it to an indexer
through an input the indexer already reads (the Galexie data-lake layout), and reports **pass / mishandled / dropped** from what the
indexer stored. The harness deliverable generalises this to every entry, with an adapter per indexer.

## Run it

```bash
sighting-729/repro.sh --clean --from-corpus   # ~30 s after the first run; uses the committed ledger file
sighting-729/repro.sh --clean                 # ~90 s; exports the ledger again from the testnet history archive
```

The script exits 0 only if pre-fix = `dropped` and fixed = `pass`. The first run also builds wallet-backend twice
(about 1 minute each, in a Go container) and pulls the Docker images.

## What the script does, step by step

```
 testnet history archive ──► Galexie (captive stellar-core) ──► local S3 (SeaweedFS) ──► wallet-backend ingest ──► Postgres ──► verdict
        (step 2)                     (step 2)                    (step 1)                 (steps 4-5)                  (step 6)
```

1. **Start the local infrastructure** ([`infra/docker-compose.yml`](../infra/docker-compose.yml)):
   - **SeaweedFS**, an S3-compatible object store, plays the cloud bucket a production deployment would use. It runs
     without authentication; the keys `test`/`test` are dummies the AWS SDK needs to sign requests.
   - **TimescaleDB** (Postgres) for wallet-backend.

   Both use the same images and settings as wallet-backend's own setup.
2. **Put ledger 4437357 into the S3 bucket.** This is the "data lake" that wallet-backend reads.
   - With `--from-corpus`, the committed file is uploaded as-is.
   - Without it, **Galexie** exports the ledger again. Galexie is SDF's ledger exporter and bundles its own
     stellar-core (*captive core*). Asked for ledgers 4437357–4437358, core:
     1. downloads the testnet **history archive**: the files validators publish every 64 ledgers (a *checkpoint*), holding the full
        ledger state and every transaction;
     2. rebuilds the state at the checkpoint before 4437357;
     3. replays the two ledgers itself.

     Replaying gives each ledger's complete **`LedgerCloseMeta`**: the transactions, their results, and every ledger
     entry they changed. Galexie wraps each ledger in a `LedgerCloseMetaBatch` (one ledger per file), compresses it with zstd,
     and uploads it under a key such as `ledgers/testnet/FFBC9DFF--4416000-4479999/FFBC4A92--4437357.xdr.zst`. The hex
     prefix is `0xFFFFFFFF − ledger`, so newer ledgers sort first. Galexie also writes `.config.json` describing this layout.
     Config: [`infra/galexie/testnet.toml`](../infra/galexie/testnet.toml).
3. **Verify the object.** Download it, decompress it and decode it with the `stellar` CLI. Check that the #729 transaction and its `P…` signer are
   in it, and compare it with the committed corpus file (see *Re-exports are not byte-identical* below).
4. **Build wallet-backend at both commits** ([`build-wallet-backend.sh`](build-wallet-backend.sh)), inside a `golang:1.25.9`
   container (the version in wallet-backend's `go.mod`), so no local Go is needed.
5. **For each commit, on a fresh database:**
   - run `wallet-backend migrate up`, then the seed [`seed-backfill.sql`](seed-backfill.sql) (see *Deviations* below);
   - run `wallet-backend ingest` in **backfill** mode for ledger 4437357, with the **datastore** ledger backend pointed at
     the local S3 ([`env.sh`](env.sh)). wallet-backend downloads the object, decompresses and decodes it, and runs its
     indexer over the `LedgerCloseMeta`. This is the same code path it uses in production with a cloud data lake.
6. **Verdict from the database** ([`query.sql`](query.sql)):
   - `dropped`: the transaction is not stored;
   - `mishandled`: it is stored, but the signer state change is missing or wrong;
   - `pass`: the signer state change holds the full `P…` key, the right account and weight 1.

   The stored key bytes are turned back into a strkey by [`strkey.py`](strkey.py), independently of wallet-backend's code.

## Files

| File | What |
| --- | --- |
| [`evidence/repro-729.cast`](evidence/repro-729.cast) | asciinema recording of a full clean run (export → pre-fix → fixed). Play: `asciinema play sighting-729/evidence/repro-729.cast`. Recorded before the folders were reorganised, so it shows the old script path `scripts/repro-729.sh`. |
| `evidence/galexie-export.log` | Galexie and captive-core log of that export |
| `evidence/ledger-4437357.decoded.json` | `stellar xdr decode` of the ledger: the operation is at `tx_set` / `tx_processing[0]` |
| `evidence/FFBC4A91--4437358.xdr.zst` | the next ledger; only its close time is used, for the seed row |
| `evidence/migrate-*.log`, `evidence/ingest-*.log` | full wallet-backend logs per commit |
| `evidence/sql-*.txt`, `evidence/verdict-*.txt` | query output and the computed verdict per commit |

## Deviations from a plain run, and findings

1. **Backfill on an empty database needs a seed row.** Backfill only fills gaps *inside* what is already ingested. It requires
   `END_LEDGER ≤ latest_ingest_ledger`, and it takes the "oldest ledger" from the `transactions` table. On an empty
   table that is 0, so the run logs `No gaps to backfill` and ingests nothing. `seed-backfill.sql` inserts one clearly
   marked placeholder transaction at ledger 4437358 (hash `0xEE…`, result `SENTINEL_NOT_A_REAL_TX`) and both cursors. Backfill then
   sees the gap `[4437357, 4437357]`. No check reads ledger 4437358.
2. **Galexie rejects a range with start = end**, so it exports 4437357–4437358.
3. **`ingest` also needs `STELLAR_ENVIRONMENT`.** It is only a label for logs and metrics.
4. **Exit codes don't show the bug.** Backfill logs the failed batch but exits 0, so the verdict must come from the stored
   data. The harness needs to do the same.
5. **Re-exports are not byte-identical.** Exporting the same ledger twice gives files of the same size but different bytes.
   The ledger header, the transaction set and all classic transactions (including #729's) are identical. Soroban transactions
   differ: their `core_metrics` diagnostic events carry wall-clock timings, and in one case ledger-entry changes came
   out in a different order. So the corpus keeps the **captured file as canonical** (by sha256). When re-exporting, the script
   compares only the ledger header and the #729 transaction.
