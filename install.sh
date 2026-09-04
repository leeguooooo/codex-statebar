#!/bin/sh
set -eu

REPO="${CODEX_STATEBAR_REPO:-leeguooooo/codex-statebar}"
INSTALL_DIR="${CODEX_STATEBAR_INSTALL_DIR:-$HOME/.local/bin}"
INSTALL_CODEX_DEFAULT="${CODEX_STATEBAR_INSTALL_CODEX_DEFAULT:-1}"
SKIP_SETUP="${CODEX_STATEBAR_SKIP_SETUP:-0}"

case "$(uname -s)" in
  Darwin) os="macos" ;;
  Linux) os="linux" ;;
  *) echo "Unsupported OS: $(uname -s)" >&2; exit 1 ;;
esac

case "$(uname -m)" in
  x86_64|amd64) arch="x86_64" ;;
  arm64|aarch64) arch="arm64" ;;
  *) echo "Unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

target="${os}-${arch}"
base="${CODEX_STATEBAR_RELEASE_BASE:-https://github.com/${REPO}/releases/latest/download}"
tmp="$(mktemp -d)"
install_stage=""
trap 'rm -rf "$tmp"; if [ -n "$install_stage" ]; then rm -rf "$install_stage"; fi' EXIT INT TERM

verify_asset() {
  asset="$1"
  asset_base="$2"
  curl -fsSL "${asset_base}/${asset}" -o "$tmp/$asset"
  curl -fsSL "${asset_base}/${asset}.sha256" -o "$tmp/$asset.sha256"

  if command -v sha256sum >/dev/null 2>&1; then
    (cd "$tmp" && sha256sum -c "$asset.sha256")
  else
    expected="$(cut -d ' ' -f 1 "$tmp/$asset.sha256")"
    actual="$(shasum -a 256 "$tmp/$asset" | cut -d ' ' -f 1)"
    [ "$expected" = "$actual" ] || {
      echo "SHA-256 verification failed for $asset" >&2
      exit 1
    }
  fi
}

cxs_asset="cxs-${target}.tar.gz"
codex_asset="codex-cxs-${target}.tar.gz"
codex_base="${CODEX_STATEBAR_CODEX_RELEASE_BASE:-}"
if [ -z "$codex_base" ]; then
  if [ -n "${CODEX_STATEBAR_RELEASE_BASE:-}" ]; then
    codex_base="$base"
  else
    manifest="$tmp/codex-cxs-manifest.json"
    if curl -fsSL "${base}/codex-cxs-manifest.json" -o "$manifest"; then
      source_tag="$(
        sed -n \
          's/^[[:space:]]*"source_tag":[[:space:]]*"\([^"]*\)".*/\1/p' \
          "$manifest" | head -n 1
      )"
      if [ -n "$source_tag" ]; then
        codex_base="https://github.com/${REPO}/releases/download/${source_tag}"
      fi
    fi
    codex_base="${codex_base:-$base}"
  fi
fi
verify_asset "$cxs_asset" "$base"
verify_asset "$codex_asset" "$codex_base"

mkdir -p "$tmp/unpacked" "$INSTALL_DIR"
tar -xzf "$tmp/$cxs_asset" -C "$tmp/unpacked"
tar -xzf "$tmp/$codex_asset" -C "$tmp/unpacked"
# Stage on the destination filesystem so rename never truncates an executable
# still mapped by an active session, even when TMPDIR is on another volume.
install_stage="$(mktemp -d "$INSTALL_DIR/.cxs-install.XXXXXX")"
for binary in cxs codex-cxs; do
  cp "$tmp/unpacked/$binary" "$install_stage/$binary"
  chmod +x "$install_stage/$binary"
done
mv -f "$install_stage/codex-cxs" "$INSTALL_DIR/codex-cxs"
mv -f "$install_stage/cxs" "$INSTALL_DIR/cxs"

if [ "$INSTALL_CODEX_DEFAULT" = "1" ]; then
  codex_path="$INSTALL_DIR/codex"
  if [ -L "$codex_path" ] && [ "$(readlink "$codex_path")" = "codex-cxs" ]; then
    unlink "$codex_path"
  elif [ -f "$codex_path" ] && grep -q "managed by codex-statebar" "$codex_path"; then
    :
  else
    if [ -e "$codex_path" ] || [ -L "$codex_path" ]; then
      backup="$codex_path.backup.$(date +%Y%m%d%H%M%S)"
      mv "$codex_path" "$backup"
      echo "Backed up existing user-level codex to $backup"
    fi
  fi
  launcher="$tmp/codex"
  printf '%s\n' \
    '#!/bin/sh' \
    '# managed by codex-statebar' \
    'set -eu' \
    'bin_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)' \
    'export CODEX_STATEBAR_MANAGED_DIR="$bin_dir"' \
    'exec "$bin_dir/cxs" _launch-codex "$@"' > "$launcher"
  chmod +x "$launcher"
  cp "$launcher" "$install_stage/codex"
  chmod +x "$install_stage/codex"
  mv -f "$install_stage/codex" "$codex_path"
fi

if [ "$SKIP_SETUP" != "1" ]; then
  "$INSTALL_DIR/cxs" --setup
fi

echo "Installed cxs to $INSTALL_DIR/cxs"
echo "Installed patched Codex to $INSTALL_DIR/codex-cxs"
if [ "$INSTALL_CODEX_DEFAULT" = "1" ]; then
  echo "Installed version-aware Codex launcher to $INSTALL_DIR/codex"
fi
case ":$PATH:" in
  *":$INSTALL_DIR:"*) ;;
  *) echo "Add $INSTALL_DIR to PATH, then run: codex" ;;
esac
