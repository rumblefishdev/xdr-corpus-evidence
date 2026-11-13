# Evidence: reproducing wallet-backend #729

**Sighting:** [stellar/wallet-backend#729](https://github.com/stellar/wallet-backend/pull/729). A CAP-40 signed-payload
signer (`P…` strkey) added by `set_options` cannot be persisted, and the whole ledger is lost.

**Result: reproduced.** On the same input, the pre-fix commit drops the ledger and the fixed commit stores the full `P…` key.

| | Pre-fix `2a0a84ea` | Fixed `2c873353` |
| --- | --- | --- |
| Backfill result (log) | `Batch [4437357-4437357] failed` after 5 attempts | `0/1 batches failed` |
| Error | `converting signer_account_id: … PBCK2RYY…AANMS has a 40-byte key payload; the account_id column stores only 32-byte account/contract keys (SQLSTATE 57014)` | none |
| Txs of ledger 4437357 in DB | **0 of 12** (the whole ledger is lost, not just the one tx) | 12 of 12 |
| `SIGNER`/`ADD` state change | none | `signer_account_id` = 41 bytes → `PBCK2RYYLIYOSHBEL2FQLAVRSHRH3GP3ID476SGKBV7BEZHMQ6NSAAAAAAB55LN6AANMS`, account `GBCK2RYY…BB7B`, weight 1 |
| Process exit code | **0** | 0 |
| Harness verdict | `dropped` | `pass` |

## Input

| Item | Value |
| --- | --- |
| Network / ledger | testnet, ledger 4437357 (ledger protocol 28, 12 txs) |
| Transaction | `d9f78749cd01297cd5f22c7f2c798c9eec93d56f399e1c4cc2f8977ca52b9fed` (tx index 0, `tx_success`) |
| Operation | `set_options`, signer `PBCK2RYY…AANMS` (ed25519 signed payload, payload `deadbe`, 3 bytes padded to 4), weight 1 |
| Ledger file | [`corpus/cap-0040/cap0040-signed-payload-signer-01/`](../../corpus/cap-0040/cap0040-signed-payload-signer-01/): the Galexie object as uploaded (`FFBC4A92--4437357.xdr.zst`, sha256 `afaad11d…`) and the decompressed `LedgerCloseMetaBatch` (sha256 `c7d9fc65…`) |
| Exporter | `stellar/stellar-galexie:30.0.1` (bundled stellar-core catches up from the SDF testnet archive), `scan-and-fill 4437357..4437358`, 1 ledger per file, 64000 files per partition |

## Files

| File | What |
| --- | --- |
| `repro-729.cast` | asciinema recording of a full clean run of `scripts/repro-729.sh --clean` (export → pre-fix → fixed). Play with `asciinema play evidence/729/repro-729.cast` |
| `galexie-export.log` | Galexie + captive core log of that export |
| `ledger-4437357.decoded.json` | `stellar xdr decode --type LedgerCloseMetaBatch` of the corpus file; the op is at `tx_processing[0]` / `tx_set` |
| `FFBC4A91--4437358.xdr.zst` | the next ledger. Only used for the sentinel's close time (see below) |
| `migrate-*.log`, `ingest-*.log` | full logs of `migrate up` and `ingest` per commit |
| `sql-*.txt` | output of [`scripts/query-729.sql`](../../scripts/query-729.sql) per commit |
| `verdict-*.txt` | the per-commit verdict computed by the script |

## Method and deviations from the original plan

1. **Backfill on an empty DB needs a sentinel row.** `startBackfilling` requires `END_LEDGER ≤ latest_ingest_ledger`. It also
   takes the "oldest ledger" from the `transactions` table, not from `ingest_store`. With an empty table that value is 0,
   so the run logs `No gaps to backfill` and ingests nothing. Seeding `ingest_store` alone is therefore not enough.
   [`scripts/seed-backfill.sql`](../../scripts/seed-backfill.sql) inserts one placeholder transaction at ledger 4437358
   (hash `0xEE…`, result `SENTINEL_NOT_A_REAL_TX`) and both cursors. Backfill then sees the gap `[4437357, 4437357]`.
   No assertion reads ledger 4437358.
2. **Galexie rejects `start == end`** ("end must be greater than start"), so we export 4437357..4437358.
3. **`STELLAR_ENVIRONMENT` is required** by `ingest`. It is only a label; the full env is in
   [`scripts/env-729.sh`](../../scripts/env-729.sh).
4. **Exit codes don't show the bug.** Backfill logs a failed batch but still exits 0, so the verdict comes from the DB.

## Finding for the corpus design: re-exports are not byte-reproducible

Exporting the same ledger twice with the same Galexie/core version gives two different `LedgerCloseMetaBatch` files of the same size.
The ledger header, the tx set, the upgrades, evictions and all 4 classic transactions (including the #729 tx) are identical.
All 8 Soroban transactions differ:
- `core_metrics` diagnostic events (`invoke_time_nsecs`, …) carry wall-clock timings;
- in one Soroban op the order of the `created` ledger-entry changes (TTL entries) differs between runs.

So the corpus must treat the **captured file as canonical** (checked by sha256), not "whatever a re-export produces".
A re-export can only be compared on a normalized view. `repro-729.sh` compares the ledger header and the target tx meta.
The decode → encode round trip of the committed file is byte-exact.

## Rerun

See the [top-level README](../../README.md#reproduce-wallet-backend-729).
