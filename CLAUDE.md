# xdr-corpus-evidence

Evidence repo for a proposal to **SCF RFP 9: XDR/CAP-Coverage Audit Tool and Indexer-Hardening Guide**
(summary: `docs/rfp-09.md`; original: https://stellar.gitbook.io/scf-handbook/scf-awards/build-award/rfp-track).
RFP section 4 ("Reviewability") calls a proposal incomplete without a draft manifest schema, 2–3 sample corpus entries and
evidence of reproducing one sighting from section 7. `README.md` maps each folder to those items; start there.

## Rules

- The repo is **public**. Never commit secrets, keys or credentials. The only keys used are the dummy local S3 keys `test`/`test`.
  Generators use fresh random keypairs that live only in memory; `generation.json` holds public keys only.
- `corpus/manifest.json` is **generated** by `corpus/tools/build-manifest.py` from the entry files. Never edit it by hand.
  Hand-written inputs (selectors, CAP/SEP tags, links, invariants) live in `ENTRIES` in that script.
- Every entry is XDR that stellar-core emitted (mined from history or generated on testnet); none is hand-crafted.
- Scripts write their run output to `.cache/` (git-ignored). Only `--record` overwrites committed evidence.
- No local Go, Rust or Node is needed: builds and SDK runs happen in Docker containers.

## Checks

```bash
corpus/tools/roundtrip.sh                              # hashes + byte-exact decode/encode (offline, seconds)
python3 -I corpus/tools/build-manifest.py --check      # manifest = fresh decode of the files
conformance/run.sh                                     # every entry through js-stellar-sdk 17.2.0 / 17.2.1 / 17.3.0 / 17.4.0
sighting-729/repro.sh --clean --from-corpus            # wallet-backend #729 pre-fix vs fixed
corpus/tools/regen-entry.sh <entry-id>                 # regenerate a generated entry on testnet and compare
python3 -I corpus/tools/coverage-targets.py            # XDR union arms / optionals vs the corpus (prototype)
```

Ports 8333 (local S3) and 5433 (Postgres) can be changed with `XDR_S3_PORT` / `XDR_PG_PORT`.
Protocol-30 ledgers need stellar CLI ≥ 30; `corpus/tools/get-stellar-cli.sh` fetches the pinned build.

## Known facts worth keeping in mind

- Galexie re-exports of a ledger are not byte-identical (Soroban diagnostic timings); the committed file is canonical (sha256).
- wallet-backend backfill exits 0 even when a batch fails; verdicts come from the database and the log, never the exit code.
- Since protocol 25, core rejects a muxed source or a memo on Soroban transactions; muxed-source entries are classic.
