#!/usr/bin/env bash
# Unified test gate for the Leng repo.
# Non-zero exit -> red. Stages: backend pytest; backend ruff (lint gate,
# rule 5); backend mypy --strict app (type gate, rule 5); rustfmt checks
# on both client crates; cargo clippy -D warnings on both client crates;
# the Rust client's leng-core tests (the tauri crate `leng` is a separate
# workspace and ships no unit tests).
set -euo pipefail

# CWD-independent: everything below resolves from the repo root the script
# lives in (same approach as golden.sh).
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

# cargo lives under ~/.cargo (rustup default) and is not on the PATH of
# non-interactive shells; make the gate work from either.
if ! command -v cargo >/dev/null 2>&1; then
  export PATH="$HOME/.cargo/bin:$PATH"
fi

(cd backend && uv run pytest tests -q)

(cd backend && uv run ruff check app tests)

(cd backend && uv run mypy)

cargo fmt --check --manifest-path client/core/Cargo.toml
cargo fmt --check --manifest-path client/src-tauri/Cargo.toml

cargo clippy --manifest-path client/core/Cargo.toml --all-targets --quiet -- -D warnings
cargo clippy --manifest-path client/src-tauri/Cargo.toml --all-targets --quiet -- -D warnings

cargo test --manifest-path client/core/Cargo.toml --quiet
