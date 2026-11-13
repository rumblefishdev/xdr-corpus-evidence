#!/usr/bin/env bash
# Check every corpus file listed in corpus/manifest.json:
#   * sha256 matches the manifest,
#   * .xdr.zst decompresses to bytes that decode,
#   * decode -> encode is byte-exact (stellar CLI = rs-stellar-xdr).
# Usage: corpus/tools/roundtrip.sh   (STELLAR=<cli path> to override the pinned CLI)
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"
STELLAR=${STELLAR:-$(corpus/tools/get-stellar-cli.sh | tail -1)}
echo "decoder: $("$STELLAR" --version | sed -n 2p)"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
fail=0
while IFS=$'\t' read -r id path type encoding sha; do
  if [ "$(sha256sum "$path" | cut -d' ' -f1)" != "$sha" ]; then
    echo "FAIL $id $path: sha256 mismatch"; fail=1; continue
  fi
  case $encoding in
    xdr) cp "$path" "$TMP/in.xdr" ;;
    xdr+zstd) zstd -qdc "$path" > "$TMP/in.xdr" ;;
    *) echo "FAIL $id $path: unsupported encoding $encoding"; fail=1; continue ;;
  esac
  if "$STELLAR" xdr decode --type "$type" --input single --output json "$TMP/in.xdr" > "$TMP/out.json" &&
     "$STELLAR" xdr encode --type "$type" --input json --output single "$TMP/out.json" > "$TMP/out.xdr" &&
     cmp -s "$TMP/in.xdr" "$TMP/out.xdr"; then
    echo "ok   $id $path ($type, $(stat -c %s "$TMP/in.xdr") bytes)"
  else
    echo "FAIL $id $path: decode -> encode is not byte-exact"; fail=1
  fi
done < <(jq -r '.entries[] | .id as $id | .files[] | [$id, .path, .xdr_type, .encoding, .sha256] | @tsv' corpus/manifest.json)
exit $fail
