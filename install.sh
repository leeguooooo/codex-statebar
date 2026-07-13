#!/bin/sh
set -eu

REPO="leeguooooo/codex-statebar"
INSTALL_DIR="${CODEX_STATEBAR_INSTALL_DIR:-$HOME/.local/bin}"

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

asset="cxs-${os}-${arch}.tar.gz"
base="https://github.com/${REPO}/releases/latest/download"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT INT TERM

curl -fsSL "${base}/${asset}" -o "$tmp/$asset"
curl -fsSL "${base}/${asset}.sha256" -o "$tmp/$asset.sha256"

if command -v sha256sum >/dev/null 2>&1; then
  (cd "$tmp" && sha256sum -c "$asset.sha256")
else
  expected="$(cut -d ' ' -f 1 "$tmp/$asset.sha256")"
  actual="$(shasum -a 256 "$tmp/$asset" | cut -d ' ' -f 1)"
  [ "$expected" = "$actual" ] || { echo "SHA-256 verification failed" >&2; exit 1; }
fi

mkdir -p "$INSTALL_DIR"
tar -xzf "$tmp/$asset" -C "$INSTALL_DIR"
chmod +x "$INSTALL_DIR/cxs"
echo "Installed cxs to $INSTALL_DIR/cxs"
case ":$PATH:" in
  *":$INSTALL_DIR:"*) ;;
  *) echo "Add $INSTALL_DIR to PATH, then run: cxs doctor" ;;
esac
