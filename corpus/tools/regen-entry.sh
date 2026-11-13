#!/usr/bin/env bash
# Regenerate a generated corpus entry from scratch on testnet and check it shows the same pattern
# and the same core verdicts as the committed entry.
#
# Usage: corpus/tools/regen-entry.sh <entry-id> [out-dir]
#   entry-id: cap0027-muxed-sac-destination-01 | lp-issuer-order-01
#   out-dir:  default .cache/regen/<entry-id>-<UTC timestamp>
#
# Steps: run the generator (new random accounts) -> wait until the testnet history archive has a
# checkpoint at or past the target ledger -> export it with Galexie into the local S3 -> download,
# decompress, round-trip -> compare with the committed entry (corpus/tools/build-manifest.py --regen-check).
#
# A regenerated entry is never byte-identical to the committed one: accounts, tx hash and ledger are
# new on every run. What must match are the fields listed in build-manifest.py (COMMON_INVARIANTS and
# each entry's "invariants"): op type and results, the pattern's decoded values, and core's rejections.
# Takes ~3-8 minutes, mostly waiting for the history checkpoint (every 64 ledgers, ~5.5 minutes).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"

ID=${1:?usage: corpus/tools/regen-entry.sh <entry-id> [out-dir]}
case $ID in
  cap0027-muxed-sac-destination-01) GEN=muxed-soroban ;;
  lp-issuer-order-01) GEN=lp-issuer-order ;;
  cap0040-signed-payload-signer-01)
    echo "$ID is mined, not generated: re-export it with sighting-729/repro.sh (it checks the ledger header and tx)" >&2
    exit 2 ;;
  *) echo "unknown entry $ID" >&2; exit 2 ;;
esac
OUT=${2:-.cache/regen/$ID-$(date -u +%Y%m%dT%H%M%SZ)}
ARCHIVE=https://history.stellar.org/prd/core-testnet/core_testnet_001
STELLAR=${STELLAR:-$(corpus/tools/get-stellar-cli.sh | tail -1)}
export STELLAR
compose() { docker compose -f "$ROOT/infra/docker-compose.yml" "$@"; }
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

# Testnet only: Galexie's built-in "testnet" config is used for the export.
unset RPC_URL FRIENDBOT_URL NETWORK_PASSPHRASE
mkdir -p "$OUT"

step "Starting the local S3 store"
compose up -d --wait s3

step "Running generator '$GEN' on testnet"
corpus/gen/run.sh "$GEN" > "$OUT/generation.json"
L=$(jq -r .target_ledger "$OUT/generation.json")
echo "target tx $(jq -r .target_tx_hash "$OUT/generation.json") in ledger $L"

step "Waiting for a history checkpoint at or past ledger $L"
# Galexie's captive core can only replay up to the latest published checkpoint, and needs end > start,
# so the export range is [L-1, L].
for i in $(seq 1 90); do
  tip=$(curl -sf "$ARCHIVE/.well-known/stellar-history.json" | jq -r .currentLedger || echo 0)
  [ "$tip" -ge "$L" ] && break
  [ $((i % 6)) = 1 ] && echo "archive at $tip, need $L"
  sleep 10
done
[ "$tip" -ge "$L" ] || { echo "archive did not reach ledger $L in 15 minutes" >&2; exit 1; }
echo "archive at $tip"

step "Exporting ledgers $((L - 1))..$L with Galexie"
compose run --rm galexie scan-and-fill --start $((L - 1)) --end "$L" \
  --config-file /config/testnet.toml > "$OUT/galexie-export.log" 2>&1 ||
  { tail -20 "$OUT/galexie-export.log"; exit 1; }
sed -i -E 's/(Root account seed: )S[A-Z2-7]{55}/\1<redacted>/' "$OUT/galexie-export.log"

# Object key for ledgers_per_file=1, files_per_partition=64000 (infra/galexie/testnet.toml).
P=$((L / 64000 * 64000))
KEY=$(printf '%08X--%d-%d/%08X--%d.xdr.zst' $((0xFFFFFFFF - P)) "$P" $((P + 63999)) $((0xFFFFFFFF - L)) "$L")
curl -sf -o "$OUT/$(basename "$KEY")" "http://localhost:8333/ledgers/testnet/$KEY"
zstd -qdf "$OUT/$(basename "$KEY")" -o "$OUT/ledger-$L.LedgerCloseMetaBatch.xdr"
echo "downloaded $KEY"

step "Comparing with the committed entry"
python3 -I corpus/tools/build-manifest.py --regen-check "$ID" "$OUT"
echo "files and the regenerated manifest entry are in $OUT"
