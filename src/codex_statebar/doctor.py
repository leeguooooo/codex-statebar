"""Read-only diagnostics for Codex Statebar."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


_EXTERNAL_STATUS_MARKER = b"CODEX_STATUS_LINE"


def _ansi() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def _paint(code: str, text: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if _ansi() else text


def _line(label: str, value: object, ok: bool = True) -> None:
    mark = _paint("32" if ok else "31", "✓" if ok else "✗")
    print(f"  {mark} {label:<24} {value}")


def _binary_contains(path: str, needle: bytes) -> bool:
    """Scan a binary for a patch marker without loading it all into memory."""
    overlap = max(0, len(needle) - 1)
    tail = b""
    try:
        with open(path, "rb") as binary:
            while chunk := binary.read(1024 * 1024):
                data = tail + chunk
                if needle in data:
                    return True
                tail = data[-overlap:] if overlap else b""
    except OSError:
        return False
    return False


def run() -> int:
    from .codex_launcher import (
        _is_cmux_launcher,
        _is_managed_launcher,
        exec_alias_path,
        independent_process_environment,
        inspect_install,
    )

    print("\n  cxs doctor — Codex Statebar self-check")
    print(f"  {_paint('2', '─' * 64)}")

    binary = shutil.which("cxs") or shutil.which("codex-statebar")
    _line("statebar binary", binary or "not on PATH", binary is not None)
    try:
        from . import __version__
        _line("statebar version", __version__)
    except Exception as exc:
        _line("statebar version", exc, False)
    _line("python", f"{sys.version.split()[0]} ({sys.executable})")

    from pathlib import Path

    launch_decision = inspect_install()
    codex = shutil.which("codex")
    if codex and (_is_managed_launcher(Path(codex)) or _is_cmux_launcher(Path(codex))):
        # A routing wrapper (managed or cmux shim) would reach launch and publish
        # the exec link, so doctor reports the decision it already made instead.
        chosen = launch_decision.selected
        _line(
            "Codex CLI",
            f"{chosen.version_text} ({chosen.path}); PATH routing wrapper not executed" if chosen
            else "no usable Codex; PATH routing wrapper not executed",
            chosen is not None,
        )
    elif codex:
        try:
            completed = subprocess.run(
                [codex, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                env=independent_process_environment(),
            )
            version = completed.stdout.strip() or completed.stderr.strip()
            _line("Codex CLI", f"{version} ({codex})", completed.returncode == 0)
        except (OSError, subprocess.TimeoutExpired) as exc:
            _line("Codex CLI", exc, False)
    else:
        _line("Codex CLI", "not on PATH", False)

    patched = launch_decision.patched
    official = launch_decision.official
    _line(
        "patched Codex",
        f"{patched.version_text} ({patched.path})" if patched else "not installed",
        patched is not None,
    )
    _line(
        "official Codex",
        f"{official.version_text} ({official.path})" if official else "not found on PATH",
        official is not None,
    )
    selected = launch_decision.selected
    planned = None
    if selected and selected.patched:
        try:
            planned = exec_alias_path(selected)
        except OSError:
            planned = None
    routing_ok = not (
        official
        and patched
        and official.version > patched.version
        and codex
        and _binary_contains(codex, _EXTERNAL_STATUS_MARKER)
    )
    _line(
        "Codex version routing",
        (
            f"{launch_decision.reason}; launcher selects "
            f"{selected.version_text} ({selected.path})"
            + (f"; 计划路径 {planned}，失败时回退 {selected.path}" if planned else "")
            if selected
            else launch_decision.reason
        ),
        routing_ok,
    )

    from .setup import SETTINGS_PATH, is_statusline_configured
    native_ok = is_statusline_configured()
    _line("native [tui] fallback",
          f"configured ({SETTINGS_PATH})" if native_ok
          else f"not configured — run: cxs --setup ({SETTINGS_PATH})",
          native_ok)
    rich_hook_path = str(selected.path) if selected else (codex or "")
    rich_hook = _binary_contains(rich_hook_path, _EXTERNAL_STATUS_MARKER)
    _line("rich command hook",
          "patched Codex external command enabled" if rich_hook
          else "not provided by stable Codex; cxs/tmux/watch work now",
          rich_hook)

    try:
        from .rollout import collect_status
        status = collect_status()
        rollout = status.get("rollout_path")
        _line("rollout", rollout or "not found", bool(rollout))
        _line("model", status.get("model_id") or "unknown",
              bool(status.get("model_id")))
        context = status.get("context_used_pct")
        _line("context", f"{float(context):.1f}% used" if context is not None else "missing",
              context is not None)
        weekly = status.get("rate_limit_7d_pct")
        _line("weekly limit", f"{weekly}% used" if weekly is not None else "not exposed",
              weekly is not None)
    except Exception as exc:
        _line("rollout parser", f"{type(exc).__name__}: {exc}", False)

    try:
        columns, rows = os.get_terminal_size()
        _line("terminal", f"{columns}×{rows} TERM={os.environ.get('TERM', '?')}")
    except OSError:
        _line("terminal", "no TTY (piped invocation)")

    try:
        from .config import CONFIG_PATH, load_config
        config = load_config()
        _line("render config", f"{config.style}/{config.theme}/{config.density}")
        _line("config file", CONFIG_PATH)
        disabled = os.environ.get("CODEX_STATEBAR_NO_UPDATE", "").lower() in ("1", "true", "yes")
        _line("auto-upgrade", "off (environment)" if disabled else
              ("on (GitHub Releases, every 6h)" if config.auto_upgrade else "check only"))
        from .styles import _update_hint
        latest = _update_hint()
        if latest:
            _line("new release", f"{latest} — cxs upgrade")
        from .config import LOOKS_VERSION
        _line("new looks", "cxs theme" if config.looks_seen < LOOKS_VERSION else "seen")
        from .ocs import find_ocs
        _line("OCS integration", (find_ocs() or "not installed") if config.show_ocs else "off")
        try:
            import json
            from .update_check import _root
            state = json.loads((_root() / "upgrade_status.json").read_text())
            _line("last update check", state.get("message", "unknown"), state.get("ok") is True)
        except (OSError, ValueError, AttributeError):
            pass
    except Exception as exc:
        _line("render config", exc, False)
    print()
    return 0
