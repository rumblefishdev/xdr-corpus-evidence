#!/usr/bin/env bash
# Build wallet-backend at a given commit inside a golang container (no local Go needed).
# Usage: scripts/build-wallet-backend.sh <commit> <output-binary>
set -euo pipefail

COMMIT=${1:?commit}
OUT=${2:?output binary path}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
SRC=${WB_SRC:-$ROOT/.cache/wallet-backend}
GO_IMAGE=${GO_IMAGE:-golang:1.25.9-bookworm} # matches `go 1.25.9` in wallet-backend's go.mod at both commits

if [ ! -d "$SRC/.git" ]; then
  git clone -q https://github.com/stellar/wallet-backend.git "$SRC"
fi
git -C "$SRC" fetch -q origin
WT=$ROOT/.cache/wb-$COMMIT
if [ ! -d "$WT" ]; then
  git -C "$SRC" worktree add -q --detach "$WT" "$COMMIT"
fi

mkdir -p "$(dirname "$OUT")"
OUT_ABS=$(cd "$(dirname "$OUT")" && pwd)/$(basename "$OUT")

docker run --rm \
  -u "$(id -u):$(id -g)" \
  -e HOME=/tmp -e GOCACHE=/cache/build -e GOMODCACHE=/cache/mod -e CGO_ENABLED=0 \
  -v "$ROOT/.cache/go:/cache" \
  -v "$WT:/src:ro" \
  -v "$(dirname "$OUT_ABS"):/out" \
  -w /src "$GO_IMAGE" \
  go build -buildvcs=false -o "/out/$(basename "$OUT_ABS")" .

echo "built $OUT_ABS from $(git -C "$WT" rev-parse HEAD)"
