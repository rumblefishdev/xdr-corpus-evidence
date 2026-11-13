# xdr-corpus-evidence

Evidence repo for our proposal to **SCF RFP 9: XDR/CAP-Coverage Audit Tool and Indexer-Hardening Guide**.
Full RFP text (our paraphrase): `docs/rfp-09.md`. Original: https://stellar.gitbook.io/scf-handbook/scf-awards/build-award/rfp-track

This repo is **public from day one** ("building in public" is an evaluation criterion). Never commit secrets, keys or credentials.
The only ones used here are the dummy local S3 keys `test`/`test`.

## Why this repo exists

RFP 9 (section 4, "Reviewability") says a proposal is **incomplete** unless it includes:
1. a **draft manifest schema** for the corpus,
2. **2–3 sample corpus entries**,
3. **evidence of reproducing one sighting** from the RFP's section 7.

This repo produces all three. The team: Marek (owner) and Oskar. The plan lives in the doc
"RFP 9 – Proposal Evidence Plan" (https://claude.ai/artifact/7jQA4ec746fA2oTwfxXxaU). This file holds everything needed from it.

Decisions already made (2026-11-13): apply for RFP 9; full $65K budget including the linter; repo public; use the
**Galexie data lake path** (not RPC `getLedgers`) because wallet-backend reads it and it is simpler.

## Current layout (2026-11-13)

Organised by deliverable; `README.md` maps each folder to RFP §4. `infra/` (compose + Galexie config),
`corpus/` (entries/, manifest + schema, gen/, tools/), `sighting-729/` (#729 repro, harness prototype),
`conformance-1766/` (js-stellar-sdk #1766 on entry lp-issuer-order-01: 17.2.0 mishandled, 17.2.1 pass, latest 17.4.0 pass;
conformance-runner prototype). Each entry also stores its target tx envelope (`tx-<hash>.TransactionEnvelope.xdr`), because
old SDK releases (e.g. the pre-fix one) can't decode current ledger meta: 17.2.x fails on protocol-30 LCM, 17.3.0+ decodes it. Paths in the plan sections below are the old ones.

## Done (2026-11-13): reproduce wallet-backend #729 (sighting)

Status: reproduced; `sighting-729/repro.sh` passes (pre-fix = dropped, fixed = pass). Evidence + write-up in
`sighting-729/README.md`. Corrections to the plan below, learned while doing it:
- Galexie `scan-and-fill` rejects start = end; export 4437357..4437358.
- Backfill on an empty DB also needs a row in `transactions` (oldest ledger comes from there, not `ingest_store`);
  see `sighting-729/seed-backfill.sql`. Seeding only `latest_ingest_ledger` gives "No gaps to backfill".
- `ingest` also requires `STELLAR_ENVIRONMENT`. Full env: `sighting-729/env.sh`. DB runs on port **5433**.
- Backfill exits 0 even when a batch fails: verdicts must come from the DB.
- Galexie re-exports are NOT byte-identical (Soroban `core_metrics` diagnostic timings, change ordering). The committed
  file is canonical; compare re-exports only on a normalized view. Relevant for the manifest schema (sha256 of captured file).
- Galexie layout used: 1 ledger per file, 64000 files per partition; the stellar CLI 23.4.1 decodes the export fine.

The original plan, kept for reference:

### The bug
wallet-backend PR #729 (https://github.com/stellar/wallet-backend/pull/729): a CAP-40 **signed-payload signer** (`P…` strkey,
40–100 bytes) added by `set_options` makes `AddressBytea.Value()` fail. The COPY fails, the whole persist transaction rolls back
(including the cursor), and ingestion retries the same ledger forever. Backfill fails the same way.

### Fixed facts (verified 2026-11-13)
| Item | Value |
| --- | --- |
| Network | testnet (not reset since the sighting; tip ≈ 5.7M, protocol 30) |
| Ledger | 4437357. Its history checkpoint is 4437375 (`0x0043b57f`) |
| Transaction | `d9f78749cd01297cd5f22c7f2c798c9eec93d56f399e1c4cc2f8977ca52b9fed` |
| Operation | `set_options` adding signer type `SIGNER_KEY_TYPE_ED25519_SIGNED_PAYLOAD` |
| Source account | `GBCK2RYYLIYOSHBEL2FQLAVRSHRH3GP3ID476SGKBV7BEZHMQ6NSBB7B` |
| Signer key | `PBCK2RYYLIYOSHBEL2FQLAVRSHRH3GP3ID476SGKBV7BEZHMQ6NSAAAAAAB55LN6AANMS` |
| Pre-fix commit | `2a0a84ea12523b6c92e278c0c84b443041240095` (parent of the #729 merge) |
| Fixed commit | `2c873353c1f0f3c9b145e25a7d009023ad104a1c` (#729 merge on `main`) |

### Steps
1. **Local S3:** SeaweedFS `chrislusf/seaweedfs:4.46`, S3 port 8333, bucket `ledgers`, region `us-east-1`, keys `test`/`test`.
   These settings copy wallet-backend's integration tests:
   `internal/integrationtests/infrastructure/ledger_exporter.go`.
2. **Export ledger 4437357 to the data lake.** Run Galexie in `scan-and-fill` mode (start = end = 4437357) on testnet, writing to
   `ledgers/testnet` with **1 ledger per file**. Galexie bundles captive stellar-core, which catches up from the testnet history
   archive. Check the Galexie README for the exact config keys of the S3 datastore and the schema settings.
   Fallback: write a small Go exporter modelled on wallet-backend's `ledger_exporter.go`. It already writes exactly the layout
   wallet-backend reads (zstd + XDR `LedgerCloseMetaBatch`, `schema.GetObjectKeyFromSequenceNumber`). Get the LCM from captive core;
   RPC won't do, because the ledger is outside RPC retention.
3. **Verify the export:** download the object, `zstd -d`, then `stellar xdr decode --type LedgerCloseMetaBatch`. Confirm the tx
   above and its `ED25519_SIGNED_PAYLOAD` signer are present. Keep the file in the repo, so the evidence survives a testnet reset.
4. **Build wallet-backend** at both commits into two binaries (`wallet-backend-prefix`, `wallet-backend-fixed`).
   **Go is not installed on this machine**: either install the version from wallet-backend's `go.mod`, or build inside a
   `golang:<that version>` Docker container.
5. **Database:** PostgreSQL + TimescaleDB from wallet-backend's own `docker-compose.yaml`. Use a **fresh database per
   commit** and run `wallet-backend migrate up` with the matching binary.
6. **Run the pre-fix commit in backfill mode** (backfill, not live: live mode on an empty DB first downloads all balances from
   the latest checkpoint; backfill reads just the range and takes no lock):
   ```bash
   INGESTION_MODE=backfill START_LEDGER=4437357 END_LEDGER=4437357 \
   LEDGER_BACKEND_TYPE=datastore DATASTORE_BUCKET_PATH=ledgers/testnet \
   DATASTORE_ENDPOINT_URL=http://localhost:8333 DATASTORE_REGION=us-east-1 \
   DATASTORE_LEDGERS_PER_FILE=1 DATASTORE_FILES_PER_PARTITION=<match the export> \
   AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_REGION=us-east-1 \
   NETWORK_PASSPHRASE="Test SDF Network ; September 2015" \
   RPC_URL=https://soroban-testnet.stellar.org DATABASE_URL=<local db> \
     ./wallet-backend-prefix ingest
   ```
   Expected: an encode error on `signer_account_id`, and the transaction is not persisted. Save the full log.
7. **Run the fixed commit the same way** on a fresh DB. Expected: `Backfilling completed …`. Query `state_changes` for the tx:
   `signer_account_id` must decode to the full `PBCK2RYY…AANMS`. Optionally, GraphQL `signerAddress` returns it.
8. **Save evidence** into `sighting-729/evidence/`: both logs, SQL output, decoded XDR, and an asciinema recording of steps 6–7.
9. **Automate:** `sighting-729/repro.sh` builds, exports, runs both, and asserts. It must fail on pre-fix and pass on the fix, the
   same shape as the future harness verdict (pass / mishandled / dropped). A reviewer must be able to rerun it from the
   README in under 30 minutes.

### Known risks and fallbacks
- **Backfill on an empty DB may be rejected.** The docs say `END_LEDGER` must be ≤ `latest_ingest_ledger`.
  Check `internal/services/ingest_backfill.go`; if needed, seed `ingest_store` with `latest_ingest_ledger = 4437357`.
- **Galexie S3 config or object layout mismatch** → use the `ledger_exporter.go`-style fallback (step 2).
- **The pre-fix commit needs different migrations or env** → separate DB per binary; read that commit's `docs/deploy/configuration.md`.
- `ingest` always requires `RPC_URL`, even with the datastore backend. The public testnet RPC is fine.

Useful wallet-backend docs: `docs/deploy/ingest.md`, `docs/deploy/backfill.md`, `docs/deploy/configuration.md`.

## Done (2026-11-13): corpus entries + draft manifest schema

See `corpus/README.md` (entries, schema decisions, findings). Deviations from the plan below:
- `cap0027-muxed-soroban-source-01` became **`cap0027-muxed-sac-destination-01`**: core (protocol ≥ 25) rejects a muxed tx
  or op source on `invoke_host_function` (`tx_soroban_invalid`). Both rejections are recorded as `negative_probes`; the entry is a
  SAC transfer to a muxed `ScAddress` (#716 producer 2). The classic M→M payment fallback is still in the generator.
  The RFP also asks for a "muxed source" pattern; that needs a classic-tx entry (not done yet).
- Decided: keep both `protocol.introduced_in` and `protocol.ledger`; fixed `expected` core + per-pattern `extra`.
  `synthetic` = hand-crafted bytes only (all three entries are `false`).
- Protocol-30 ledgers need stellar CLI ≥ 30 (`corpus/tools/get-stellar-cli.sh`); the installed 23.4.1 fails on them.
- Not yet done: mainnet passphrase for generators (only testnet + standalone tested), `--check-lcm` goldens.

## Original plan for the tasks after #729 (same repo)

### Two more sample corpus entries
| ID | Pattern | Source | Targets |
| --- | --- | --- | --- |
| `cap0040-signed-payload-signer-01` | the #729 ledger above | mined, testnet 4437357 | wallet-backend #729 |
| `cap0027-muxed-soroban-source-01` | `invoke_host_function` (e.g. SAC `transfer`) with an `M…` operation source | generated on testnet, exported with Galexie | wallet-backend #716 (M… truncated) |
| `lp-issuer-order-01` | `change_trust` on an LP share whose issuers sort differently by strkey string vs XDR bytes | generated (grind keypairs: base32 digits 2–7 sort after letters by value, before them in ASCII) | js-stellar-sdk #1766 |

- If core rejects a muxed op source on `invoke_host_function`, use a classic payment with a muxed source and destination
  instead, and record the rejection.
- Every entry must round-trip byte-exactly: `stellar xdr decode` then `encode`, compared byte for byte.
- Expected values in the manifest come from the decode, never typed by hand.
- Generators go in `gen/` and must also run against a local quickstart network (both passphrases).

### Draft manifest schema
- `corpus/manifest.schema.json` (JSON Schema) + `corpus/manifest.json`.
- Required by the RFP: entry ID, CAP, protocol version, source, synthetic flag, expected decoded values.
- We add: `sep`, `network`, `source{kind,ledger,tx_hash}`, `files{path,xdr_type,sha256}`, `pattern`, `core_validates`,
  `roundtrip`, `check_lcm_golden` (null unless a stellar-core golden exists), `related` (issue links).
- Still to decide: `introduced_in` vs `ledger_protocol` versions; a fixed `expected` core plus a per-pattern `extra` object.

### Target layout
```
README.md  docker-compose.yml  galexie/testnet.toml
scripts/{export-ledger.sh,repro-729.sh,roundtrip.sh}
gen/  corpus/{manifest.schema.json,manifest.json,cap-0040/,cap-0027/,cap-0038/}
evidence/729/
```
Add a GitHub Actions job running `roundtrip.sh` + schema validation.

## Local environment (checked 2026-11-13)
Available: docker + compose v5, `stellar` CLI (`~/.local/bin/stellar`), `zstd`, `psql`. **Missing: Go.**
