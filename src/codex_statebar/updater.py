"""Self-upgrade from GitHub Release binary assets."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import ssl
import stat
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

REPO = "leeguooooo/codex-statebar"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASE_TAG_API = f"https://api.github.com/repos/{REPO}/releases/tags"
TIMEOUT = 15
CODE_MODE_HOST = "codex-code-mode-host"
# Records which native build codex-cxs and its tool host came from, so a cxs-only
# release does not re-download ~400 MB of unchanged native binaries.
NATIVE_RECEIPT = ".codex-cxs-receipt.json"
NATIVE_IDENTITY_KEYS = ("source_tag", "fingerprint", "codex_version")
SYSTEM_CA_FILES = (
    Path("/etc/ssl/cert.pem"),
    Path("/etc/ssl/certs/ca-certificates.crt"),
    Path("/etc/pki/tls/certs/ca-bundle.crt"),
)


def _target() -> Optional[str]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    os_name = {"darwin": "macos", "linux": "linux", "windows": "windows"}.get(system)
    arch = {"x86_64": "x86_64", "amd64": "x86_64",
            "arm64": "arm64", "aarch64": "arm64"}.get(machine)
    if not os_name or not arch:
        return None
    return f"{os_name}-{arch}"


def _ssl_context() -> ssl.SSLContext:
    for candidate in SYSTEM_CA_FILES:
        if candidate.is_file():
            return ssl.create_default_context(cafile=str(candidate))
    return ssl.create_default_context()


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "codex-statebar-updater"})
    with urllib.request.urlopen(
        request,
        timeout=TIMEOUT,
        context=_ssl_context(),
    ) as response:
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
        return Path(os.environ.get("CODEX_STATEBAR_ENTRYPOINT") or sys.executable).resolve()
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


def _asset_url(release: dict, name: str) -> Optional[str]:
    for asset in release.get("assets", []):
        if isinstance(asset, dict) and asset.get("name") == name:
            value = asset.get("browser_download_url")
            return value if isinstance(value, str) else None
    return None


def _patched_codex_release(release: dict) -> Tuple[dict, Optional[dict]]:
    manifest_url = _asset_url(release, "codex-cxs-manifest.json")
    if not manifest_url:
        return release, None
    manifest = json.loads(_fetch(manifest_url))
    source_tag = manifest.get("source_tag")
    if not isinstance(source_tag, str) or not source_tag:
        raise ValueError("Patched Codex manifest has no source_tag.")
    if source_tag == release.get("tag_name"):
        return release, manifest
    return json.loads(_fetch(f"{RELEASE_TAG_API}/{source_tag}")), manifest


def _file_sha256(path: Path) -> Optional[str]:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _receipt_path(destination: Path) -> Path:
    return destination.with_name(NATIVE_RECEIPT)


def _read_receipt(destination: Path) -> Optional[dict]:
    try:
        receipt = json.loads(_receipt_path(destination).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return receipt if isinstance(receipt, dict) else None


def _native_identity(manifest: dict) -> dict:
    return {key: manifest.get(key) for key in NATIVE_IDENTITY_KEYS}


def _native_is_current(
    receipt: Optional[dict],
    manifest: Optional[dict],
    path: Path,
) -> bool:
    """True when the installed native file came from this exact native build.

    The receipt alone is not trusted: the file on disk must still hash to what
    was installed, so a replaced or truncated binary is always reinstalled.
    """
    if receipt is None or manifest is None or path.is_symlink():
        return False
    identity = _native_identity(manifest)
    if not all(isinstance(value, str) and value for value in identity.values()):
        return False
    if any(receipt.get(key) != value for key, value in identity.items()):
        return False
    files = receipt.get("files")
    expected = files.get(path.name) if isinstance(files, dict) else None
    return isinstance(expected, str) and _file_sha256(path) == expected


def _write_receipt(destination: Path, manifest: dict, files: dict) -> None:
    path = _receipt_path(destination)
    staged = path.with_name(path.name + ".new")
    try:
        staged.write_text(
            json.dumps({**_native_identity(manifest), "files": files}, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(staged, path)
    except OSError:
        # Without a receipt the next upgrade simply reinstalls native files.
        staged.unlink(missing_ok=True)


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


def _upgrade_locked(target: str, destination: Path) -> Tuple[bool, str]:
    staged_paths: list[Path] = []
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
        patched_release, manifest = release, None
        native: list[Tuple[str, str, Path]] = []
        if os.name != "nt":
            patched_release, manifest = _patched_codex_release(release)
            native.append(
                ("codex-cxs", "codex-cxs", destination.with_name("codex-cxs"))
            )
            # Codex resolves its tool host beside codex-cxs. Releases that
            # predate the host asset rely on the launcher's fallback link.
            if _asset_urls(release, target, CODE_MODE_HOST)[0]:
                native.append((
                    CODE_MODE_HOST, CODE_MODE_HOST,
                    destination.with_name(CODE_MODE_HOST),
                ))
        receipt = _read_receipt(destination)
        if not (native and all(
            _native_is_current(receipt, manifest, path) for _b, _m, path in native
        )):
            binaries.extend(native)
        staged_paths = [
            path.with_name(path.name + ".new") for _b, _m, path in binaries
        ]
        archives = {
            binary: _verified_archive(
                patched_release if binary == "codex-cxs" else release,
                target,
                binary,
            )
            for binary, _member, _destination_path in binaries
        }
        for binary, member, destination_path in binaries:
            _extract_member(archives[binary], member, destination_path)
        # Replace the patched Codex first and cxs last. All downloads,
        # checksums, and archive members have already been validated.
        installed_native = {
            path.name: _file_sha256(path.with_name(path.name + ".new"))
            for binary, _m, path in binaries if binary != "cxs"
        }
        if installed_native:
            # Drop the old receipt before touching native files: an interrupted
            # install must never look current on the next run.
            _receipt_path(destination).unlink(missing_ok=True)
        for _binary, _member, destination_path in reversed(binaries):
            staged = destination_path.with_name(destination_path.name + ".new")
            os.replace(staged, destination_path)
        if installed_native and manifest is not None:
            _write_receipt(destination, manifest, installed_native)
        names = " and ".join(binary for binary, _member, _path in binaries)
        action = "Reinstalled" if same_release else "Upgraded"
        return True, f"{action} {names} from {tag or 'latest'} beside {destination}."
    except Exception as exc:
        return False, f"Upgrade failed: {type(exc).__name__}: {exc}"
    finally:
        # Never leave partial downloads beside live executables.
        for staged in staged_paths:
            try:
                staged.unlink(missing_ok=True)
            except OSError:
                pass


def _try_lock(handle) -> bool:
    """Take a non-blocking exclusive lock; released when the handle closes."""
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


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
    # Manual and background upgrades share one lock so two installers never
    # write the same .new staging files.
    lock_path = destination.with_name(".cxs-upgrade.lock")
    try:
        lock = lock_path.open("a+")
    except OSError as exc:
        return False, f"Upgrade failed: cannot open lock {lock_path}: {exc}"
    with lock:
        if not _try_lock(lock):
            return False, "Another cxs upgrade is already running."
        return _upgrade_locked(target, destination)


def check_and_upgrade() -> Tuple[bool, str]:
    return upgrade_current_install()


def spawn_background_upgrade_check() -> None:
    # Background auto-upgrade is intentionally disabled. Explicit `cxs upgrade`
    # is reviewable and avoids mutating an executable from the render hot path.
    return None
