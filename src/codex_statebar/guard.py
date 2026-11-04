"""Keep the managed `codex` launcher in place without the user noticing.

Codex's own standalone installer (and its auto-update) writes
``~/.local/bin/codex`` and ``codex-code-mode-host`` as symlinks into
``~/.codex/packages/standalone``. That silently replaces our launcher, so Codex
starts without the statebar and most users have no idea why. A tiny OS job
watches the install directory and puts our files back:

- macOS: a LaunchAgent with ``WatchPaths`` on the directory (+ hourly backstop)
- Linux: a systemd user ``.path`` unit with ``PathChanged`` on the directory

Only links into ``~/.codex/packages/`` are treated as foreign; anything else a
user put there deliberately is left alone.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import List, Optional
from xml.sax.saxutils import escape as _xml_escape

LABEL = "com.codex-statebar.guard"
SYSTEMD_NAME = "codex-statebar-guard"
BACKSTOP_S = 3600
_TIMEOUT = 10.0

LAUNCHER = "\n".join((
    "#!/bin/sh",
    "# managed by codex-statebar",
    "set -eu",
    'bin_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)',
    'export CODEX_STATEBAR_MANAGED_DIR="$bin_dir"',
    'exec "$bin_dir/cxs" _launch-codex "$@"',
)) + "\n"


def _is_ours(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        with path.open("rb") as handle:
            return b"managed by codex-statebar" in handle.read(512)
    except OSError:
        return False


def _is_foreign_link(path: Path) -> bool:
    """A symlink written by Codex's standalone installer."""
    if not path.is_symlink():
        return False
    try:
        target = os.readlink(path)
    except OSError:
        return False
    return "/.codex/packages/" in target.replace("\\", "/")


def _write_launcher(path: Path) -> None:
    staged = path.with_name(f".{path.name}.guard.tmp")
    staged.write_text(LAUNCHER)
    os.chmod(staged, 0o755)
    os.replace(staged, path)


def repair(managed_dir: Path, *, reinstall=None) -> List[str]:
    """Restore what a foreign installer replaced; return what was fixed."""
    if not (managed_dir / "cxs").is_file():
        return []
    fixed = []
    codex = managed_dir / "codex"
    if not _is_ours(codex) and (_is_foreign_link(codex)
                                or not (codex.exists() or codex.is_symlink())):
        _write_launcher(codex)
        fixed.append("codex")
    natives = [managed_dir / "codex-cxs", managed_dir / "codex-code-mode-host"]
    if any(_is_foreign_link(path) for path in natives) or not natives[0].exists():
        if reinstall is None:
            from .updater import upgrade_current_install as reinstall
        ok, _message = reinstall()
        if ok:
            fixed.append("native")
    return fixed


def _managed_dir() -> Optional[Path]:
    entry = os.environ.get("CODEX_STATEBAR_ENTRYPOINT")
    if getattr(sys, "frozen", False) and entry:
        return Path(entry).absolute().parent
    return None


def run() -> int:
    """`cxs _guard`: invoked by launchd/systemd whenever the directory changes."""
    managed = _managed_dir()
    if managed is None:
        return 0
    try:
        repair(managed)
    except Exception:
        pass  # A guard must never crash-loop its OS job.
    return 0


# ---------------------------------------------------------------------------
# OS job installation
# ---------------------------------------------------------------------------
def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def build_plist(cxs: str, watch_dir: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{_xml_escape(cxs)}</string>
        <string>_guard</string>
    </array>
    <key>WatchPaths</key>
    <array>
        <string>{_xml_escape(watch_dir)}</string>
    </array>
    <key>StartInterval</key>
    <integer>{BACKSTOP_S}</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>/dev/null</string>
    <key>StandardErrorPath</key>
    <string>/dev/null</string>
</dict>
</plist>
"""


def systemd_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "systemd" / "user"


def build_systemd_units(cxs: str, watch_dir: str) -> dict:
    return {
        f"{SYSTEMD_NAME}.service": (
            "[Unit]\nDescription=Restore the codex-statebar Codex launcher\n\n"
            f"[Service]\nType=oneshot\nExecStart={shlex.quote(cxs)} _guard\n"),
        f"{SYSTEMD_NAME}.path": (
            "[Unit]\nDescription=Watch the codex-statebar install directory\n\n"
            f"[Path]\nPathChanged={watch_dir}\n\n"
            "[Install]\nWantedBy=default.target\n"),
    }


def _run(*command: str) -> int:
    try:
        return subprocess.run(command, capture_output=True, timeout=_TIMEOUT).returncode
    except (OSError, subprocess.TimeoutExpired):
        return 1


def ensure_installed(cxs: Optional[Path] = None) -> bool:
    """Install the OS job once; a no-op (one file read) when already current."""
    if os.environ.get("CODEX_STATEBAR_NO_GUARD", "").lower() in ("1", "true", "yes"):
        return False
    if cxs is None:
        managed = _managed_dir()
        if managed is None:
            return False
        cxs = managed / "cxs"
    watch_dir = str(cxs.parent)
    from .cache import atomic_write_text
    if sys.platform == "darwin":
        path = plist_path()
        body = build_plist(str(cxs), watch_dir)
        try:
            if path.read_text() == body:
                return True
        except OSError:
            pass
        path.parent.mkdir(parents=True, exist_ok=True)
        if not atomic_write_text(path, body):
            return False
        uid = os.getuid()
        _run("launchctl", "bootout", f"gui/{uid}/{LABEL}")
        return _run("launchctl", "bootstrap", f"gui/{uid}", str(path)) == 0
    if sys.platform.startswith("linux"):
        units = build_systemd_units(str(cxs), watch_dir)
        directory = systemd_dir()
        try:
            if all((directory / name).read_text() == body for name, body in units.items()):
                return True
        except OSError:
            pass
        directory.mkdir(parents=True, exist_ok=True)
        for name, body in units.items():
            if not atomic_write_text(directory / name, body):
                return False
        _run("systemctl", "--user", "daemon-reload")
        return _run("systemctl", "--user", "enable", "--now", f"{SYSTEMD_NAME}.path") == 0
    return False
