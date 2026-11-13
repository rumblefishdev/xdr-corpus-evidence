# Corpus (draft)

Three sample entries and a draft manifest schema, as required by RFP 9 section 4 ("Reviewability").

- [`manifest.schema.json`](manifest.schema.json): JSON Schema (2020-12) for the manifest.
- [`manifest.json`](manifest.json): the manifest. It is **generated** by [`scripts/build-manifest.py`](../scripts/build-manifest.py)
  from the committed ledger files. Every value under `expected` and `extra` comes from `stellar xdr decode`. The hand-written
  inputs are only which tx and operation an entry is about, CAP/SEP tags, `protocol.introduced_in`, and issue links.

| Entry | Pattern | Source | Ledger (protocol) | Targets |
| --- | --- | --- | --- | --- |
| [`cap0040-signed-payload-signer-01`](cap-0040/cap0040-signed-payload-signer-01/) | `set_options` adds a `P…` signed-payload signer (40-byte key payload) | mined, testnet | 4437357 (28) | wallet-backend [#729](https://github.com/stellar/wallet-backend/pull/729), reproduced in [`evidence/729`](../evidence/729/README.md) |
| [`cap0027-muxed-sac-destination-01`](cap-0027/cap0027-muxed-sac-destination-01/) | native SAC `transfer` to a muxed-account `ScAddress` (`M…` recipient); event carries `to_muxed_id` | generated, testnet | 5708671 (30) | wallet-backend [#716](https://github.com/stellar/wallet-backend/issues/716) (producer 2) |
| [`lp-issuer-order-01`](cap-0038/lp-issuer-order-01/) | `change_trust` on a pool share whose two `LPO` issuers sort one way as key bytes and the other as strkey text | generated, testnet | 5708628 (30) | js-stellar-sdk [#1766](https://github.com/stellar/js-stellar-sdk/pull/1766) |

Each entry directory holds the Galexie object exactly as exported (`FF…--<ledger>.xdr.zst`), the decompressed
`LedgerCloseMetaBatch` (`ledger-<n>.LedgerCloseMetaBatch.xdr`), and, for generated entries, the generator's `generation.json`.
That file lists public keys, every submission and every rejection, never secrets.

## Checks

```bash
scripts/roundtrip.sh                      # sha256 + decode -> encode byte-exact for every file in the manifest
python3 -I scripts/build-manifest.py --check   # manifest matches what the files decode to
```

Both run in CI (`.github/workflows/corpus.yml`), together with schema validation. Decoding protocol-30 ledgers needs
stellar CLI ≥ 30; [`scripts/get-stellar-cli.sh`](../scripts/get-stellar-cli.sh) fetches the pinned 30.0.1, checked against GitHub's sha256.

## Generators

`gen/` is a Go program (go-stellar-sdk v0.9.0) run in a `golang` container by [`gen/run.sh`](../gen/run.sh). It runs on
testnet by default. Set `RPC_URL`, `FRIENDBOT_URL` and `NETWORK_PASSPHRASE` to target a local quickstart (`docker compose --profile local up -d quickstart`).
Both generators were run on testnet and on a local standalone network; the local records are in
[`evidence/gen-local/`](../evidence/gen-local/).

## Schema decisions in this draft

- **`protocol.introduced_in` *and* `protocol.ledger`.** The first is the protocol where the pattern can first occur (from the CAP).
  The second is the protocol of the captured ledger (decoded). The harness needs both, for example the CAP-40 entry is a protocol-28 ledger.
- **Fixed `expected` core plus a per-pattern `extra`.** `expected` has the same 12 fields for every entry: ledger, tx, op,
  results and sources. `extra` holds the pattern's own decoded values, such as the signer key and payload, the muxed id and the event's
  `to_muxed_id`, or the pool's issuer order and ID.
- **`synthetic` means hand-crafted bytes** (RFP wording). Generated entries were emitted by core on a real network, so they
  are `synthetic: false`; `source.kind` (`mined` / `generated` / `local` / `handcrafted`) records the origin. The schema
  enforces `synthetic == (kind == handcrafted)`.
- **`negative_probes`** records variants core rejected, with core's result and diagnostic message. They show the pattern
  sits exactly at the validation boundary.
- **The captured file is canonical (sha256).** Re-exporting the same ledger is not byte-identical: Soroban txs carry wall-clock
  `core_metrics` diagnostic events, and ledger-entry changes can come out in a different order (see `evidence/729`).

## Findings while building the entries

1. **A muxed source on a Soroban tx cannot be generated on any current network.** Since protocol 25 stellar-core rejects
   `invoke_host_function` with a muxed tx source or op source, or a memo (`TransactionFrame::validateSorobanMemo`,
   `tx_soroban_invalid`, "Soroban transactions are not allowed to use memo or muxed source account"). In current core, ledgers
   before protocol 25 apply the same check only in the transaction queue (`TransactionQueue.cpp`), not in consensus.
   We have not checked what older core releases did. wallet-backend #716's "most reachable" producer 1 (Soroban op
   participant from `op.SourceAccount()`) can therefore only be met in **pre-protocol-25 history**, if at all.
   Mining for it is a follow-up.
   The entry instead targets #716's producer 2 (muxed SAC recipient), which is valid today. Both rejected variants are recorded as
   `negative_probes`.
2. **The text-ordered pool is rejected by core** as `change_trust: malformed` (op 6 of the probe tx), and the byte-ordered one is
   accepted. Random issuer pairs disagree on order often (5–8 tries), so this is not an exotic case.
3. **A fresh local network has no native SAC**, so the muxed generator deploys it first when it is missing.
