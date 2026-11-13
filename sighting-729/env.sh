# Environment for running wallet-backend backfill over ledger 4437357 from the local data lake.
# Source it, then set DATABASE_URL per binary.
export INGESTION_MODE=backfill
export START_LEDGER=4437357
export END_LEDGER=4437357
export LEDGER_BACKEND_TYPE=datastore
export DATASTORE_BUCKET_PATH=ledgers/testnet
export DATASTORE_ENDPOINT_URL=http://localhost:8333
export DATASTORE_REGION=us-east-1
export DATASTORE_LEDGERS_PER_FILE=1
export DATASTORE_FILES_PER_PARTITION=64000   # matches galexie/testnet.toml
export AWS_ACCESS_KEY_ID=test                # dummy keys; SeaweedFS runs without auth
export AWS_SECRET_ACCESS_KEY=test
export AWS_REGION=us-east-1
export NETWORK_PASSPHRASE="Test SDF Network ; September 2015"
export RPC_URL=https://soroban-testnet.stellar.org  # required by `ingest` even with the datastore backend
export STELLAR_ENVIRONMENT=local-repro             # required label (metrics/logging only)
export LOG_LEVEL=INFO
