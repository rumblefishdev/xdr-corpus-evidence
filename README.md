# xdr-corpus-evidence

Supporting evidence for our proposal to **SCF RFP 9: XDR/CAP-Coverage Audit Tool and Indexer-Hardening Guide**
([our summary of the RFP](docs/rfp-09.md)).

RFP 9 asks for a **corpus of rare transaction shapes**: real ledger data that indexers and SDKs tend to mishandle, tagged by
CAP, each with the values a correct reader must decode. It also asks for tools that run the corpus through indexers (a
*replay harness*) and through SDKs (a *conformance runner*). Section 4 calls a proposal **incomplete** unless it includes three
things. This repository provides them, each as something a reviewer can rerun:

| RFP §4 asks for | Here | Rerun | Result |
| --- | --- | --- | --- |
| a draft manifest schema | [`corpus/manifest.schema.json`](corpus/manifest.schema.json), with the generated [`corpus/manifest.json`](corpus/manifest.json) | `corpus/tools/roundtrip.sh` and `python3 -I corpus/tools/build-manifest.py --check` (~10 s, offline) | 3 entries, every file round-trips byte-exactly, manifest = fresh decode |
| 2–3 sample corpus entries | [`corpus/`](corpus/README.md): a CAP-40 signed-payload signer, a muxed token-contract recipient, a pool with differently ordered issuers | `corpus/tools/regen-entry.sh <entry>` regenerates one on testnet (3–8 min) | same pattern and same core verdicts as the committed entry |
| evidence of reproducing one sighting | [`sighting-729/`](sighting-729/README.md): wallet-backend #729 | `sighting-729/repro.sh --clean --from-corpus` (~30 s; first run builds wallet-backend) | pre-fix **dropped**, fixed **pass** |
| *(extra)* a corpus entry catching an SDK bug | [`conformance-1766/`](conformance-1766/README.md): js-stellar-sdk #1766 | `conformance-1766/run.sh` (~30 s) | 17.2.0 **mishandled**, 17.2.1 **pass** |

## How the parts fit together

```
                       ┌───────────────────────────── corpus/ ─────────────────────────────┐
  testnet history ───► │ entry 1  cap0040-signed-payload-signer-01   (mined)                │ ──► sighting-729/      wallet-backend, pre-fix vs fixed
  gen/ + testnet  ───► │ entry 2  cap0027-muxed-sac-destination-01   (generated)            │      (replay-harness prototype)
  gen/ + testnet  ───► │ entry 3  lp-issuer-order-01                 (generated)            │ ──► conformance-1766/  js-stellar-sdk, pre-fix vs fixed
                       │ manifest.json  ◄── derived from the files by tools/build-manifest  │      (conformance-runner prototype)
                       └────────────────────────────────────────────────────────────────────┘
```

- **The corpus is the product.** Each entry is ledger data that **stellar-core itself emitted**: either found in network
  history (*mined*) or produced by submitting transactions we wrote to testnet (*generated*). None is hand-crafted, so in the
  RFP's terms none is *synthetic*. The manifest lists the values a correct reader must decode, and they are all derived from the files.
  [`corpus/README.md`](corpus/README.md) explains how entries are made and what exactly is validated.
- **`sighting-729/` and `conformance-1766/` use the corpus.** Each feeds one entry to a real reader at the commit before and
  after a known bug fix, and reports **pass / mishandled / dropped**. They are hand-built first versions of the RFP's replay
  harness (indexers) and conformance runner (SDKs).
- The `stellar` CLI appears throughout only as the **reference XDR decoder**: it wraps rs-stellar-xdr, the library the RFP names.

## Requirements

Docker with Compose v2, `psql`, `zstd`, `curl`, `jq`, `python3`. Free ports 5433 (Postgres) and 8333 (local S3).
No Go or Node needed: builds and SDK runs happen in containers. The `stellar` CLI (the XDR decoder) is downloaded on first
use by `corpus/tools/get-stellar-cli.sh` (pinned 30.0.1, Linux x86_64, sha256-checked); set `STELLAR=<path>` to use your own.

## Layout

```
README.md                   this overview
infra/                      shared local infrastructure
  docker-compose.yml          SeaweedFS (local S3), TimescaleDB, Galexie exporter, optional local quickstart network
  galexie/testnet.toml        Galexie config: testnet → s3://ledgers/testnet, 1 ledger per file
corpus/                     the corpus (RFP: schema + sample entries)
  README.md                   what an entry is, how entries are made, what is validated
  manifest.schema.json        draft manifest schema (JSON Schema 2020-12)
  manifest.json               generated manifest, 3 entries
  entries/cap-00NN/<id>/      ledger files, extracted target tx, generation record
  gen/                        generators (Go, run in a container) + records of runs on a local network
  tools/                      roundtrip.sh, build-manifest.py, regen-entry.sh, get-stellar-cli.sh
sighting-729/               wallet-backend #729 reproduction (RFP: one sighting)
  README.md                   the bug, the result, every step of the script
  repro.sh                    end-to-end run with verdicts; plus env.sh, seed-backfill.sql, query.sql, strkey.py,
                              build-wallet-backend.sh
  evidence/                   logs, SQL output, decoded ledger, asciinema recording
conformance-1766/           js-stellar-sdk #1766 on corpus entry 3
  README.md, run.sh, check.mjs, evidence/
docs/rfp-09.md              our summary of the RFP
.github/workflows/          CI: corpus checks + the #1766 run, on every push
```

## Glossary

| Term | Meaning |
| --- | --- |
| `LedgerCloseMeta` | everything that happened in one ledger: transactions, results, and every ledger entry they changed. Indexers read this. `LedgerCloseMetaBatch` is a list of them, the unit Galexie writes. |
| Galexie, data lake | SDF's exporter: it replays ledgers with a bundled stellar-core and writes `LedgerCloseMetaBatch` files to S3/GCS in a fixed layout (the *data lake*). wallet-backend can read that layout directly. |
| history archive, checkpoint | files every validator publishes every 64 ledgers (a checkpoint) with the full state and all transactions. Galexie's core rebuilds ledgers from them. |
| strkey | the text form of keys and addresses: `G…` account, `M…` muxed account, `P…` signed-payload signer, `C…` contract, `L…` pool (SEP-23) |
| muxed account | a `G…` account plus a 64-bit ID, written `M…` (CAP-27); 40 bytes instead of 32 |
| signed-payload signer | a signer key that is an ed25519 key plus a payload, written `P…` (CAP-40); 40–100 bytes |
| SAC | Stellar Asset Contract: the built-in Soroban token contract for a classic asset, here native XLM |
| mined / generated / synthetic | found in network history / produced by submitting our transactions to a network / hand-crafted bytes |

This repository is public from day one and contains no credentials. The only keys are the dummy local S3 keys `test`/`test`.
