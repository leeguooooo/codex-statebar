#!/usr/bin/env python3
"""Create and verify reusable patched-Codex release assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
MANIFEST_NAME = "codex-cxs-manifest.json"
TARGETS = (
    "macos-x86_64",
    "macos-arm64",
    "linux-x86_64",
    "linux-arm64",
)


def fingerprint_inputs(root: Path) -> list[Path]:
    return [
        root / "scripts" / "build-patched-codex.sh",
        *sorted((root / "patches").glob("codex-*.patch")),
    ]


def build_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in fingerprint_inputs(root):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def codex_version(root: Path) -> str:
    script = (root / "scripts" / "build-patched-codex.sh").read_text()
    match = re.search(r'^VERSION="\$\{CODEX_VERSION:-([^}]+)\}"$', script, re.MULTILINE)
    if not match:
        raise ValueError("could not read the default Codex version from build script")
    return match.group(1)


def asset_names() -> list[str]:
    return [f"codex-cxs-{target}.tar.gz" for target in TARGETS]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sidecar(asset: Path, expected: str) -> None:
    sidecar = asset.with_name(asset.name + ".sha256")
    if not sidecar.is_file():
        raise ValueError(f"missing checksum sidecar: {sidecar.name}")
    fields = sidecar.read_text().strip().split()
    if len(fields) != 2 or fields[0] != expected or fields[1] != asset.name:
        raise ValueError(f"invalid checksum sidecar: {sidecar.name}")


def create_manifest(root: Path, assets_dir: Path, source_tag: str) -> dict[str, Any]:
    assets: dict[str, str] = {}
    for name in asset_names():
        asset = assets_dir / name
        if not asset.is_file():
            raise ValueError(f"missing patched Codex asset: {name}")
        digest = sha256(asset)
        verify_sidecar(asset, digest)
        assets[name] = digest

    return {
        "schema": SCHEMA_VERSION,
        "fingerprint": build_fingerprint(root),
        "codex_version": codex_version(root),
        "source_tag": source_tag,
        "assets": assets,
    }


def verify_manifest_metadata(root: Path, manifest: dict[str, Any]) -> None:
    if manifest.get("schema") != SCHEMA_VERSION:
        raise ValueError("unsupported patched Codex manifest schema")
    if manifest.get("fingerprint") != build_fingerprint(root):
        raise ValueError("patched Codex source fingerprint does not match")
    if manifest.get("codex_version") != codex_version(root):
        raise ValueError("patched Codex version does not match")
    if not isinstance(manifest.get("source_tag"), str) or not manifest["source_tag"]:
        raise ValueError("patched Codex source tag is missing")

    assets = manifest.get("assets")
    if not isinstance(assets, dict) or sorted(assets) != sorted(asset_names()):
        raise ValueError("patched Codex asset list is incomplete")
    for name, digest in assets.items():
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise ValueError(f"invalid checksum in manifest: {name}")


def verify_manifest(root: Path, manifest: dict[str, Any], assets_dir: Path) -> None:
    verify_manifest_metadata(root, manifest)
    assets = manifest.get("assets")
    assert isinstance(assets, dict)
    for name in asset_names():
        asset = assets_dir / name
        if not asset.is_file():
            raise ValueError(f"missing patched Codex asset: {name}")
        expected = assets[name]
        if not isinstance(expected, str) or sha256(asset) != expected:
            raise ValueError(f"checksum mismatch: {name}")
        verify_sidecar(asset, expected)


def read_manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("manifest must be a JSON object")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help=argparse.SUPPRESS,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("fingerprint")

    matches = subparsers.add_parser("matches")
    matches.add_argument("manifest", type=Path)

    create = subparsers.add_parser("create")
    create.add_argument("assets_dir", type=Path)
    create.add_argument("--source-tag", required=True)

    verify = subparsers.add_parser("verify")
    verify.add_argument("manifest", type=Path)
    verify.add_argument("assets_dir", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    try:
        if args.command == "fingerprint":
            print(build_fingerprint(root))
        elif args.command == "matches":
            manifest = read_manifest(args.manifest)
            verify_manifest_metadata(root, manifest)
        elif args.command == "create":
            manifest = create_manifest(root, args.assets_dir, args.source_tag)
            output = args.assets_dir / MANIFEST_NAME
            output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            print(output)
        elif args.command == "verify":
            verify_manifest(root, read_manifest(args.manifest), args.assets_dir)
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
