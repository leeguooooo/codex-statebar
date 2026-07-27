"""Self-upgrade from GitHub Release binary assets."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

REPO = "leeguooooo/codex-statebar"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
TIMEOUT = 15


def _target() -> Optional[str]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    os_name = {"darwin": "macos", "linux": "linux", "windows": "windows"}.get(system)
    arch = {"x86_64": "x86_64", "amd64": "x86_64",
            "arm64": "arm64", "aarch64": "arm64"}.get(machine)
    if not os_name or not arch:
        return None
    return f"{os_name}-{arch}"


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "codex-statebar-updater"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read()


def _version_tuple(value: str) -> tuple:
    parts = []
    for token in value.lstrip("v").split("."):
        digits = ""
        for char in token:
            if not char.isdigit():
                break
            digits += char
        parts.append(int(digits or 0))
    return tuple(parts)


def _destination() -> Optional[Path]:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    command = shutil.which("cxs") or shutil.which("codex-statebar")
    return Path(command).resolve() if command else None


def _asset_urls(
    release: dict,
    target: str,
    binary: str = "cxs",
) -> Tuple[Optional[str], Optional[str]]:
    archive_name = f"{binary}-{target}.tar.gz"
    checksum_name = archive_name + ".sha256"
    urls = {
        asset.get("name"): asset.get("browser_download_url")
        for asset in release.get("assets", []) if isinstance(asset, dict)
    }
    return urls.get(archive_name), urls.get(checksum_name)


def _verified_archive(release: dict, target: str, binary: str) -> bytes:
    archive_url, checksum_url = _asset_urls(release, target, binary)
    if not archive_url or not checksum_url:
        tag = str(release.get("tag_name") or "latest")
        raise ValueError(f"Release {tag} has no {binary} asset for {target}.")
    archive = _fetch(archive_url)
    checksum_text = _fetch(checksum_url).decode("utf-8", "replace")
    expected = checksum_text.split()[0].lower()
    actual = hashlib.sha256(archive).hexdigest()
    if expected != actual:
        raise ValueError(f"Downloaded {binary} release failed SHA-256 verification.")
    return archive


def _extract_member(archive: bytes, member: str, destination: Path) -> None:
    with tempfile.TemporaryDirectory(prefix=f"{member}-upgrade-") as tmp:
        archive_path = Path(tmp) / "release.tar.gz"
        archive_path.write_bytes(archive)
        with tarfile.open(archive_path, "r:gz") as bundle:
            try:
                source = bundle.extractfile(member)
            except KeyError as exc:
                raise ValueError(f"Release archive does not contain {member}.") from exc
            if source is None:
                raise ValueError(f"Release archive does not contain a file named {member}.")
            staged = destination.with_name(destination.name + ".new")
            with source, staged.open("wb") as output:
                shutil.copyfileobj(source, output)
        staged.chmod(staged.stat().st_mode | stat.S_IXUSR)


def upgrade_current_install() -> Tuple[bool, str]:
    target = _target()
    if not target:
        return False, "Unsupported platform for a prebuilt cxs binary."
    destination = _destination()
    if destination is None:
        return False, (
            "No installed cxs binary found. Install with:\n"
            "curl -fsSL https://raw.githubusercontent.com/"
            f"{REPO}/main/install.sh | sh"
        )
    try:
        release = json.loads(_fetch(LATEST_API))
        tag = str(release.get("tag_name") or "")
        from . import __version__
        if tag and _version_tuple(tag) < _version_tuple(__version__):
            return True, (
                f"Installed cxs {__version__} is newer than latest release {tag}; "
                "nothing changed."
            )
        same_release = bool(
            tag and _version_tuple(tag) == _version_tuple(__version__)
        )
        binaries = [("cxs", "cxs.exe" if os.name == "nt" else "cxs", destination)]
        if os.name != "nt":
            binaries.append(
                ("codex-cxs", "codex-cxs", destination.with_name("codex-cxs"))
            )
        archives = {
            binary: _verified_archive(release, target, binary)
            for binary, _member, _destination_path in binaries
        }
        for binary, member, destination_path in binaries:
            _extract_member(archives[binary], member, destination_path)
        # Replace the patched Codex first and cxs last. All downloads,
        # checksums, and archive members have already been validated.
        for _binary, _member, destination_path in reversed(binaries):
            staged = destination_path.with_name(destination_path.name + ".new")
            os.replace(staged, destination_path)
        names = " and ".join(binary for binary, _member, _path in binaries)
        action = "Reinstalled" if same_release else "Upgraded"
        return True, f"{action} {names} from {tag or 'latest'} beside {destination}."
    except Exception as exc:
        return False, f"Upgrade failed: {type(exc).__name__}: {exc}"


def check_and_upgrade() -> Tuple[bool, str]:
    return upgrade_current_install()


def spawn_background_upgrade_check() -> None:
    # Background auto-upgrade is intentionally disabled. Explicit `cxs upgrade`
    # is reviewable and avoids mutating an executable from the render hot path.
    return None
