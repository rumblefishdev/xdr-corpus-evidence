# SDK conformance: every corpus entry through js-stellar-sdk

A first version of the RFP's **SDK conformance runner**, for one SDK (JavaScript) and four of its releases. It runs
**every** corpus entry through each release with the **same generic checks**. None of the checks knows about a particular
bug, and none was written for one entry. A defect shows up because an SDK disagrees with the network on data the network
emitted.

## Result

```
entry                               17.2.0       17.2.1       17.3.0       17.4.0
cap0040-signed-payload-signer-01    pass         pass         pass         pass
cap0027-muxed-sac-destination-01    pass         pass         pass         pass
lp-issuer-order-01                  mishandled   pass         pass         pass
cap0027-muxed-classic-payment-01    pass         pass         pass         pass
cap0046-scmap-key-collision-01      mishandled   mishandled   pass         pass
```

| Release | What changed | What the corpus shows |
| --- | --- | --- |
| 17.2.0 | last release before [#1766](https://github.com/stellar/js-stellar-sdk/pull/1766) | `lp-issuer-order-01`: `TransactionBuilder.fromXDR` **throws** "Assets are not in lexicographic order" on a transaction the network accepted. The SDK orders a pool's issuers by `G…` text; the network orders them by key bytes. |
| 17.2.1 | #1766 fixed | `lp-issuer-order-01` passes. `cap0046-scmap-key-collision-01` still fails: `scValToNative` returns **1 of 3** map entries, **with no error** ([#1789](https://github.com/stellar/js-stellar-sdk/issues/1789)). |
| 17.3.0 | #1789 and #1785 fixed | `scValToNative` refuses the map with an error instead of losing entries. Everything passes, and this release also decodes all five protocol-30 ledger files. |
| 17.4.0 | latest release on 2026-11-13 | everything passes; `run.sh` also requires the latest release to decode every entry's full ledger file |

Full output: [`evidence/run.txt`](evidence/run.txt); per release: `evidence/result-<version>.json`.

## The checks (`check.mjs`)

For each entry, inputs come only from the corpus: the target transaction envelope file and the manifest's expected values.

| # | Check | What it means for a user of the SDK |
| --- | --- | --- |
| 1 | `xdr-roundtrip`: decode the envelope and re-encode it, byte-identical | the raw XDR layer reads and writes it. If this fails, the verdict is `error`. |
| 2 | `tx-roundtrip`: `TransactionBuilder.fromXDR` parses it, the hash equals the manifest's, `toEnvelope()` re-encodes byte-identically | a wallet or indexer can load the transaction. **This is what catches #1766.** |
| 3 | `scval-maps`: every `scvMap` in the envelope through `scValToNative` keeps every entry, or fails with an error | contract data is not silently lost. **This is what catches #1789.** |
| 4 | `muxed`: every muxed account (`M…`), as a `MuxedAccount` or an `ScAddress`, renders to an address that decodes back to the same bytes and id | muxed ids survive, including ids above 2^53 and 2^64−1 |

All pass → `pass`; check 1 fails → `error`; any other failure → `mishandled`. Also reported, not part of the verdict:
whether the release decodes the entry's full ledger file (17.2.x cannot decode protocol-30 ledgers; 17.3.0 and later can,
and `run.sh` requires it of the latest release).
[`expected.json`](expected.json) lists the expected verdicts; `run.sh` fails on any difference.

## What these checks cannot catch: #1785

[#1785](https://github.com/stellar/js-stellar-sdk/issues/1785) (fixed in 17.3.0) is about **input the SDK accepts from
its user**: `MuxedAccount` took id strings such as `"0x10"`, `" 16 "` or `""`, and `manageSellOffer()` rounded number offer
ids above 2^53. Ledger data never contains these: the network only emits canonical ids. So no replayed corpus entry can
trigger #1785, and the runner does not claim it. Covering it needs a second kind of test vector, invalid inputs with the
error an SDK must give, which is a scope question for the runner deliverable, not for the corpus of ledger data.

The corpus does carry the related *valid* shapes (entry `cap0027-muxed-classic-payment-01`: muxed ids 2^63+1, 2^53+1 and
2^64−1), and check 4 confirms every release handles them.

## Run it

```bash
conformance/run.sh            # needs only Docker; ~30 s after the first run; results in .cache/conformance/
conformance/run.sh --record   # write the results to evidence/ instead
```

It installs each release with npm inside `node:22.12.0-bookworm-slim` (the SDK needs Node ≥ 22.12). CI runs it on every push.

## Relation to the RFP deliverable

The RFP's acceptance test for the runner is reproducing js-stellar-sdk #1766, #1785 and #1789 on pre-fix tags.
This prototype reproduces #1766 and #1789 with generic checks, and explains above why #1785 needs input vectors.
The deliverable extends the same corpus and checks to the Go and Rust SDKs.
