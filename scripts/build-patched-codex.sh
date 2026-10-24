#!/usr/bin/env bash
set -euo pipefail

VERSION="${CODEX_VERSION:-0.164.0}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_ROOT="${CODEX_BUILD_ROOT:-$ROOT/build/codex-$VERSION}"
SOURCE_DIR="$BUILD_ROOT/source"
ARCHIVE="$BUILD_ROOT/codex-$VERSION.tar.gz"
CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$BUILD_ROOT/target}"
PATCH_FILES=(
  "$ROOT/patches/codex-0.164.0-external-status-line.patch"
)
OUTPUT_DIR="${CODEX_OUTPUT_DIR:-$ROOT/dist}"

if [[ "$VERSION" != "0.164.0" ]]; then
  echo "error: this patch targets Codex 0.164.0, got $VERSION" >&2
  exit 2
fi

mkdir -p "$BUILD_ROOT" "$OUTPUT_DIR"
if [[ ! -f "$ARCHIVE" ]]; then
  curl -fL --retry 3 \
    "https://github.com/openai/codex/archive/refs/tags/rust-v$VERSION.tar.gz" \
    -o "$ARCHIVE"
fi

rm -rf "$SOURCE_DIR"
mkdir -p "$SOURCE_DIR"
tar -xzf "$ARCHIVE" -C "$SOURCE_DIR" --strip-components=1
for patch_file in "${PATCH_FILES[@]}"; do
  patch -d "$SOURCE_DIR" -p1 --forward < "$patch_file"
done

# Release tags update workspace.package.version but leave path-package lock
# entries at 0.0.0. Normalize only those entries; registry/git pins stay intact.
python3 - "$SOURCE_DIR/codex-rs/Cargo.lock" "$VERSION" <<'PYLOCK'
from pathlib import Path
import sys
path = Path(sys.argv[1])
blocks = path.read_text().split('\n[[package]]\n')
for index, block in enumerate(blocks):
    if '\nsource = ' not in block:
        blocks[index] = block.replace('\nversion = "0.0.0"',
                                      '\nversion = "' + sys.argv[2] + '"', 1)
path.write_text('\n[[package]]\n'.join(blocks))
PYLOCK

if [[ "${CODEX_PATCH_ONLY:-0}" == "1" ]]; then
  echo "patched source: $SOURCE_DIR"
  exit 0
fi

if [[ "${CODEX_RUN_PATCH_TESTS:-0}" == "1" ]]; then
  cargo test --locked --release \
    --manifest-path "$SOURCE_DIR/codex-rs/Cargo.toml" \
    --package codex-tui --lib external_status_ \
    --target-dir "$CARGO_TARGET_DIR"
fi

cargo build --locked \
  --manifest-path "$SOURCE_DIR/codex-rs/Cargo.toml" \
  --package codex-cli \
  --target-dir "$CARGO_TARGET_DIR" \
  --release

cp "$CARGO_TARGET_DIR/release/codex" "$OUTPUT_DIR/codex-cxs"
chmod +x "$OUTPUT_DIR/codex-cxs"
echo "built $OUTPUT_DIR/codex-cxs"
