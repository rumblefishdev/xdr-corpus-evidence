# Corpus: 3 sample entries and a draft manifest schema

RFP 9 asks for a corpus of **rare transaction shapes** that indexers and SDKs tend to mishandle, one or more per CAP, each with
a manifest of the values a correct reader must decode. This folder holds three sample entries and the draft manifest schema.

## What an entry is

An entry is **real ledger data emitted by stellar-core**, not hand-made bytes. Each entry directory under `entries/` holds:

| File | What |
| --- | --- |
| `FF…--<ledger>.xdr.zst` | the ledger exactly as Galexie wrote it to the data lake (zstd-compressed `LedgerCloseMetaBatch`) |
| `ledger-<ledger>.LedgerCloseMetaBatch.xdr` | the same, decompressed: the ledger's transactions, results and every ledger change |
| `tx-<hash>.TransactionEnvelope.xdr` | the target transaction alone, extracted from the ledger. Readers that can't decode the ledger's meta (e.g. SDKs without protocol-30 support) can still be tested on the pattern itself. |
| `generation.json` | generated entries only: what the generator did, i.e. public keys, every submission, and core's answer to each (no secrets) |

[`manifest.json`](manifest.json) describes every entry. **It is generated** by [`tools/build-manifest.py`](tools/build-manifest.py)
from the files. Every value under `expected` and `extra` comes from decoding the files. The only hand-written inputs are which
transaction and operation an entry is about, the CAP/SEP tags, the protocol a CAP introduced, and issue links.

## The three entries

| Entry | Pattern | How it was made | Ledger (protocol) | Bug it targets | Shown? |
| --- | --- | --- | --- | --- | --- |
| [`cap0040-signed-payload-signer-01`](entries/cap-0040/cap0040-signed-payload-signer-01/) | `set_options` adds a `P…` signed-payload signer: a 40-byte key where readers assume 32 | **mined**: found in testnet history | 4437357 (28) | wallet-backend [#729](https://github.com/stellar/wallet-backend/pull/729) | **yes**, [`sighting-729/`](../sighting-729/README.md) |
| [`cap0027-muxed-sac-destination-01`](entries/cap-0027/cap0027-muxed-sac-destination-01/) | a native-XLM token-contract (SAC) `transfer` to a **muxed** `M…` recipient: a 40-byte address, plus the event's `to_muxed_id` field | **generated** on testnet | 5708671 (30) | wallet-backend [#716](https://github.com/stellar/wallet-backend/issues/716) | no, future harness work |
| [`lp-issuer-order-01`](entries/cap-0038/lp-issuer-order-01/) | a liquidity-pool trustline whose two `LPO` issuers sort one way as raw key bytes (the network's rule) and the other way as `G…` text | **generated** on testnet | 5708628 (30) | js-stellar-sdk [#1766](https://github.com/stellar/js-stellar-sdk/pull/1766) | **yes**, [`conformance-1766/`](../conformance-1766/README.md) |

**Muxed account:** one normal account `G…` plus a 64-bit ID, written `M…` (CAP-27). Exchanges use it to give each customer
a deposit address while holding one account. On the wire the address is 40 bytes, not 32.

## How the generated entries were made

```
 gen/ (Go program)  ──submit txs──►  testnet validators  ──ledger──►  history archive  ──►  Galexie  ──►  entry files  ──►  manifest
   new random accounts            accept or reject each tx        (checkpoint every 64)      export      (+ generation.json)   build-manifest.py
```

1. **The generator** ([`gen/main.go`](gen/main.go), run in a Go container by [`gen/run.sh`](gen/run.sh)):
   1. creates fresh accounts funded by testnet friendbot;
   2. builds the transactions with go-stellar-sdk and submits them through Stellar RPC.

   It submits the valid pattern **and** the nearby invalid variants, and records core's answer to each in `generation.json`:
   - **`lp-issuer-order`:**
     1. grinds two random issuers until their byte order and their strkey text order disagree (5–8 tries);
     2. creates the issuers and a trustor;
     3. trusts both `LPO` assets and the pool share, in one transaction.

     It first submits the same transaction with the pool's assets in *text* order, which core must reject.
   - **`muxed-soroban`:** tries a Soroban transfer with a muxed **operation** source, then a muxed **transaction** source.
     Core rejects both. It then submits the valid pattern: a transfer **to** a muxed recipient. The recipient form came
     with CAP-67 in protocol 23.
2. **Export.** Once the history archive has a checkpoint past the target ledger, Galexie replays that ledger and writes it to
   the local S3, as described in [`sighting-729`](../sighting-729/README.md#what-the-script-does-step-by-step). The object is
   downloaded into the entry directory.
3. **Manifest.** `build-manifest.py` decodes the files and fills in the entry.

The generators also run against a local standalone network (`docker compose -f infra/docker-compose.yml --profile local up -d quickstart`).
The records are in [`gen/local-runs/`](gen/local-runs/). The export step is testnet-only for now.

## What is validated, and how

| # | Check | What it establishes | Done by |
| --- | --- | --- | --- |
| 1 | The target tx is in a ledger with `tx_success` | the shape is something the network accepts and emits | the network; recorded in the manifest (`core_validates`) |
| 2 | The invalid variants were rejected by core, with its error message | the pattern sits exactly on core's validation boundary | the network; recorded as `negative_probes` |
| 3 | sha256 of every file matches the manifest | the files are the captured ones | `tools/roundtrip.sh` |
| 4 | decode → encode of every file is **byte-exact** with rs-stellar-xdr (the `stellar` CLI's decoder) | a reference decoder reads and writes it losslessly. This is the RFP's reviewer check. | `tools/roundtrip.sh` |
| 5 | the `.zst` decompresses to the `.xdr`; the extracted envelope's hash is the tx hash; every envelope in the ledger hashes to a tx in its results | the files are consistent with each other | `tools/build-manifest.py` |
| 6 | the manifest equals a fresh decode of the files | no expected value was typed or edited by hand | `tools/build-manifest.py --check` |
| 7 | the manifest matches [`manifest.schema.json`](manifest.schema.json) | the draft schema is enforceable | `check-jsonschema` |
| 8 | regenerating an entry from scratch keeps the same pattern and core verdicts | the generator reliably produces the pattern | `tools/regen-entry.sh` |
| 9 | a real reader mishandles the entry before its fix and handles it after | the entry actually catches the bug it targets | [`sighting-729`](../sighting-729/) (entry 1), [`conformance-1766`](../conformance-1766/) (entry 3) |

Checks 3–7 run in CI on every push (`.github/workflows/corpus.yml`), and so does check 9 for entry 3.

**Not validated yet:** that wallet-backend mishandles entry 2 (#716). Doing that, for every entry and indexer, is the harness deliverable.
No stellar-core `--check-lcm` golden ledger files are published for these ledgers, so `check_lcm_golden` is `null`.

## Commands

```bash
corpus/tools/roundtrip.sh                             # checks 3-4 (offline, ~10 s)
python3 -I corpus/tools/build-manifest.py --check     # checks 5-6
corpus/tools/regen-entry.sh lp-issuer-order-01        # check 8 (testnet, 3-8 min)
corpus/tools/regen-entry.sh cap0027-muxed-sac-destination-01
```

Decoding protocol-30 ledgers needs stellar CLI ≥ 30. [`tools/get-stellar-cli.sh`](tools/get-stellar-cli.sh) downloads
the pinned 30.0.1 into `.cache/bin` and checks it against GitHub's sha256.

**A regenerated entry is never byte-identical to the committed one.** New accounts mean a new tx hash and ledger.
`regen-entry.sh` compares what must stay the same:
- the operation and its results;
- the pattern's decoded values, such as the muxed ID and the event's `to_muxed_id`, or the issuers' byte and text order;
- core's rejections of the invalid variants.

The field list is `COMMON_INVARIANTS` plus each entry's `invariants` in `tools/build-manifest.py`. The mined entry cannot
be regenerated, only re-exported: `sighting-729/repro.sh --clean` does that.

## Manifest schema: decisions in this draft

- **Two protocol numbers.** `protocol.introduced_in` is where the pattern can first occur (from the CAP). `protocol.ledger` is
  the protocol of the captured ledger (decoded). For example, the CAP-40 entry is a protocol-28 ledger.
- **A fixed `expected` core plus a per-pattern `extra`.** `expected` has the same 12 fields for every entry (ledger, tx, op,
  results, sources). `extra` holds the pattern's own values.
- **`synthetic` means hand-crafted bytes**, as in the RFP. All three entries were emitted by core on a real network, so they are
  `synthetic: false`. `source.kind` (`mined` / `generated` / `local` / `handcrafted`) records the origin, and the schema enforces
  `synthetic == (kind == handcrafted)`.
- **The captured file is canonical (sha256).** Re-exporting a ledger doesn't give identical bytes (see `sighting-729`, finding 5).
- **`derived_from`** marks files extracted from another file of the entry (the target envelope).

## Findings while building the entries

1. **A Soroban transaction with a muxed source can't be generated on any current network.** Since protocol 25, stellar-core rejects
   `invoke_host_function` with a muxed transaction or operation source, or with a memo (`TransactionFrame::validateSorobanMemo`,
   `tx_soroban_invalid`: "Soroban transactions are not allowed to use memo or muxed source account"). In current core, ledgers
   before protocol 25 apply this check only when transactions are queued, not in consensus. We haven't checked older core releases.
   wallet-backend #716's "most reachable" path, taking a Soroban participant from the operation source, can therefore only be
   met in pre-protocol-25 history, if at all. Entry 2 targets #716's other path, a muxed recipient, which is valid today.
   The RFP also asks for a *muxed source* pattern; that needs a classic-transaction entry, which isn't done yet.
2. **Core rejects the text-ordered pool** as `change_trust: malformed` and accepts the byte-ordered one. Random issuer pairs
   disagree on order often, so this isn't an exotic case.
3. **A fresh local network has no native-XLM token contract**, so the muxed generator deploys it first when it's missing.
