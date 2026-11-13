#!/usr/bin/env bash
# Reproduce wallet-backend #729: a CAP-40 signed-payload signer (P... strkey) added by set_options
# in testnet ledger 4437357 cannot be persisted by the pre-fix commit; the fix stores it.
#
# Usage: scripts/repro-729.sh [--from-corpus] [--clean]
#   --from-corpus  upload the committed ledger file to the local S3 instead of exporting it with
#                  Galexie (no testnet history archive needed; survives a testnet reset)
#   --clean        remove the local S3/DB containers and volumes first
#
# Verdict per binary (same shape as the future harness):
#   pass        the tx is persisted and its SIGNER state change holds the full P... key
#   mishandled  the tx is persisted but the signer is missing or altered
#   dropped     the tx is not persisted
# The script exits 0 only if pre-fix = dropped and fixed = pass.
# Note: wallet-backend exits 0 even when a backfill batch fails, so verdicts come from the DB.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

PREFIX_COMMIT=2a0a84ea12523b6c92e278c0c84b443041240095
FIXED_COMMIT=2c873353c1f0f3c9b145e25a7d009023ad104a1c
LEDGER=4437357
TX_HASH=d9f78749cd01297cd5f22c7f2c798c9eec93d56f399e1c4cc2f8977ca52b9fed
EXPECTED_SIGNER=PBCK2RYYLIYOSHBEL2FQLAVRSHRH3GP3ID476SGKBV7BEZHMQ6NSAAAAAAB55LN6AANMS
EXPECTED_ACCOUNT=GBCK2RYYLIYOSHBEL2FQLAVRSHRH3GP3ID476SGKBV7BEZHMQ6NSBB7B
CORPUS_DIR=corpus/cap-0040/cap0040-signed-payload-signer-01
OBJECT_KEY=testnet/FFBC9DFF--4416000-4479999/FFBC4A92--4437357.xdr.zst
OUT=evidence/729
PG="psql -h localhost -p 5433 -U postgres -v ON_ERROR_STOP=1"
export PGCONNECT_TIMEOUT=5

FROM_CORPUS=0
CLEAN=0
for a in "$@"; do
  case $a in
    --from-corpus) FROM_CORPUS=1 ;;
    --clean) CLEAN=1 ;;
    *) echo "unknown argument: $a" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
mkdir -p "$OUT" bin

# --- 1. local S3 + database -------------------------------------------------------------------
if [ $CLEAN = 1 ]; then
  step "Removing old containers and volumes"
  docker compose down -v --remove-orphans
fi
step "Starting SeaweedFS (S3 :8333) and TimescaleDB (:5433)"
docker compose up -d --wait s3 db

# --- 2. put ledger 4437357 in the data lake ---------------------------------------------------
if [ $FROM_CORPUS = 1 ]; then
  step "Uploading the committed ledger file to s3://ledgers/$OBJECT_KEY"
  curl -sf -X PUT --data-binary "@$CORPUS_DIR/FFBC4A92--4437357.xdr.zst" \
    "http://localhost:8333/ledgers/$OBJECT_KEY" >/dev/null
  # wallet-backend gets the schema from env, but publish the manifest Galexie would write too
  printf '%s' '{"networkPassphrase":"Test SDF Network ; September 2015","version":"1.0","compression":"zstd","ledgersPerBatch":1,"batchesPerPartition":64000}' |
    curl -sf -X PUT --data-binary @- "http://localhost:8333/ledgers/testnet/.config.json" >/dev/null
else
  step "Exporting ledgers $LEDGER..$((LEDGER + 1)) with Galexie (captive core catches up from the testnet archive)"
  # Galexie requires end > start; 4437358 is exported too and is harmless.
  docker compose run --rm galexie scan-and-fill --start $LEDGER --end $((LEDGER + 1)) \
    --config-file /config/testnet.toml > "$OUT/galexie-export.log" 2>&1 ||
    { tail -20 "$OUT/galexie-export.log"; exit 1; }
fi

step "Verifying the exported object against the committed corpus file"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
curl -sf -o "$TMP/l.xdr.zst" "http://localhost:8333/ledgers/$OBJECT_KEY"
zstd -qdc "$TMP/l.xdr.zst" > "$TMP/l.xdr"
stellar xdr decode --type LedgerCloseMetaBatch --input single --output json "$TMP/l.xdr" > "$TMP/l.json"
grep -q "$TX_HASH" "$TMP/l.json" || { echo "tx $TX_HASH not in exported ledger" >&2; exit 1; }
grep -q "$EXPECTED_SIGNER" "$TMP/l.json" || { echo "signer $EXPECTED_SIGNER not in exported ledger" >&2; exit 1; }
echo "tx $TX_HASH and signer ${EXPECTED_SIGNER:0:8}... present"
if cmp -s "$TMP/l.xdr" "$CORPUS_DIR/ledger-4437357.LedgerCloseMetaBatch.xdr"; then
  echo "exported LedgerCloseMetaBatch is byte-identical to the committed corpus file"
else
  # A fresh captive-core replay is not byte-reproducible: Soroban txs carry wall-clock
  # core_metrics diagnostic events and may order ledger-entry changes differently.
  # The ledger header and the #729 transaction's meta must still match exactly.
  stellar xdr decode --type LedgerCloseMetaBatch --input single --output json \
    "$CORPUS_DIR/ledger-4437357.LedgerCloseMetaBatch.xdr" > "$TMP/c.json"
  pick() { jq -cS --arg h "$TX_HASH" '.ledger_close_metas[0].v2 |
    [.ledger_header, (.tx_processing[] | select(.result.transaction_hash == $h))]' "$1"; }
  if [ "$(pick "$TMP/l.json")" = "$(pick "$TMP/c.json")" ]; then
    echo "exported file differs from the committed one only outside the #729 tx (Soroban diagnostics);"
    echo "ledger header and the #729 tx meta are identical"
  else
    echo "ledger header or #729 tx meta differs from the committed corpus file" >&2
    exit 1
  fi
fi

# --- 3. build, migrate, seed, backfill, verdict ----------------------------------------------
# shellcheck source=env-729.sh
. scripts/env-729.sh

run_variant() { # <name> <commit>
  local name=$1 commit=$2 db=wb_$1 bin=bin/wallet-backend-$1
  if [ ! -x "$bin" ]; then
    step "[$name] Building wallet-backend at $commit"
    scripts/build-wallet-backend.sh "$commit" "$bin"
  fi
  step "[$name] Fresh database $db, migrations, backfill seed"
  $PG -qc "DROP DATABASE IF EXISTS $db WITH (FORCE)" -qc "CREATE DATABASE $db" 2>&1 | grep -v NOTICE || true
  $PG -d "$db" -qc "CREATE EXTENSION IF NOT EXISTS timescaledb" 2>&1 | grep -v NOTICE || true
  export DATABASE_URL="postgres://postgres@localhost:5433/$db?sslmode=disable"
  "$bin" migrate up > "$OUT/migrate-$name.log" 2>&1
  $PG -d "$db" -qf scripts/seed-backfill.sql

  step "[$name] wallet-backend ingest (backfill $START_LEDGER..$END_LEDGER from the data lake)"
  local rc=0
  timeout 600 "$bin" ingest > "$OUT/ingest-$name.log" 2>&1 || rc=$?
  echo "ingest exit code: $rc (not used for the verdict)"
  grep -E 'Batch .*(failed|completed)|Backfilling completed' "$OUT/ingest-$name.log" | sed 's/^time="[^"]*" //' | cut -c1-400

  $PG -d "$db" -f scripts/query-729.sql > "$OUT/sql-$name.txt" 2>&1

  local tx_rows signer_hex signer account_hex account weight
  tx_rows=$($PG -d "$db" -Atc "SELECT count(*) FROM transactions WHERE hash = decode('$TX_HASH','hex')")
  signer_hex=$($PG -d "$db" -Atc "SELECT encode(signer_account_id,'hex') FROM state_changes
     WHERE ledger_number = $LEDGER AND state_change_category = 'SIGNER' AND state_change_reason = 'ADD' LIMIT 1")
  account_hex=$($PG -d "$db" -Atc "SELECT encode(account_id,'hex') FROM state_changes
     WHERE ledger_number = $LEDGER AND state_change_category = 'SIGNER' AND state_change_reason = 'ADD' LIMIT 1")
  weight=$($PG -d "$db" -Atc "SELECT signer_weight_new FROM state_changes
     WHERE ledger_number = $LEDGER AND state_change_category = 'SIGNER' AND state_change_reason = 'ADD' LIMIT 1")
  signer=$([ -n "$signer_hex" ] && python3 -I scripts/strkey.py "$signer_hex" || echo "")
  account=$([ -n "$account_hex" ] && python3 -I scripts/strkey.py "$account_hex" || echo "")

  local verdict
  if [ "$tx_rows" = 0 ]; then
    verdict=dropped
  elif [ "$signer" = "$EXPECTED_SIGNER" ] && [ "$account" = "$EXPECTED_ACCOUNT" ] && [ "$weight" = 1 ]; then
    verdict=pass
  else
    verdict=mishandled
  fi
  {
    echo "variant:          $name"
    echo "commit:           $commit"
    echo "tx persisted:     $tx_rows"
    echo "signer (decoded): ${signer:-<none>}"
    echo "account:          ${account:-<none>}"
    echo "signer weight:    ${weight:-<none>}"
    echo "verdict:          $verdict"
  } | tee "$OUT/verdict-$name.txt"
  eval "VERDICT_$name=$verdict"
}

run_variant prefix "$PREFIX_COMMIT"
run_variant fixed "$FIXED_COMMIT"

step "Result"
echo "pre-fix ($PREFIX_COMMIT): $VERDICT_prefix  (expected: dropped)"
echo "fixed   ($FIXED_COMMIT): $VERDICT_fixed  (expected: pass)"
if [ "$VERDICT_prefix" = dropped ] && [ "$VERDICT_fixed" = pass ]; then
  echo "REPRODUCED: wallet-backend #729"
else
  echo "NOT REPRODUCED" >&2
  exit 1
fi
