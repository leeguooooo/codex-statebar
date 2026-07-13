"""Codex CLI setup helpers for the patched command-status-line build."""

from __future__ import annotations

import os
import json
import re
import shutil
from pathlib import Path
from typing import Optional, Tuple

from .cache import atomic_write_text

CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
SETTINGS_PATH = CODEX_HOME / "config.toml"
COMMANDS_DIR = CODEX_HOME / "prompts"
SKILLS_DIR = CODEX_HOME / "skills"
OUR_COMMAND_NAMES = ("cxs", "codex-statebar")

NATIVE_ITEMS = (
    "model-with-reasoning",
    "current-dir",
    "git-branch",
    "context-remaining",
    "five-hour-limit",
    "weekly-limit",
    "fast-mode",
)
_MARKER = "# managed by codex-statebar"


def _resolve_cs_command() -> str:
    return shutil.which("cxs") or shutil.which("codex-statebar") or "cxs"


def _statusline_config(fast: bool = False, refresh_interval: int = 1) -> dict:
    """Compatibility descriptor used by diagnostics and older callers."""
    return {
        "items": ["command", _resolve_cs_command(), "render"],
        "colors": True,
        "external": True,
    }


def _is_our_statusline(entry: object) -> bool:
    if isinstance(entry, dict):
        command = str(entry.get("command") or "").split()
        return bool(command and Path(command[0]).name in OUR_COMMAND_NAMES)
    if isinstance(entry, (list, tuple)):
        return (
            len(entry) >= 2
            and entry[0] == "command"
            and Path(str(entry[1])).name in OUR_COMMAND_NAMES
        ) or tuple(entry) == NATIVE_ITEMS
    return False


def _existing_uses_render(existing: object) -> bool:
    return (
        isinstance(existing, (list, tuple))
        and len(existing) >= 3
        and entry_is_our_command(existing[1])
        and existing[2] == "render"
    )


def entry_is_our_command(value: object) -> bool:
    return Path(str(value)).name in OUR_COMMAND_NAMES


def _array_literal() -> str:
    items = ("command", _resolve_cs_command(), "render")
    return "[" + ", ".join(json.dumps(item) for item in items) + "]"


def _upsert_tui(text: str) -> str:
    """Update only two keys in [tui], preserving the rest of config.toml."""
    lines = text.splitlines()
    start: Optional[int] = None
    end = len(lines)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "[tui]":
            start = index
            continue
        if start is not None and index > start and re.match(r"^\s*\[[^[]", line):
            end = index
            break

    desired = [
        _MARKER,
        f"status_line = {_array_literal()}",
        "status_line_use_colors = true",
    ]
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(["[tui]", *desired])
        return "\n".join(lines) + "\n"

    section = lines[start + 1:end]
    section = [
        line for line in section
        if line.strip() != _MARKER
        and not re.match(r"^\s*status_line\s*=", line)
        and not re.match(r"^\s*status_line_use_colors\s*=", line)
    ]
    lines[start + 1:end] = [*desired, *section]
    return "\n".join(lines) + "\n"


def _configure(path: Path) -> Tuple[bool, str]:
    try:
        original = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError as exc:
        return False, f"Could not read {path}: {exc}"
    updated = _upsert_tui(original)
    if updated == original:
        return False, f"Codex command status line already configured in {path}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"Could not create {path.parent}: {exc}"
    if not atomic_write_text(path, updated):
        return False, f"Could not write {path}"
    return True, f"Configured Codex command status line in {path}"


def is_statusline_configured() -> bool:
    try:
        text = SETTINGS_PATH.read_text(encoding="utf-8")
    except OSError:
        return False
    return (
        _MARKER in text
        and 'status_line = ["command",' in text
        and '"render"]' in text
    )


def ensure_statusline_configured(fast: Optional[bool] = None) -> Tuple[bool, str]:
    return _configure(SETTINGS_PATH)


def project_settings_path(project_dir: Path) -> Path:
    return project_dir / ".codex" / "config.toml"


def ensure_project_statusline_configured(
    project_dir: Path,
    fast: bool = True,
    refresh_interval: int = 1,
) -> Tuple[bool, str]:
    try:
        root = project_dir.expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        return False, f"Could not resolve project path: {exc}"
    if not root.is_dir():
        return False, f"Project directory not found: {root}"
    return _configure(project_settings_path(root))


def install_commands(force: bool = False) -> Tuple[int, list[str]]:
    # Codex slash commands are built in; the old Claude command files are not
    # copied because their frontmatter and settings semantics are incompatible.
    return 0, []


def install_skills(force: bool = False) -> Tuple[int, list[str]]:
    source = Path(__file__).resolve().parent / "skills" / "codex-statebar"
    if not source.is_dir():
        return 0, []
    destination = SKILLS_DIR / "codex-statebar"
    if destination.exists() and not force:
        return 0, [str(destination)]
    try:
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
    except OSError:
        return 0, [str(destination)]
    return 1, []


def run_setup(verbose: bool = True, install_cmds: bool = True,
              fast: bool = True) -> int:
    changed, message = ensure_statusline_configured(fast=fast)
    ok = changed or "already configured" in message
    if verbose:
        print(f"{'✓' if ok else '!'} {message}")
        print("  Requires the codex-statebar patched Codex binary.")
        print("  The rich cxs renderer is embedded below the composer.")
        if changed:
            print("  Restart Codex CLI to load the command status-line configuration.")
    if install_cmds:
        installed, _ = install_skills()
        if verbose and installed:
            print(f"✓ Installed Codex statebar skill to {SKILLS_DIR}")
    return 0 if ok else 1
