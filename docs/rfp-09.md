# RFP 9: XDR/CAP-Coverage Audit Tool and Indexer-Hardening Guide

> Source: https://stellar.gitbook.io/scf-handbook/scf-awards/build-award/rfp-track#current-open-rfps
> (collapsible section on the main page, no separate URL). Fetched 2026-11-13.
> Note: close paraphrase of the source; figures, requirement levels and URLs are exact.

## 1. Scope of Work
A shared test kit for applications that read the Stellar ledger: a library of rare-but-valid ledger data, a harness that replays it against an indexer and reports failures, a linter, and a guide for indexer authors. The same corpus doubles as a **conformance suite for the Stellar SDKs** that decode the ledger.

Deliverables:
- **Tooling — corpus + generator + replay harness:** corpus of rare ledger/transaction patterns, one entry per pattern, tagged with the CAP that introduced it; a generator; a replay harness reporting shapes an indexer mishandles or silently drops.
- **Tooling — conformance runner:** replays the corpus through at least the **JavaScript, Go and Rust SDKs**; reports where decoding/encoding diverges from the network and from each other.
- **Tooling — linter:** scoped to one language/framework, flags the three ingestion mistakes in section 3.
- **Documentation — indexer-hardening guide:** which tx fields stellar-core validates and which it doesn't, recommended depth/size caps, handling of muxed and signed-payload addresses.
- **Integration:** corpus run against at least one SDF pipeline (wallet-backend or stellar-etl). Each failure triaged (severity, affected CAP, minimal reproducing entry) and filed upstream. *The findings are the deliverable, not the run.*

### Deliverable specifications (input / output / reviewer check)
| Deliverable | Input | Output | Reviewer check |
|---|---|---|---|
| Corpus & generator | CAP list + network history or local stellar-core | XDR files + manifest (entry ID, CAP, protocol version, source, synthetic flag, expected decoded values) | Entries round-trip through rs-stellar-xdr and agree with `--check-lcm` goldens |
| Replay harness | Corpus + adapter | Per-entry pass / mishandled / dropped report | Reference adapter reproduces wallet-backend #729, #731, #716 on pre-fix commits and passes on fixed ones |
| Linter | Indexer source | Findings with rule IDs for the three mistakes | Flags seeded violations; runs clean on current wallet-backend |
| Guide | — | Fixed outline: validated/unvalidated fields, caps, participants-from-meta, muxed & signed-payload handling, checklist | Field-by-field table agrees with corpus manifest; publication requires review by a core/platform engineer |
| Conformance runner | Corpus | Divergence matrix across JS, Go, Rust SDKs | Reproduces js-stellar-sdk #1766, #1785, #1789 on pre-fix tags |
| Findings reports | — | One entry per finding (issue link, corpus entry, severity, component) | Every failure maps to a filed issue |

## 2. Background and Context
Every application reading the ledger must decode XDR. Rare transaction shapes crash readers because nobody tests them. Two guiding principles:
- stellar-core **validates some fields** (signatures, sequence numbers, fees, time bounds) and **passes others through untouched** (memos, Soroban authorization trees, muxed account IDs). An indexer trusting an unvalidated field can be crashed or fed bad data.
- An indexer should **derive affected accounts from ledger meta** (what core changed), not from the envelope (what the submitter claimed).

Cited evidence:
- Wallet-backend ingestion halted by three rare patterns: CAP-0040 signed-payload signers, unbounded authorization trees, and SAC trustlines producing no trustline state change (CAP-0073).
- stellar-etl opened a Protocol 28 epic over ~50 correctness issues.
- Muxed and signed-payload addresses were truncated in wallet-backend.
- JS SDK rejected a valid liquidity-pool trustline op because it ordered asset issuers by string form instead of XDR bytes. Go SDK had already fixed it — no sweep carried the fix across.
- A JS SDK audit found 14 decode/validation defects in two weeks: muxed account & offer IDs decoding to a different ID than they encode, map entries silently dropped on key collisions, union discriminants accepting floats.

## 3. Requirements

### Functional
- Corpus covers rare patterns introduced/changed by each CAP. Every entry tagged with CAP number and protocol version. Corpus can grow as new CAPs ship.
- Entries must be XDR that stellar-core would actually emit — from testnet/mainnet history or a local stellar-core. Hand-crafted entries marked **synthetic** in the manifest. Generation reproducible for both testnet and mainnet passphrases.
- Replay harness feeds corpus into a target indexer through a documented adapter interface and reports every mishandled/dropped shape. Delivers ledgers through an input existing pipelines consume — e.g. **Galexie data lake layout** or a **mock RPC `getLedgers`** endpoint. Proposal must say which is supported first.
- Linter checks indexer source (agreed language) for three mistakes:
  1. Trusting unvalidated fields
  2. Missing depth/size caps on recursive structures (auth trees, nested ScVals)
  3. Deriving participants from the envelope instead of meta

  The guide carries the same checks as a checklist for indexers in other languages.
- Guide covers, field by field: which fields core validates; recommended caps; participants-from-meta rule; muxed & signed-payload handling incl. SEP-0023 strkey forms.
- Corpus is language-neutral: XDR files + manifest any pipeline can read. Generator may build on Rust or Go XDR libraries. Must agree with stellar-core's golden LedgerCloseMeta where published (`--check-lcm` mode).
- Each entry carries expected decoded values. An SDK must decode the entry and re-encode to the original bytes. Corpus must include: asset pairs whose issuers sort differently by strkey vs XDR bytes; muxed source and recipient accounts; muxed account & offer IDs that decode to a different ID than they encode; vectors at maximum length; nested authorization entries; maps whose keys collide after conversion.
- Conformance runner executes the corpus through at least JS, Go and Rust SDKs **in CI**. Reports every divergence from the network or from other SDKs, and **files a sibling issue in each affected SDK** when one SDK has a defect.

### Non-functional
- Generator and harness run **offline**, need no mainnet keys/funds, fast enough for CI on every PR.
- Harness handles full corpus without unbounded memory growth (one of the failures it tests for).
- Open source, permissive license, developed in public with regular status updates.
- Built on most recent stable Stellar stack, with a plan for protocol upgrades.
- Documented post-launch maintenance plan (who maintains, for how long).
- Clear documentation of infrastructure and any (minimal) user tracking.

## 4. Evaluation Criteria
- Technical capability and depth of design, with evidence of comparable shipped developer tooling
- Relevant experience, with open-source repos where possible
- Security and audit history, including disclosure handling
- Ecosystem alignment, with limits to scope explained
- Ability to deliver within timeline, with testable milestones
- Coherent integration plan and post-launch maintenance
- Open-source licensing and public building, with status updates
- Familiarity with Stellar XDR, CAPs, LedgerCloseMeta, and at least one pipeline (Horizon, stellar-etl, wallet-backend, or a third-party indexer)
- **Reviewability:** proposal must include a **draft manifest schema**, **2–3 sample corpus entries**, and **evidence of reproducing one sighting from section 7**. "Proposals without these are incomplete."

## 5. Expected Deliverables
- Corpus & generator repo with per-CAP coverage manifest
- Replay harness with documented adapter interface + one reference adapter
- Linter for the agreed language + the guide's checklist
- Indexer-hardening guide, ready to publish on developers.stellar.org
- Test suite and CI configuration for all tooling
- Findings report from ≥ 1 SDF pipeline, every finding filed upstream
- Conformance runner for ≥ 3 SDKs, divergence findings report, sibling issues filed in each affected SDK
- Production release + maintenance plan covering the first protocol upgrade after delivery

## 6. Budget and Funding Structure *(internal review purposes only)*
- Range: **$50,000 – $75,000**, working estimate **$65,000**. Comparable: State Optimization CLI ($70K) — treat as ceiling, not anchor. Corpus is the main cost driver.
- Milestones:
  1. **$25K** — indexer-hardening guide + corpus & generator with per-CAP coverage manifest
  2. **$25K** — replay harness, documented adapter interface, one reference adapter (Galexie layout or mock getLedgers), SDK conformance runner, findings reports against an SDF pipeline and the three SDKs, issues filed upstream
  3. **$7.5K** — linter + self-audit checklist
  4. **$7.5K** — production release with Protocol 30 coverage, CI, maintenance plan through first protocol upgrade
- If the linter is cut (guide checklist replaces it), total falls to ~**$57.5K**.
- SDK conformance runner added **2026-11-02** (Airtable OPP-101), absorbed into milestone 2 with no change to total. Acceptance test: reproducing the three js-stellar-sdk issues from section 1.
- Tranches released on acceptance of each milestone (section 1 checks pass). Each later tranche must be submitted within **90 days** of the previous payment (SCF Build Award rules).

## 7. Supporting Signals (public sightings, 2026-09-03 → 2026-10-16)
- 2026-09-03 — stellar-core PR #5435: adds `--check-lcm` golden LedgerCloseMeta verification in CI — https://github.com/stellar/stellar-core/pull/5435
- 2026-09-04 — wallet-backend issue #731: SAC-created trustlines produce no state change (CAP-0073) — https://github.com/stellar/wallet-backend/issues/731
- 2026-09-04 — wallet-backend PR #729: CAP-40 signed-payload signers in ingestion — https://github.com/stellar/wallet-backend/pull/729
- 2026-09-05 — henyey issue #3941: validator dies during catchup on bucket-entry XDR parse failure — https://github.com/stellar-experimental/henyey/issues/3941
- 2026-09-10 — rs-stellar-xdr PR #581: limits capacity reserved when decoding VecM — https://github.com/stellar/rs-stellar-xdr/pull/581
- 2026-09-11 — stellar-etl issue #454: ~50 data-quality issues from one audit sweep — https://github.com/stellar/stellar-etl/issues/454
- 2026-09-15 — stellar-etl issue #470: Protocol 28 Data Team epic — https://github.com/stellar/stellar-etl/issues/470
- 2026-09-18 — wallet-backend issue #716: AddressBytea truncates muxed & signed-payload addresses — https://github.com/stellar/wallet-backend/issues/716
- 2026-09-18 — stellar-etl issue #460: P23+ event/effect gaps from old SDK pin (CAP-0073) — https://github.com/stellar/stellar-etl/issues/460
- 2026-09-25 — wallet-backend PR #753: four indexer fixes from an ingestion-halt audit — https://github.com/stellar/wallet-backend/pull/753
- 2026-09-28 — rs-soroban-sdk PR #2099: MuxedAddress panics on a non-address ScVal — https://github.com/stellar/rs-soroban-sdk/pull/2099
- 2026-09-29 — rs-stellar-xdr PR #598: enforces VecM max length on serde deserialize — https://github.com/stellar/rs-stellar-xdr/pull/598
- 2026-09-30 — js-stellar-sdk PR #1766: orders asset issuers and ScVal map keys like the network — https://github.com/stellar/js-stellar-sdk/pull/1766
- 2026-10-06 — rs-stellar-xdr issue #604: xdr-json schemas omit types behind XDR feature flags — https://github.com/stellar/rs-stellar-xdr/issues/604
- 2026-10-07 — js-stellar-sdk issue #1789: scValToNative drops colliding map entries — https://github.com/stellar/js-stellar-sdk/issues/1789
- 2026-10-08 — js-stellar-sdk issue #1785: muxed account & offer IDs accept values that encode a different ID — https://github.com/stellar/js-stellar-sdk/issues/1785
- 2026-10-16 — js-xdr issue #165: nine XDR validation issues opened & fixed in one audit pass — https://github.com/stellar/js-xdr/issues/165
- 2026-10-16 — js-stellar-sdk issue #1823: XDR string arms mangle non-UTF-8 bytes and lone surrogates — https://github.com/stellar/js-stellar-sdk/issues/1823
