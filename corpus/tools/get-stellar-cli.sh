#!/usr/bin/env bash
# Fetch the pinned stellar CLI (XDR decoder for protocol-30 ledgers) into .cache/bin and print its path.
# The sha256 is GitHub's published digest for the release asset.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
VERSION=30.0.1
ASSET=stellar-cli-$VERSION-x86_64-unknown-linux-gnu.tar.gz
SHA256=48c70074020e09af0a3ebbd2bf62d53c9aec47935e49a112d3cd90fb4e37658f
BIN=$ROOT/.cache/bin/stellar

if [ -x "$BIN" ] && "$BIN" --version | grep -q "^stellar $VERSION "; then
  echo "$BIN"
  exit 0
fi
[ "$(uname -sm)" = "Linux x86_64" ] || { echo "only Linux x86_64 is pinned; set STELLAR to a stellar $VERSION binary" >&2; exit 1; }
mkdir -p "$ROOT/.cache/dl" "$ROOT/.cache/bin"
curl -sSfL -o "$ROOT/.cache/dl/$ASSET" "https://github.com/stellar/stellar-cli/releases/download/v$VERSION/$ASSET"
echo "$SHA256  $ROOT/.cache/dl/$ASSET" | sha256sum -c --quiet
tar xzf "$ROOT/.cache/dl/$ASSET" -C "$ROOT/.cache/bin" stellar
echo "$BIN"
