"""Codex rollout JSONL data source.

The statebar reads only local session records under ``$CODEX_HOME/sessions``
and ``$CODEX_HOME/archived_sessions``.
It never reads ``auth.json`` and never sends prompt or session data anywhere.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

TAIL_BYTES = 2 * 1024 * 1024
ROUTING_BYTES = 16 * 1024
RECENT_ROLLOUT_SECONDS = 48 * 60 * 60
MAX_RECENT_ROLLOUTS = 512


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()


def _json_lines(blob: bytes) -> Iterable[dict]:
    for raw in blob.splitlines():
        try:
            item = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(item, dict):
            yield item


def _tail(path: Path) -> bytes:
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - TAIL_BYTES))
        data = handle.read()
    if size > TAIL_BYTES:
        _, _, data = data.partition(b"\n")
    return data


def _meta(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            raw = handle.readline(TAIL_BYTES)
        event = json.loads(raw)
        if event.get("type") == "session_meta":
            payload = event.get("payload")
            return payload if isinstance(payload, dict) else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        pass
    return {}


_ROUTING_FIELD = re.compile(
    rb'"(?P<key>cwd|source|originator)"\s*:\s*"(?P<value>(?:\\.|[^"\\])*)"'
)


def _routing_meta(path: Path) -> dict:
    """Read only early routing fields from a potentially huge metadata line."""
    try:
        with path.open("rb") as handle:
            prefix = handle.read(ROUTING_BYTES)
    except OSError:
        return {}
    result = {}
    for match in _ROUTING_FIELD.finditer(prefix):
        try:
            # Decode JSON string escapes without parsing the intentionally
            # truncated (and therefore invalid) full session_meta object.
            result[match.group("key").decode("ascii")] = json.loads(
                b'"' + match.group("value") + b'"'
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return result


def _rollout_roots() -> Iterable[Path]:
    home = codex_home()
    for name in ("sessions", "archived_sessions"):
        root = home / name
        if root.is_dir():
            yield root


def _rollout_files(root: Path, *, recent_only: bool = False) -> Iterable[Path]:
    """Yield newest rollouts first for both nested and flat Codex stores."""
    try:
        import heapq
        cutoff = time.time() - RECENT_ROLLOUT_SECONDS
        def candidates():
            for path in root.rglob("rollout-*.jsonl"):
                try:
                    modified = path.stat().st_mtime
                except OSError:
                    continue
                if not recent_only or modified >= cutoff:
                    yield modified, path
        # Discovery memory stays bounded even after years of session history.
        ordered = (heapq.nlargest(MAX_RECENT_ROLLOUTS, candidates()) if recent_only
                   else sorted(candidates(), reverse=True))
        yield from (path for _, path in ordered)
    except OSError:
        return


def _is_cli_session(meta: dict) -> bool:
    """True only for a top-level Codex terminal session.

    Codex Desktop writes rollouts into the same store and often shares the
    same cwd. A standalone shell command cannot know which Desktop tab the
    user meant, so silently selecting one produces convincing but incorrect
    context numbers. Top-level terminal sessions identify themselves with
    ``source=cli``; subagent sources are structured objects and are excluded.
    """
    return meta.get("source") == "cli"


def find_rollout(thread_id: str = "", cwd: str = "",
                 explicit_path: str = "") -> Optional[Path]:
    if explicit_path:
        path = Path(explicit_path).expanduser()
        if path.is_file():
            return path
        return None

    roots = list(_rollout_roots())
    if not roots:
        return None
    if thread_id:
        # IDs are data, never glob expressions. A miss must not select a
        # different session just because it happens to share the cwd.
        matches = []
        for root in roots:
            for path in root.rglob("rollout-*.jsonl"):
                if path.name.endswith(f"-{thread_id}.jsonl"):
                    meta = _meta(path)
                    if (meta.get("id") or meta.get("session_id")) == thread_id:
                        matches.append(path)
        if matches:
            return max(matches, key=lambda p: p.stat().st_mtime)
        return None

    try:
        wanted = str(Path(cwd or os.getcwd()).expanduser().resolve())
    except (OSError, RuntimeError):
        wanted = cwd or os.getcwd()
    source = os.environ.get("CODEX_STATEBAR_SOURCE", "cli").strip().lower()
    candidates = sorted(
        (path for root in roots for path in _rollout_files(root, recent_only=True)),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        try:
            meta = _routing_meta(path)
            recorded = meta.get("cwd")
            source_ok = source == "any" or (
                source == "desktop" and meta.get("originator") == "Codex Desktop"
            ) or (source == "cli" and _is_cli_session(meta))
            if (source_ok and recorded and
                    str(Path(recorded).expanduser().resolve()) == wanted):
                return path
        except (OSError, RuntimeError):
            continue
    # Never leak another project's session into this bar. A cwd miss is an
    # honest "no live rollout", not permission to use the newest global one.
    return None


def _external_payload(data: dict) -> Dict[str, Any]:
    """Normalize payloads used by proposed/patched Codex status commands."""
    out: Dict[str, Any] = {}
    model = data.get("model")
    if isinstance(model, dict):
        out["model_id"] = model.get("id") or model.get("name")
        out["display_name"] = model.get("display_name") or out.get("model_id")
    elif isinstance(model, str):
        out["model_id"] = model
        out["display_name"] = model
    out["session_id"] = data.get("thread_id") or data.get("session_id") or data.get("id")
    out["effort_level"] = data.get("reasoning_effort") or data.get("effort")
    out["fast_mode"] = data.get("fast_mode")
    columns = _finite_number(data.get("terminal_columns"))
    if columns is not None and 0 < columns <= 10000:
        out["terminal_columns"] = int(columns)
    out["codex_version"] = data.get("codex_version") or data.get("version")
    workspace = data.get("workspace")
    if isinstance(workspace, dict):
        out["workspace_current_dir"] = workspace.get("current_dir") or workspace.get("cwd")
        out["workspace_project_dir"] = workspace.get("project_dir")
    else:
        out["workspace_current_dir"] = data.get("cwd")

    context = data.get("context") or data.get("context_window")
    if isinstance(context, dict):
        used_pct = _finite_number(context.get("used_percent", context.get("used_percentage")))
        if used_pct is not None:
            used_pct = min(100.0, max(0.0, used_pct))
        out["context_used_pct"] = used_pct
        if isinstance(used_pct, (int, float)):
            out["context_remaining_pct"] = max(0.0, 100.0 - float(used_pct))
        out["context_window_size"] = _finite_number(context.get(
            "window_tokens", context.get("context_window_size")))
        out["total_input_tokens"] = _finite_number(context.get("used_tokens"))

    limits = data.get("limits")
    if isinstance(limits, dict):
        for key, suffix in (("five_hour", ""), ("weekly", "_7d"),
                            ("seven_day", "_7d")):
            window = limits.get(key)
            if isinstance(window, dict):
                fields = _rate_limit_fields({"primary": {
                    "used_percent": window.get("used_percent", window.get("used_percentage")),
                    "window_minutes": 10080 if suffix else 300,
                    "resets_at": window.get("resets_at"),
                }})
                out.update(fields)
    return {k: v for k, v in out.items() if v is not None and v != ""}


def _finite_number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _rate_limit_fields(rate_limits: dict) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in ("primary", "secondary"):
        window = rate_limits.get(key)
        if not isinstance(window, dict):
            continue
        try:
            minutes = int(window.get("window_minutes") or 0)
            pct = float(window["used_percent"])
            if not math.isfinite(pct) or pct < 0 or pct >= 100000:
                continue
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if 240 <= minutes <= 360:
            suffix = ""
        elif 6 * 1440 <= minutes <= 8 * 1440:
            suffix = "_7d"
        else:
            continue
        reset = window.get("resets_at")
        try:
            reset = float(reset) if reset is not None else None
            if reset is not None and not math.isfinite(reset):
                reset = None
        except (TypeError, ValueError, OverflowError):
            reset = None
        out[f"rate_limit{suffix}_pct"] = (
            None if reset is not None and reset <= time.time() else pct)
        out[f"rate_limit{suffix}_resets_at"] = reset
    return out


def collect_status(data: Optional[dict] = None) -> Dict[str, Any]:
    data = data if isinstance(data, dict) else {}
    out = _external_payload(data)
    if out:
        out["_has_stdin"] = True
    session_env = data.get("_cs_env")
    if isinstance(session_env, dict):
        out["_session_env"] = {str(k): str(v) for k, v in session_env.items()}
    thread_id = str(out.get("session_id") or
                    os.environ.get("CODEX_STATEBAR_THREAD_ID") or "")
    cwd = str(out.get("workspace_current_dir") or data.get("cwd") or os.getcwd())
    explicit = str(data.get("rollout_path") or data.get("transcript_path") or "")
    if data.get("_codex_live"):
        thread_id = str(out.get("session_id") or "")
    if data.get("_codex_live") and not (thread_id or explicit):
        # A newly opened TUI does not yet have an attached thread. Never borrow
        # another terminal's rollout merely because it has the same cwd.
        return out
    path = find_rollout(thread_id, cwd, explicit)
    if path is None:
        return out

    out["_has_stdin"] = True
    out["transcript_path"] = str(path)
    out["rollout_path"] = str(path)
    meta = _meta(path)
    defaults = {
        "session_id": meta.get("id") or meta.get("session_id"),
        "workspace_current_dir": meta.get("cwd") or cwd,
        "workspace_project_dir": meta.get("cwd") or cwd,
        "codex_version": meta.get("cli_version"),
    }
    out["session_source"] = meta.get("source")
    out["session_originator"] = meta.get("originator")
    for key, value in defaults.items():
        if not out.get(key) and value is not None:
            out[key] = value

    token_event: Optional[dict] = None
    quota_fields: Dict[str, Any] = {}
    turn_event: Optional[dict] = None
    try:
        events = list(_json_lines(_tail(path)))
    except OSError:
        return out
    for event in reversed(events):
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if (event.get("type") == "event_msg"
                and payload.get("type") == "token_count"):
            info = payload.get("info")
            if (token_event is None and isinstance(info, dict)
                    and isinstance(info.get("last_token_usage"), dict)
                    and _finite_number(info["last_token_usage"].get("total_tokens")) is not None):
                token_event = payload
            limits = payload.get("rate_limits")
            if isinstance(limits, dict):
                for key, value in _rate_limit_fields(limits).items():
                    quota_fields.setdefault(key, value)
                if limits.get("plan_type"):
                    quota_fields.setdefault("plan_type", limits["plan_type"])
        elif turn_event is None and event.get("type") == "turn_context":
            turn_event = payload
        # Context and quota updates are independent. Keep scanning within the
        # bounded tail to recover a window omitted by a later token event.

    if turn_event:
        for key, value in (
            ("model_id", turn_event.get("model")),
            ("effort_level", turn_event.get("effort")),
        ):
            if not out.get(key) and value is not None:
                out[key] = value
        out["display_name"] = out.get("display_name") or out.get("model_id")
        out["approval_policy"] = turn_event.get("approval_policy")
        out["sandbox_policy"] = turn_event.get("sandbox_policy")

    if token_event:
        info = token_event.get("info")
        if isinstance(info, dict):
            usage = info.get("last_token_usage")
            if isinstance(usage, dict):
                # Codex reports cached_input_tokens as a subset of
                # input_tokens. Cached tokens are discounted for billing but
                # still occupy the model context, so keep them in the context
                # numerator and expose the billable subset separately.
                try:
                    cached = max(0, int(usage.get("cached_input_tokens") or 0))
                    total = max(0, int(usage.get("total_tokens") or 0))
                    input_tokens = max(0, int(usage.get("input_tokens") or 0))
                    output_tokens = max(0, int(usage.get("output_tokens") or 0))
                except (TypeError, ValueError, OverflowError):
                    cached = total = input_tokens = output_tokens = 0
                used = total
                window = _finite_number(info.get("model_context_window") or meta.get("context_window")) or 0
                out["context_window_size"] = window
                out["cached_input_tokens"] = cached
                out["billable_input_tokens"] = max(0, input_tokens - cached)
                out["total_input_tokens"] = input_tokens
                out["total_output_tokens"] = output_tokens
                if window > 0:
                    pct = min(100.0, max(0.0, float(used) / float(window) * 100.0))
                    out["context_used_pct"] = pct
                    out["context_remaining_pct"] = 100.0 - pct
    out.update(quota_fields)
    if data.get("_codex_live"):
        live = _external_payload(data)
        # The current TUI's context survives compaction/model changes even
        # before the matching token event has reached the rollout file.
        for key in ("context_used_pct", "context_remaining_pct", "context_window_size"):
            if key in live:
                out[key] = live[key]
        if "rate_limit_7d_pct" in live:
            out["rate_limit_7d_pct"] = live["rate_limit_7d_pct"]

    out["model_id"] = out.get("model_id") or "unknown"
    out["display_name"] = out.get("display_name") or out["model_id"]
    return out


def collect_from_stdin() -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
            if raw.strip():
                candidate = json.loads(raw)
                if isinstance(candidate, dict):
                    data = candidate
    except (OSError, json.JSONDecodeError):
        pass
    return collect_status(data)
