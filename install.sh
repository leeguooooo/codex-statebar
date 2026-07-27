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
trap 'rm -rf "$tmp"' EXIT INT TERM

verify_asset() {
  asset="$1"
  curl -fsSL "${base}/${asset}" -o "$tmp/$asset"
  curl -fsSL "${base}/${asset}.sha256" -o "$tmp/$asset.sha256"

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
verify_asset "$cxs_asset"
verify_asset "$codex_asset"

mkdir -p "$INSTALL_DIR"
tar -xzf "$tmp/$cxs_asset" -C "$INSTALL_DIR"
tar -xzf "$tmp/$codex_asset" -C "$INSTALL_DIR"
chmod +x "$INSTALL_DIR/cxs" "$INSTALL_DIR/codex-cxs"

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
  cp "$launcher" "$codex_path"
  chmod +x "$codex_path"
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
