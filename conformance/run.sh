#!/usr/bin/env bash
# Run every corpus entry through several @stellar/stellar-sdk releases (generic checks, see check.mjs) and compare
# the verdicts with expected.json. Needs only Docker.
#   17.2.0  last release before js-stellar-sdk #1766
#   17.2.1  #1766 fixed (v17.2.0...v17.2.1 = #1766 + one docs commit)
#   17.3.0  #1789 (scValToNative drops colliding map keys) and #1785 fixed
#   17.4.0  latest release on 2026-11-13 (must pass everything and decode every protocol-30 ledger file)
# Usage: conformance/run.sh [--record]   (--record writes results to conformance/evidence/, else .cache/conformance/)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
NODE_IMAGE=node:22.12.0-bookworm-slim   # the SDK requires node >= 22.12
VERSIONS=(17.2.0 17.2.1 17.3.0 17.4.0)
if [ "${1:-}" = --record ]; then OUT=$HERE/evidence; else OUT=$ROOT/.cache/conformance; fi
mkdir -p "$OUT"

for v in "${VERSIONS[@]}"; do
  dir=$ROOT/.cache/npm/sdk-$v
  mkdir -p "$dir"
  docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -e npm_config_cache=/tmp/npm \
    -v "$ROOT:/repo:ro" -v "$OUT:/out" -v "$dir:/w" -w /w "$NODE_IMAGE" sh -c "
      [ -d node_modules/@stellar/stellar-sdk ] ||
        { npm init -y >/dev/null && npm install --no-audit --no-fund --silent @stellar/stellar-sdk@$v; }
      cp /repo/conformance/check.mjs ./check.mjs && node check.mjs /repo /out/result-$v.json"
  echo
done

# Compare with expected.json: every (version, entry) verdict must match.
fail=0
printf '%-36s' "entry"; for v in "${VERSIONS[@]}"; do printf '%-13s' "$v"; done; echo
for e in $(jq -r '.entries[].id' "$ROOT/corpus/manifest.json"); do
  printf '%-36s' "$e"
  for v in "${VERSIONS[@]}"; do
    got=$(jq -r --arg e "$e" '.results[] | select(.entry == $e) | .verdict' "$OUT/result-$v.json")
    want=$(jq -r --arg e "$e" --arg v "$v" '.[$v][$e] // "pass"' "$HERE/expected.json")
    if [ "$got" = "$want" ]; then printf '%-13s' "$got"; else printf '%-13s' "$got!=$want"; fail=1; fi
  done
  echo
done
# The latest release must also decode every entry's full ledger file (older releases lag on ledger meta).
LATEST=${VERSIONS[-1]}
undecoded=$(jq '[.results[] | select(.info[0].ok | not)] | length' "$OUT/result-$LATEST.json")
echo "latest $LATEST decodes all full ledger files: $([ "$undecoded" = 0 ] && echo yes || echo "no, $undecoded fail")"
[ "$undecoded" = 0 ] || fail=1
if [ $fail = 0 ]; then
  echo "AS EXPECTED: js-stellar-sdk #1766 (17.2.0 -> 17.2.1) and #1789 (17.2.1 -> 17.3.0) reproduced by generic checks"
else
  echo "UNEXPECTED VERDICTS (marked got!=expected)" >&2
  exit 1
fi
