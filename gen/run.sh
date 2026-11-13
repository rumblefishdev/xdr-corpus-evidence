#!/usr/bin/env bash
# Run a corpus generator inside a golang container (no local Go needed).
# Usage: gen/run.sh muxed-soroban|lp-issuer-order
# Testnet by default; for a local quickstart set RPC_URL, FRIENDBOT_URL and NETWORK_PASSPHRASE (see main.go).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$ROOT/.cache/go"
exec docker run --rm --network host \
  -u "$(id -u):$(id -g)" \
  -e HOME=/tmp -e GOCACHE=/cache/build -e GOMODCACHE=/cache/mod -e GOFLAGS=-mod=mod \
  -e RPC_URL -e NETWORK_PASSPHRASE -e FRIENDBOT_URL \
  -v "$ROOT/.cache/go:/cache" -v "$ROOT/gen:/gen" -w /gen \
  "${GO_IMAGE:-golang:1.25.9-bookworm}" go run . "$@"
