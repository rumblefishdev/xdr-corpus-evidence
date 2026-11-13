#!/usr/bin/env bash
# Reproduce wallet-backend #729: a CAP-40 signed-payload signer (P... strkey) added by set_options
# in testnet ledger 4437357 cannot be persisted by the pre-fix commit; the fix stores it.
#
# Usage: sighting-729/repro.sh [--from-corpus] [--clean] [--record]
#   --from-corpus  upload the committed ledger file to the local S3 instead of exporting it with
#                  Galexie (no testnet history archive needed; survives a testnet reset)
#   --clean        remove the local S3/DB containers and volumes first
#   --record       write logs and verdicts to sighting-729/evidence/ (the committed evidence);
#                  by default they go to .cache/sighting-729/<timestamp>/ and the working tree stays clean
#
# Verdict per binary (same shape as the future harness):
#   pass        all transactions of the ledger are persisted and the SIGNER state change holds the full
#               P... key, the right account and weight 1
#   mishandled  the tx is persisted, but the signer is missing or altered, or other txs of the ledger are missing
#   dropped     the tx is not persisted AND the ingest log shows the #729 failure for this signer
#               (the signer_account_id conversion error); the cause is checked, not just the empty table
#   error       the tx is not persisted for any other reason (setup, network, data lake): inconclusive
# The script exits 0 only if pre-fix = dropped and fixed = pass.
# Note: wallet-backend exits 0 even when a backfill batch fails, so verdicts come from the DB and the log.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

PREFIX_COMMIT=2a0a84ea12523b6c92e278c0c84b443041240095
FIXED_COMMIT=2c873353c1f0f3c9b145e25a7d009023ad104a1c
LEDGER=4437357
TX_HASH=d9f78749cd01297cd5f22c7f2c798c9eec93d56f399e1c4cc2f8977ca52b9fed
EXPECTED_SIGNER=PBCK2RYYLIYOSHBEL2FQLAVRSHRH3GP3ID476SGKBV7BEZHMQ6NSAAAAAAB55LN6AANMS
EXPECTED_ACCOUNT=GBCK2RYYLIYOSHBEL2FQLAVRSHRH3GP3ID476SGKBV7BEZHMQ6NSBB7B
CORPUS_DIR=corpus/entries/cap-0040/cap0040-signed-payload-signer-01
OBJECT_KEY=testnet/FFBC9DFF--4416000-4479999/FFBC4A92--4437357.xdr.zst
HORIZON=https://horizon-testnet.stellar.org
# Host ports of the local S3 and Postgres; override if 8333/5433 are taken (infra/docker-compose.yml reads the same vars).
export XDR_S3_PORT=${XDR_S3_PORT:-8333} XDR_PG_PORT=${XDR_PG_PORT:-5433}
S3_PORT=$XDR_S3_PORT
PG_PORT=$XDR_PG_PORT
PG="psql -h localhost -p $PG_PORT -U postgres -v ON_ERROR_STOP=1"
export PGCONNECT_TIMEOUT=5

FROM_CORPUS=0
CLEAN=0
RECORD=0
for a in "$@"; do
  case $a in
    --from-corpus) FROM_CORPUS=1 ;;
    --clean) CLEAN=1 ;;
    --record) RECORD=1 ;;
    *) echo "unknown argument: $a" >&2; exit 2 ;;
  esac
done
if [ $RECORD = 1 ]; then OUT=sighting-729/evidence; else OUT=.cache/sighting-729/$(date -u +%Y%m%dT%H%M%SZ); fi

compose() { docker compose -f "$ROOT/infra/docker-compose.yml" "$@"; }
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
mkdir -p "$OUT"

# --- 1. local S3 + database -------------------------------------------------------------------
if [ $CLEAN = 1 ]; then
  step "Removing old containers and volumes"
  compose down -v --remove-orphans
fi
step "Starting SeaweedFS (S3 :$S3_PORT) and TimescaleDB (:$PG_PORT)"
compose up -d --wait s3 db

# --- 2. put ledger 4437357 in the data lake ---------------------------------------------------
if [ $FROM_CORPUS = 1 ]; then
  step "Uploading the committed ledger file to s3://ledgers/$OBJECT_KEY"
  curl -sf -X PUT --data-binary "@$CORPUS_DIR/FFBC4A92--4437357.xdr.zst" \
    "http://localhost:$S3_PORT/ledgers/$OBJECT_KEY" >/dev/null
  # wallet-backend gets the schema from env, but publish the manifest Galexie would write too
  printf '%s' '{"networkPassphrase":"Test SDF Network ; September 2015","version":"1.0","compression":"zstd","ledgersPerBatch":1,"batchesPerPartition":64000}' |
    curl -sf -X PUT --data-binary @- "http://localhost:$S3_PORT/ledgers/testnet/.config.json" >/dev/null
else
  step "Exporting ledgers $LEDGER..$((LEDGER + 1)) with Galexie (captive core catches up from the testnet archive)"
  # Galexie requires end > start; 4437358 is exported too and is harmless.
  compose run --rm galexie scan-and-fill --start $LEDGER --end $((LEDGER + 1)) \
    --config-file /config/testnet.toml > "$OUT/galexie-export.log" 2>&1 ||
    { tail -20 "$OUT/galexie-export.log"; exit 1; }
  # captive core logs the network root secret (sha256 of the passphrase; public, but keep secrets out of the repo)
  sed -i -E 's/(Root account seed: )S[A-Z2-7]{55}/\1<redacted: the network root key, sha256 of the testnet passphrase>/' \
    "$OUT/galexie-export.log"
fi

STELLAR=${STELLAR:-$(corpus/tools/get-stellar-cli.sh | tail -1)}
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
curl -sf -o "$TMP/l.xdr.zst" "http://localhost:$S3_PORT/ledgers/$OBJECT_KEY"
zstd -qdc "$TMP/l.xdr.zst" > "$TMP/l.xdr"
"$STELLAR" xdr decode --type LedgerCloseMetaBatch --input single --output json "$TMP/l.xdr" > "$TMP/l.json"
grep -q "$TX_HASH" "$TMP/l.json" || { echo "tx $TX_HASH not in the ledger in S3" >&2; exit 1; }
grep -q "$EXPECTED_SIGNER" "$TMP/l.json" || { echo "signer $EXPECTED_SIGNER not in the ledger in S3" >&2; exit 1; }
echo "tx $TX_HASH and signer ${EXPECTED_SIGNER:0:8}... present"
# Number of transactions in the ledger: the fixed binary must persist all of them.
LEDGER_TX_COUNT=$(jq '.ledger_close_metas[0].v2.tx_processing | length' "$TMP/l.json")
if [ $FROM_CORPUS = 1 ]; then
  # The uploaded object IS the committed file, so comparing the two proves nothing. Check the committed
  # transaction against the network instead (independent of this repo), when testnet still has it.
  step "Checking the committed #729 transaction against testnet Horizon"
  if H=$(curl -sf --max-time 20 "$HORIZON/transactions/$TX_HASH"); then
    env_b64=$(base64 -w0 "$CORPUS_DIR"/tx-*.TransactionEnvelope.xdr)
    if [ "$(jq -r .envelope_xdr <<<"$H")" = "$env_b64" ] && [ "$(jq -r .ledger <<<"$H")" = "$LEDGER" ] &&
       [ "$(jq -r .successful <<<"$H")" = true ]; then
      echo "Horizon: tx in ledger $LEDGER, successful, envelope byte-identical to the committed one"
    else
      echo "Horizon disagrees with the committed transaction (envelope, ledger or result)" >&2
      exit 1
    fi
  else
    echo "Horizon unreachable or tx unknown (testnet reset?): committed file NOT verified against the network"
    echo "(run without --from-corpus to re-export it from the history archive)"
  fi
elif cmp -s "$TMP/l.xdr" "$CORPUS_DIR/ledger-4437357.LedgerCloseMetaBatch.xdr"; then
  step "Comparing the fresh export with the committed corpus file"
  echo "exported LedgerCloseMetaBatch is byte-identical to the committed corpus file"
else
  step "Comparing the fresh export with the committed corpus file"
  # A fresh captive-core replay is not byte-reproducible: Soroban txs carry wall-clock
  # core_metrics diagnostic events and may order ledger-entry changes differently.
  # The ledger header and the #729 transaction's meta must still match exactly.
  "$STELLAR" xdr decode --type LedgerCloseMetaBatch --input single --output json \
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
# shellcheck source=env.sh
. sighting-729/env.sh

run_variant() { # <name> <commit>
  local name=$1 commit=$2 db=wb_$1 bin=.cache/wb-bin/wallet-backend-$1
  if [ ! -x "$bin" ]; then
    step "[$name] Building wallet-backend at $commit"
    sighting-729/build-wallet-backend.sh "$commit" "$bin"
  fi
  step "[$name] Fresh database $db, migrations, backfill seed"
  $PG -qc "DROP DATABASE IF EXISTS $db WITH (FORCE)" -qc "CREATE DATABASE $db" 2>&1 | grep -v NOTICE || true
  $PG -d "$db" -qc "CREATE EXTENSION IF NOT EXISTS timescaledb" 2>&1 | grep -v NOTICE || true
  export DATABASE_URL="postgres://postgres@localhost:$PG_PORT/$db?sslmode=disable"
  "$bin" migrate up > "$OUT/migrate-$name.log" 2>&1
  $PG -d "$db" -qf sighting-729/seed-backfill.sql

  step "[$name] wallet-backend ingest (backfill $START_LEDGER..$END_LEDGER from the data lake)"
  local rc=0
  timeout 600 "$bin" ingest > "$OUT/ingest-$name.log" 2>&1 || rc=$?
  echo "ingest exit code: $rc (not used for the verdict)"
  grep -E 'Batch .*(failed|completed)|Backfilling completed' "$OUT/ingest-$name.log" | sed 's/^time="[^"]*" //' | cut -c1-400

  $PG -d "$db" -f sighting-729/query.sql > "$OUT/sql-$name.txt" 2>&1

  local tx_rows ledger_rows signer_hex signer account_hex account weight bug_in_log=no
  tx_rows=$($PG -d "$db" -Atc "SELECT count(*) FROM transactions WHERE hash = decode('$TX_HASH','hex')")
  ledger_rows=$($PG -d "$db" -Atc "SELECT count(*) FROM transactions WHERE ledger_number = $LEDGER")
  # The #729 failure: wallet-backend cannot convert this P... signer into its 32-byte account column.
  grep -F "converting signer_account_id" "$OUT/ingest-$name.log" | grep -qF "$EXPECTED_SIGNER" && bug_in_log=yes
  signer_hex=$($PG -d "$db" -Atc "SELECT encode(signer_account_id,'hex') FROM state_changes
     WHERE ledger_number = $LEDGER AND state_change_category = 'SIGNER' AND state_change_reason = 'ADD' LIMIT 1")
  account_hex=$($PG -d "$db" -Atc "SELECT encode(account_id,'hex') FROM state_changes
     WHERE ledger_number = $LEDGER AND state_change_category = 'SIGNER' AND state_change_reason = 'ADD' LIMIT 1")
  weight=$($PG -d "$db" -Atc "SELECT signer_weight_new FROM state_changes
     WHERE ledger_number = $LEDGER AND state_change_category = 'SIGNER' AND state_change_reason = 'ADD' LIMIT 1")
  signer=$([ -n "$signer_hex" ] && python3 -I sighting-729/strkey.py "$signer_hex" || echo "")
  account=$([ -n "$account_hex" ] && python3 -I sighting-729/strkey.py "$account_hex" || echo "")

  local verdict
  if [ "$tx_rows" = 0 ] && [ $bug_in_log = yes ]; then
    verdict=dropped
  elif [ "$tx_rows" = 0 ]; then
    verdict=error   # nothing stored, but not because of #729: setup/network problem, inconclusive
  elif [ "$signer" = "$EXPECTED_SIGNER" ] && [ "$account" = "$EXPECTED_ACCOUNT" ] && [ "$weight" = 1 ] &&
       [ "$ledger_rows" = "$LEDGER_TX_COUNT" ]; then
    verdict=pass
  else
    verdict=mishandled
  fi
  {
    echo "variant:          $name"
    echo "commit:           $commit"
    echo "tx persisted:     $tx_rows"
    echo "ledger txs:       $ledger_rows of $LEDGER_TX_COUNT persisted"
    echo "#729 error in log: $bug_in_log"
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
