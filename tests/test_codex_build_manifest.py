from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "codex_build_manifest.py"
SPEC = importlib.util.spec_from_file_location("codex_build_manifest", SCRIPT)
assert SPEC and SPEC.loader
manifest_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manifest_module)


def write_inputs(root: Path) -> None:
    (root / "scripts").mkdir(parents=True)
    (root / "patches").mkdir()
    (root / "scripts" / "build-patched-codex.sh").write_text(
        '#!/bin/sh\nVERSION="${CODEX_VERSION:-0.145.0}"\n'
    )
    (root / "patches" / "codex-0.145.0.patch").write_text("patch contents\n")


def write_assets(assets_dir: Path) -> None:
    assets_dir.mkdir()
    for name in manifest_module.asset_names():
        asset = assets_dir / name
        asset.write_bytes(name.encode())
        digest = hashlib.sha256(asset.read_bytes()).hexdigest()
        (assets_dir / f"{name}.sha256").write_text(f"{digest}  {name}\n")


def test_manifest_round_trip(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    assets_dir = tmp_path / "assets"
    write_inputs(root)
    write_assets(assets_dir)

    manifest = manifest_module.create_manifest(root, assets_dir, "v0.1.1")

    assert manifest["codex_version"] == "0.145.0"
    assert manifest["source_tag"] == "v0.1.1"
    manifest_module.verify_manifest(root, manifest, assets_dir)


def test_manifest_rejects_changed_patch(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    assets_dir = tmp_path / "assets"
    write_inputs(root)
    write_assets(assets_dir)
    manifest = manifest_module.create_manifest(root, assets_dir, "v0.1.1")

    (root / "patches" / "codex-0.145.0.patch").write_text("changed\n")

    with pytest.raises(ValueError, match="fingerprint"):
        manifest_module.verify_manifest(root, manifest, assets_dir)


def test_manifest_rejects_corrupt_asset(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    assets_dir = tmp_path / "assets"
    write_inputs(root)
    write_assets(assets_dir)
    manifest = manifest_module.create_manifest(root, assets_dir, "v0.1.1")
    (assets_dir / manifest_module.asset_names()[0]).write_bytes(b"corrupt")

    with pytest.raises(ValueError, match="checksum mismatch"):
        manifest_module.verify_manifest(root, manifest, assets_dir)


def test_read_manifest_requires_object(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps([]))

    with pytest.raises(ValueError, match="JSON object"):
        manifest_module.read_manifest(path)
