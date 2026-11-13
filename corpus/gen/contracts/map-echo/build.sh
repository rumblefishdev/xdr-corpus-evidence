#!/usr/bin/env bash
# Build map_echo.wasm in a Rust container (no local Rust needed) and print its sha256.
# The committed map_echo.wasm is what `gen scmap-collision` embeds and uploads; Cargo.lock pins the deps.
# wasm32v1-none (Rust >= 1.84): the MVP-only wasm target the Soroban host accepts.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
docker run --rm -v "$HERE:/src" -w /src "${RUST_IMAGE:-rust:1.89.0-bookworm}" sh -c "
    set -e
    rustup target add wasm32v1-none >/dev/null
    cargo build --release --target wasm32v1-none --target-dir /tmp/target
    cp /tmp/target/wasm32v1-none/release/map_echo.wasm /src/map_echo.wasm
    chown $(id -u):$(id -g) /src/map_echo.wasm /src/Cargo.lock"
sha256sum "$HERE/map_echo.wasm"
