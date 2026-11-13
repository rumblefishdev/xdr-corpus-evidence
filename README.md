# xdr-corpus-evidence

Evidence for our proposal to **SCF RFP 9: XDR/CAP-Coverage Audit Tool and Indexer-Hardening Guide** ([RFP summary](docs/rfp-09.md)).

The RFP's reviewability section asks a proposal for three things:

| Requirement | Status |
| --- | --- |
| Evidence of reproducing one sighting | **Done:** wallet-backend #729, see [`evidence/729/`](evidence/729/README.md) |
| 2–3 sample corpus entries | **Done:** 3 entries (CAP-40 signed-payload signer, muxed SAC recipient, LP issuer order), see [`corpus/`](corpus/README.md) |
| Draft manifest schema | **Done (draft):** [`corpus/manifest.schema.json`](corpus/manifest.schema.json) + generated [`corpus/manifest.json`](corpus/manifest.json) |

## Reproduce wallet-backend #729

A CAP-40 signed-payload signer (`P…`) added by `set_options` in testnet ledger 4437357 makes wallet-backend drop the whole
ledger before the fix ([#729](https://github.com/stellar/wallet-backend/pull/729)). The script builds wallet-backend at the
pre-fix and the fixed commit and backfills that ledger into a fresh database with each binary. Ledgers are read from a local
S3 data lake filled by Galexie. The script then checks the database:

```bash
scripts/repro-729.sh --clean                # export the ledger from testnet with Galexie (~2 min)
scripts/repro-729.sh --clean --from-corpus  # or use the committed ledger file (no network archive needed)
```

Expected ending:

```
pre-fix (2a0a84ea…): dropped  (expected: dropped)
fixed   (2c873353…): pass  (expected: pass)
REPRODUCED: wallet-backend #729
```

The script exits non-zero unless the pre-fix commit drops the tx and the fixed commit stores the full `P…` signer.
The first run also pulls images and does two Go builds (about 1 minute each here). Later runs take 30 s (`--from-corpus`) to 90 s.

**Requirements:** Docker with Compose v2, `psql`, `zstd`, `curl`, `jq`, `python3`, and the [`stellar` CLI](https://github.com/stellar/stellar-cli)
(for `stellar xdr decode`). Go is not needed: binaries are built in a `golang:1.25.9` container. Free ports 5433 (Postgres)
and 8333 (S3) are needed.

What gets started (`docker-compose.yml`): SeaweedFS `4.46` as the S3 store and TimescaleDB `2.28.2-pg17`, with the
same images and settings wallet-backend uses in its own setup. Galexie `30.0.1` runs as a one-shot exporter (profile `export`).
The S3 keys `test`/`test` are dummies, because the local S3 runs without auth.

## Layout

```
docker-compose.yml          local S3 (SeaweedFS) + TimescaleDB + Galexie exporter + local quickstart
galexie/testnet.toml        Galexie config (S3 → ledgers/testnet, 1 ledger per file)
scripts/
  repro-729.sh              end-to-end reproduction with verdicts
  build-wallet-backend.sh   build wallet-backend at a commit inside a golang container
  env-729.sh                wallet-backend ingest env (backfill of ledger 4437357)
  seed-backfill.sql         makes backfill possible on an empty DB (see evidence/729)
  query-729.sql             evidence queries
  strkey.py                 independent strkey encoder for the stored key bytes
  roundtrip.sh              sha256 + byte-exact decode/encode of every corpus file
  build-manifest.py         derive corpus/manifest.json from the corpus files (--check in CI)
  get-stellar-cli.sh        fetch the pinned stellar CLI 30.0.1 (sha256-checked)
gen/                        corpus generators (Go, run in a container): muxed-soroban, lp-issuer-order
corpus/                     manifest schema, manifest, entry files (see corpus/README.md)
evidence/729/               logs, SQL output, decoded XDR, asciinema recording
evidence/gen-local/         generator runs against a local quickstart network
.github/workflows/          CI: round trip, manifest check, schema validation
```

This repo is public from day one. It contains no credentials; the only keys are the dummy local S3 keys above.
