# js-stellar-sdk #1766 on corpus entry `lp-issuer-order-01`

**The bug** ([stellar/js-stellar-sdk#1766](https://github.com/stellar/js-stellar-sdk/pull/1766)): a liquidity pool's two
assets must be in a fixed order. The network orders same-code assets by the issuers' **raw key bytes**. The SDK compared the
issuers' **`G…` strkey text** instead. Strkeys are base32: the digits `2`–`7` stand for values *after* `A`–`Z`, but sort
*before* them as text. So for some issuer pairs the SDK's order is reversed, and it rejects pools the network accepts.

**Result: reproduced.** The same corpus entry is run through the release before the fix and the release with it
(`v17.2.0...v17.2.1` contains #1766 and one docs commit), and through the latest release, 17.4.0:

| Check | 17.2.0 (pre-fix) | 17.2.1 (fixed) | 17.4.0 (latest) |
| --- | --- | --- | --- |
| XDR: decode + re-encode the target transaction byte-exact | ok | ok | ok |
| `TransactionBuilder.fromXDR` parses the on-chain transaction | **throws** "Assets are not in lexicographic order" | ok; hash and pool pair match | ok |
| `Asset.compare(A, B)` | **`1`** (network order is `-1`) | `-1` | `-1` |
| `new LiquidityPoolAsset(A, B, 30)` | **throws** | accepted | accepted |
| `getLiquidityPoolId(A, B, 30)` = the network's pool ID | **throws** | `dd709f02…1550`, equal | equal |
| **Verdict** | **`mishandled`** | **`pass`** | **`pass`** |
| *info:* decode the full protocol-30 ledger file | fails | fails | ok, byte-exact |

The second row is the most practical consequence: **before the fix, the SDK can't even load a valid transaction from the
ledger.** `TransactionBuilder.fromXDR` throws on any transaction that touches such a pool.

## How this relates to the corpus

This is a first version of the RFP's **SDK conformance runner** for one SDK and one entry. It reads the entry's files and
manifest, which were built without any SDK, runs them through the SDK, and compares the results with what the network did.
The runner deliverable does this for every entry across JS, Go and Rust SDKs.

## Run it

```bash
conformance-1766/run.sh    # needs only Docker; ~30 s after the first run
```

It installs the three SDK versions with npm inside `node:22.12.0-bookworm-slim` (the SDK needs Node ≥ 22.12) and runs
[`check.mjs`](check.mjs) with each. It exits 0 only if 17.2.0 = `mishandled`, 17.2.1 = `pass`, and 17.4.0 = `pass` with the
full ledger decoding. CI runs it on every push.

## What `check.mjs` does

Inputs come only from the corpus: the transaction envelope file
`corpus/entries/cap-0038/lp-issuer-order-01/tx-d765c2492db224c5.TransactionEnvelope.xdr` and the manifest entry. Nothing about the
pool is typed in the script.

1. **XDR layer:** decode the envelope and re-encode it byte-exactly. If this fails, the verdict is `error`.
2. **Transaction layer:** `TransactionBuilder.fromXDR` must parse the transaction, its hash must equal the manifest's tx hash, and
   operation 5's pool share must be the manifest's asset pair, in network order.
3. **Asset layer:** build both assets from the manifest's decoded issuers, then check `Asset.compare`, `new LiquidityPoolAsset`
   and `getLiquidityPoolId` against the network's order and pool ID. These checks run even when step 2 throws.

All pass → `pass`; any failure after step 1 → `mishandled`. Results: [`evidence/`](evidence/) (`result-<version>.json`, `run.txt`).

## Side finding: the pre-fix and fixed releases cannot decode protocol-30 ledgers

17.2.0 and 17.2.1 fail to decode the entry's full ledger file (`LedgerCloseMetaBatch`, protocol 30):
`ledgerHeader.header.scpValue.ext.v: unknown enum value 3`. Releases from 17.3.0 on decode it and re-encode it byte-exactly
(17.4.0 is checked on every run). This is reported as `info`, not as part of the verdict.

It matters for testing bugs that were fixed in the past: the release just before a fix is often too old to read ledgers
from today's protocol. That is why the corpus also stores each target transaction's envelope on its own. Without it, #1766
could not be tested here at all, because the pre-fix SDK can't open the ledger file.
