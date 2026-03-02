#!/usr/bin/env bash

set -euo pipefail

MODE="correctness"
if [ "${1:-}" = "--strict" ]; then
    MODE="strict"
fi

ensure_cargo_subcommand_component() {
    local subcommand="$1"
    local toolchain="${RUSTUP_TOOLCHAIN:-}"
    local component="$subcommand"

    if [ "$subcommand" = "fmt" ]; then
        component="rustfmt"
    fi

    if cargo "$subcommand" --version >/dev/null 2>&1; then
        return 0
    fi

    if ! command -v rustup >/dev/null 2>&1; then
        echo "::error::cargo ${subcommand} is unavailable and rustup is not installed."
        return 1
    fi

    echo "==> rust quality: installing missing rust component '${component}'"
    if [ -n "$toolchain" ]; then
        rustup component add "$component" --toolchain "$toolchain"
    else
        rustup component add "$component"
    fi
}

ensure_cargo_subcommand_component "fmt"
echo "==> rust quality: cargo fmt --all -- --check"
cargo fmt --all -- --check

ensure_cargo_subcommand_component "clippy"
if [ "$MODE" = "strict" ]; then
    echo "==> rust quality: cargo clippy --locked --all-targets -- -D warnings"
    cargo clippy --locked --all-targets -- -D warnings
else
    echo "==> rust quality: cargo clippy --locked --all-targets -- -D clippy::correctness"
    cargo clippy --locked --all-targets -- -D clippy::correctness
fi
