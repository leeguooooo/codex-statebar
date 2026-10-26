"""Public reset announcements. Rendering reads cache; an owned worker refreshes it.

These announcements never change the account's quota or reset timestamps. The
announced time is only approximate, so the account's own quota windows are
watched too: once they show a reset, that local evidence replaces the countdown.
"""
import json
import math
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

URL = "https://codex-resets.com/api/v1/status"
TTL = 300
MAX_STALE = 3600
# A detected reset stays prominent this long, and while the announcement that
# predicted it is still listed as pending upstream.
DETECTED_SHOW = 3 * 3600
DETECTED_MAX = 86400
# A new window must end at least this much later than the still-active one.
_JUMP = {"5h": 600, "7d": 3600}


def root():
    return Path.home() / ".cache" / "codex-statebar"


def read_cache():
    try:
        value = json.loads((root() / "resets.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def epoch(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.timestamp() if dt.tzinfo else None
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


# Only our own short templates are rendered, never remote announcement text.
_TEXT = {
    "en": ("Official reset · {t} ago", "Banked reset · {t} ago", "Reset announced · in {t}", "Reset announced · awaiting confirmation", "AI forecast · reset possible{p}", " · cached", "Quota reset · seen {t} ago"),
    "zh": ("官方重置 · {t}前", "储备重置 · {t}前", "已公告重置 · {t}后", "已公告重置 · 等待确认", "AI 预测 · 可能重置{p}", " · 缓存", "额度已重置 · 本机实测 {t}前"),
    "zh-TW": ("官方重置 · {t}前", "儲備重置 · {t}前", "已公告重置 · {t}後", "已公告重置 · 等待確認", "AI 預測 · 可能重置{p}", " · 快取", "額度已重置 · 本機實測 {t}前"),
    "ja": ("公式リセット · {t}前", "予備リセット · {t}前", "リセット告知 · {t}後", "リセット告知 · 実施確認待ち", "AI 予測 · リセットの可能性{p}", " · キャッシュ", "上限リセット済み · {t}前に確認"),
    "ko": ("공식 초기화 · {t} 전", "예비 초기화 · {t} 전", "초기화 공지 · {t} 후", "초기화 공지 · 실행 확인 대기", "AI 예측 · 초기화 가능성{p}", " · 캐시", "한도 초기화됨 · {t} 전 확인"),
}
_UNITS = {"en": ("m", "h", "d"), "zh": ("分钟", "小时", "天"),
          "zh-TW": ("分鐘", "小時", "天"), "ja": ("分", "時間", "日"),
          "ko": ("분", "시간", "일")}


def duration(seconds, lang, future=False):
    seconds = max(0, seconds)
    if future:
        # Keep the remaining minutes: rounding 1h17m up to 2h makes the
        # announcement look later than the source's scheduled deadline.
        minutes = max(1, math.ceil(seconds / 60))
        days, minutes = divmod(minutes, 1440)
        hours, minutes = divmod(minutes, 60)
        units = _UNITS.get(lang, _UNITS["en"])
        return "".join(str(value) + units[index]
                       for value, index in ((days, 2), (hours, 1), (minutes, 0))
                       if value)
    divisor, index = (86400, 2) if seconds >= 86400 else ((3600, 1) if seconds >= 3600 else (60, 0))
    value = int(seconds / divisor)
    return str(max(1, value)) + _UNITS.get(lang, _UNITS["en"])[index]


def _window(pct, resets_at):
    try:
        pct, resets_at = float(pct), float(resets_at)
    except (TypeError, ValueError, OverflowError):
        return None
    return (pct, resets_at) if math.isfinite(pct) and math.isfinite(resets_at) else None


def observe(quota, now=None):
    """Remember the account's quota windows; return when a reset was seen.

    Only an early reset counts: the previous window must still have been
    running when a later-ending one replaced it. Natural rollover and older
    readings from another session never trigger it.
    """
    now = time.time() if now is None else now
    path = root() / "quota_seen.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        state = state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        state = {}
    changed = False
    for key, suffix in (("5h", ""), ("7d", "_7d")):
        seen = _window(quota.get(f"rate_limit{suffix}_pct"), quota.get(f"rate_limit{suffix}_resets_at"))
        if seen is None:
            continue
        pct, resets_at = seen
        prev = state.get(key)
        prev = _window(prev.get("pct"), prev.get("resets_at")) if isinstance(prev, dict) else None
        if prev is not None and resets_at < prev[1]:
            continue  # a stale reading from the old window
        if prev is not None and prev[1] > now and resets_at > prev[1] + _JUMP[key] and pct <= prev[0]:
            state["detected_at"] = now
        if prev != (pct, resets_at):
            state[key] = {"pct": pct, "resets_at": resets_at}
            changed = True
    if changed:
        try:
            from .cache import atomic_write_text
            root().mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, json.dumps(state))
        except OSError:
            pass
    try:
        detected = float(state.get("detected_at"))
    except (TypeError, ValueError):
        return None
    return detected if math.isfinite(detected) and 0 <= now - detected <= DETECTED_MAX else None


def status_text(entry, lang="en", now=None, detected_at=None):
    now = time.time() if now is None else now
    text = _TEXT.get(lang, _TEXT["en"])
    data = entry.get("data")
    data = data if isinstance(data, dict) else {}
    scheduled = data.get("scheduled_reset")
    if detected_at is not None and 0 <= now - detected_at <= DETECTED_MAX:
        pending = isinstance(scheduled, dict) and scheduled.get("status") == "scheduled"
        announced = epoch(scheduled.get("announced_at")) if pending else None
        # Upstream lags behind the real reset; local evidence wins until the
        # announcement is resolved or the event is no longer recent.
        if now - detected_at <= DETECTED_SHOW or (pending and (announced is None or detected_at >= announced)):
            return "✓ " + text[6].format(t=duration(now - detected_at, lang))
    try:
        age = now - float(entry.get("fetched_at", 0))
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(age) or not 0 <= age <= MAX_STALE or not data:
        return ""
    parts = []
    if isinstance(scheduled, dict) and scheduled.get("status") == "scheduled":
        target = epoch(scheduled.get("scheduled_for"))
        parts.append(text[2].format(t=duration(target-now, lang, True)) if target and target > now else text[3])
    watch = data.get("active_watch")
    if not parts and isinstance(watch, dict):
        expires = epoch(watch.get("expires_at"))
        observed = epoch(watch.get("observed_at"))
        if expires and observed and observed <= now < expires:
            chance = watch.get("reset_chance_percent")
            prob = f" ({chance}%)" if type(chance) is int and 0 <= chance <= 100 else ""
            parts.append(text[4].format(p=prob))
    latest = data.get("latest_reset")
    # A scheduled announcement takes precedence over the previous reset;
    # showing both without a history label confuses past and upcoming events.
    if isinstance(latest, dict) and not (isinstance(scheduled, dict) and scheduled.get("status") == "scheduled"):
        stamp = epoch(latest.get("announced_at"))
        if stamp and stamp <= now:
            template = text[1] if latest.get("reset_type") == "banked" else text[0]
            parts.append(template.format(t=duration(now-stamp, lang)))
    if not parts:
        return ""
    return "↻ " + " · ".join(parts) + (text[5] if age > TTL else "")


def ensure_fresh(entry, now=None, force=False):
    now = time.time() if now is None else now
    marker = root() / "resets.inflight"
    owned = False
    try:
        if now < float(entry.get("retry_at", 0)) or (
                not force and 0 <= now - float(entry.get("checked_at", 0)) < TTL):
            return
        root().mkdir(parents=True, exist_ok=True)
        try:
            if now - marker.stat().st_mtime > 60:
                marker.unlink()
        except FileNotFoundError:
            pass
        fd = os.open(str(marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        owned = True
        from .codex_launcher import module_command, independent_process_environment
        subprocess.Popen(module_command("codex_statebar.resets"),
                         env=independent_process_environment(), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         close_fds=True, start_new_session=True)
    except FileExistsError:
        pass
    except (OSError, TypeError, ValueError):
        if owned:
            marker.unlink(missing_ok=True)


def reset_line(lang="en", spawn=True, quota=None):
    entry = read_cache()
    now = time.time()
    detected = observe(quota, now) if quota else None
    if spawn:
        # Refetch right after a local reset instead of waiting out the TTL.
        try:
            stale = detected is not None and float(entry.get("checked_at", 0)) < detected
        except (TypeError, ValueError):
            stale = True
        ensure_fresh(entry, now, force=stale)
    return status_text(entry, lang, now, detected)


def main():
    import urllib.request
    from urllib.error import HTTPError
    from .cache import atomic_write_text
    from .updater import _ssl_context
    entry = read_cache()
    now = time.time()
    try:
        headers = {"User-Agent": "codex-statebar", "Accept": "application/json"}
        if isinstance(entry.get("etag"), str):
            headers["If-None-Match"] = entry["etag"]
        try:
            with urllib.request.urlopen(urllib.request.Request(URL, headers=headers), timeout=6, context=_ssl_context()) as response:
                raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise ValueError("oversized status")
                payload = json.loads(raw)
                if not isinstance(payload.get("data"), dict):
                    raise ValueError("invalid status")
                entry = {"data": payload["data"], "fetched_at": now,
                         "etag": response.headers.get("ETag")}
        except HTTPError as exc:
            if exc.code == 304 and isinstance(entry.get("data"), dict):
                entry["fetched_at"] = now
            else:
                raise
    except HTTPError as exc:
        if exc.code == 429:
            try:
                entry["retry_at"] = now + max(TTL, min(86400, int(exc.headers.get("Retry-After", TTL))))
            except (TypeError, ValueError, AttributeError):
                entry["retry_at"] = now + TTL
    except Exception:
        # Preserve last good data, but retry failures only after the normal TTL.
        pass
    finally:
        entry["checked_at"] = now
        try:
            atomic_write_text(root() / "resets.json", json.dumps(entry))
        finally:
            (root() / "resets.inflight").unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    main()
