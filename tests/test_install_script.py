import hashlib
import io
import os
import platform
import subprocess
import tarfile
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX installer")


def _target() -> str:
    os_name = "macos" if platform.system() == "Darwin" else "linux"
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "x86_64"
    return f"{os_name}-{arch}"


def _write_asset(root: Path, name: str, member: str, content: bytes) -> None:
    asset = root / name
    info = tarfile.TarInfo(member)
    info.mode = 0o755
    info.size = len(content)
    with tarfile.open(asset, "w:gz") as archive:
        archive.addfile(info, io.BytesIO(content))
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    (root / f"{name}.sha256").write_text(
        f"{digest}  {name}\n", encoding="utf-8"
    )


def test_one_click_installs_both_binaries_and_backs_up_codex(tmp_path: Path):
    target = _target()
    assets = tmp_path / "assets"
    assets.mkdir()
    _write_asset(
        assets,
        f"cxs-{target}.tar.gz",
        "cxs",
        b"#!/bin/sh\nprintf '%s\\n' \"$@\"\n",
    )
    _write_asset(
        assets,
        f"codex-cxs-{target}.tar.gz",
        "codex-cxs",
        b"#!/bin/sh\necho codex-test\n",
    )
    install_dir = tmp_path / "bin"
    install_dir.mkdir()
    (install_dir / "codex").write_text("old codex\n", encoding="utf-8")

    env = os.environ.copy()
    env.update({
        "CODEX_STATEBAR_INSTALL_DIR": str(install_dir),
        "CODEX_STATEBAR_RELEASE_BASE": assets.resolve().as_uri(),
        "CODEX_STATEBAR_SKIP_SETUP": "1",
    })
    result = subprocess.run(
        ["sh", "install.sh"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert (install_dir / "cxs").read_text(encoding="utf-8").startswith("#!/bin/sh")
    assert (install_dir / "codex-cxs").read_text(encoding="utf-8").startswith("#!/bin/sh")
    assert not (install_dir / "codex").is_symlink()
    assert "managed by codex-statebar" in (install_dir / "codex").read_text(
        encoding="utf-8"
    )
    assert len(list(install_dir.glob("codex.backup.*"))) == 1
    launched = subprocess.run(
        [str(install_dir / "codex"), "--version"],
        text=True,
        capture_output=True,
        timeout=5,
    )
    assert launched.returncode == 0
    assert launched.stdout.splitlines() == ["_launch-codex", "--version"]


def test_one_click_migrates_old_managed_symlink_without_overwriting_binary(
    tmp_path: Path,
):
    target = _target()
    assets = tmp_path / "assets"
    assets.mkdir()
    _write_asset(
        assets,
        f"cxs-{target}.tar.gz",
        "cxs",
        b"#!/bin/sh\necho cxs-test\n",
    )
    patched = b"#!/bin/sh\necho codex-test\n"
    _write_asset(
        assets,
        f"codex-cxs-{target}.tar.gz",
        "codex-cxs",
        patched,
    )
    install_dir = tmp_path / "bin"
    install_dir.mkdir()
    (install_dir / "codex-cxs").write_bytes(patched)
    (install_dir / "codex").symlink_to("codex-cxs")

    env = os.environ.copy()
    env.update({
        "CODEX_STATEBAR_INSTALL_DIR": str(install_dir),
        "CODEX_STATEBAR_RELEASE_BASE": assets.resolve().as_uri(),
        "CODEX_STATEBAR_SKIP_SETUP": "1",
    })
    result = subprocess.run(
        ["sh", "install.sh"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert not (install_dir / "codex").is_symlink()
    assert "managed by codex-statebar" in (install_dir / "codex").read_text()
    assert (install_dir / "codex-cxs").read_bytes() == patched
    assert list(install_dir.glob("codex.backup.*")) == []
