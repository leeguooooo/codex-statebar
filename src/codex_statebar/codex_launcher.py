"""Choose between the patched and official Codex binaries.

The installer places a small ``codex`` wrapper beside ``cxs``.  The wrapper
calls this module so an older patched build can never silently shadow a newer
official Codex found later on PATH.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple


_VERSION_RE = re.compile(
    r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?"
)
_PROBE_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class CodexBinary:
    path: Path
    version_text: str
    version: Tuple[int, int, int, int]
    patched: bool


@dataclass(frozen=True)
class LaunchDecision:
    selected: Optional[CodexBinary]
    patched: Optional[CodexBinary]
    official: Optional[CodexBinary]
    reason: str


def independent_process_environment() -> dict[str, str]:
    """Return an environment safe for processes independent of frozen cxs."""
    environment = dict(os.environ)
    if getattr(sys, "frozen", False):
        for name in tuple(environment):
            if name.startswith("_PYI_"):
                environment.pop(name, None)
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return environment


def _version_key(value: str) -> Optional[Tuple[int, int, int, int]]:
    match = _VERSION_RE.search(value)
    if not match:
        return None
    major, minor, patch = (int(match.group(index)) for index in (1, 2, 3))
    stable = 1 if match.group(4) is None else 0
    return major, minor, patch, stable


def _probe(path: Path) -> Optional[CodexBinary]:
    try:
        completed = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
            env=independent_process_environment(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    text = (completed.stdout.strip() or completed.stderr.strip()).splitlines()[0]
    version = _version_key(text)
    if version is None:
        return None
    return CodexBinary(
        path=path,
        version_text=text,
        version=version,
        patched=path.stem == "codex-cxs",
    )


def _same_file(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return left.absolute() == right.absolute()


def _is_managed_launcher(path: Path) -> bool:
    try:
        with path.open("rb") as launcher:
            return b"managed by codex-statebar" in launcher.read(512)
    except OSError:
        return False


def _is_app_embedded_binary(path: Path) -> bool:
    normalized = str(path).replace("\\", "/")
    return ".app/Contents/Resources/" in normalized


def _managed_dir() -> Path:
    configured = os.environ.get("CODEX_STATEBAR_MANAGED_DIR")
    if configured:
        return Path(configured).expanduser().absolute()
    command = shutil.which("cxs") or shutil.which("codex-statebar")
    if command:
        return Path(command).absolute().parent
    return Path(sys.executable).absolute().parent


def _official_paths(
    path_value: Optional[str],
    managed_dir: Path,
    patched_path: Path,
) -> Iterable[Path]:
    seen = set()
    executable = "codex.exe" if os.name == "nt" else "codex"
    search_path = (
        path_value if path_value is not None else os.environ.get("PATH", "")
    )
    for entry in search_path.split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry).expanduser().absolute() / executable
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            continue
        if _is_app_embedded_binary(candidate):
            continue
        if _is_managed_launcher(candidate):
            continue
        if _same_file(candidate, managed_dir / executable):
            continue
        if patched_path.exists() and _same_file(candidate, patched_path):
            continue
        try:
            identity = str(candidate.resolve())
        except OSError:
            identity = str(candidate)
        if identity in seen:
            continue
        seen.add(identity)
        yield candidate


def inspect_install(
    *,
    managed_dir: Optional[Path] = None,
    path_value: Optional[str] = None,
) -> LaunchDecision:
    root = (managed_dir or _managed_dir()).absolute()
    patched_path = root / ("codex-cxs.exe" if os.name == "nt" else "codex-cxs")
    patched = _probe(patched_path) if patched_path.is_file() else None
    officials = [
        binary
        for binary in (
            _probe(path) for path in _official_paths(path_value, root, patched_path)
        )
        if binary is not None
    ]
    stable_officials = [item for item in officials if item.version[3] == 1]
    official = max(
        stable_officials or officials,
        key=lambda item: item.version,
        default=None,
    )

    mode = os.environ.get("CODEX_STATEBAR_CODEX_MODE", "auto").strip().lower()
    if mode == "patched":
        return LaunchDecision(
            patched,
            patched,
            official,
            "forced patched Codex" if patched else "patched Codex is not installed",
        )
    if mode == "official":
        return LaunchDecision(
            official,
            patched,
            official,
            "forced official Codex" if official else "official Codex was not found",
        )
    if official and patched is None:
        return LaunchDecision(
            official,
            patched,
            official,
            "patched Codex is not installed",
        )
    if official and patched and official.version > patched.version:
        return LaunchDecision(
            official,
            patched,
            official,
            "official Codex is newer than the patched build",
        )
    if patched:
        return LaunchDecision(patched, patched, official, "patched Codex is current")
    if official:
        return LaunchDecision(
            official,
            patched,
            official,
            "patched Codex is not installed",
        )
    return LaunchDecision(None, None, None, "no usable Codex binary was found")


def launch(args: Sequence[str]) -> int:
    decision = inspect_install()
    selected = decision.selected
    if selected is None:
        print(f"codex-statebar: {decision.reason}", file=sys.stderr)
        return 127
    # Selecting a newer official Codex is the healthy default. The native
    # status-line fallback remains available, and `cxs doctor` reports the full
    # routing decision when diagnostics are needed, so normal launches stay quiet.
    #
    # A frozen cxs process can be replaced by Codex, which may later invoke cxs
    # again through hooks. PyInstaller otherwise treats that nested invocation as
    # its own child even though Codex is now between the two processes, and recent
    # bootloaders reject it because the immediate parent executable is different.
    # Mark later frozen descendants as independent before replacing this process.
    os.execve(
        str(selected.path),
        [str(selected.path), *args],
        independent_process_environment(),
    )
    return 127
