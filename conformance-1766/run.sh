#!/usr/bin/env bash
# Run corpus entry lp-issuer-order-01 through @stellar/stellar-sdk before and after js-stellar-sdk #1766.
# Expects 17.2.0 (pre-fix) = mishandled and 17.2.1 (the release with #1766) = pass. Needs only Docker.
# Usage: conformance-1766/run.sh
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
NODE_IMAGE=node:22.12.0-bookworm-slim   # the SDK requires node >= 22.12
PREFIX=17.2.0                            # last release before #1766
FIXED=17.2.1                             # v17.2.0...v17.2.1 = #1766 + one docs commit
mkdir -p "$HERE/evidence"

run() { # <version>
  local v=$1 dir=$ROOT/.cache/npm/sdk-$1
  mkdir -p "$dir"
  docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -e npm_config_cache=/tmp/npm \
    -v "$ROOT:/repo:ro" -v "$HERE/evidence:/out" -v "$dir:/w" -w /w "$NODE_IMAGE" sh -c "
      [ -d node_modules/@stellar/stellar-sdk ] ||
        { npm init -y >/dev/null && npm install --no-audit --no-fund --silent @stellar/stellar-sdk@$v; }
      cp /repo/conformance-1766/check.mjs ./check.mjs && node check.mjs /repo /out/result-$v.json"
}

echo "== pre-fix @stellar/stellar-sdk@$PREFIX"
run $PREFIX
P=$(jq -r .verdict "$HERE/evidence/result-$PREFIX.json")
echo
echo "== fixed @stellar/stellar-sdk@$FIXED"
run $FIXED
F=$(jq -r .verdict "$HERE/evidence/result-$FIXED.json")
echo
echo "pre-fix $PREFIX: $P (expected: mishandled)"
echo "fixed   $FIXED: $F (expected: pass)"
if [ "$P" = mishandled ] && [ "$F" = pass ]; then
  echo "REPRODUCED: js-stellar-sdk #1766 on corpus entry lp-issuer-order-01"
else
  echo "NOT REPRODUCED" >&2
  exit 1
fi
