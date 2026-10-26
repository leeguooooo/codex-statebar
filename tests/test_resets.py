import json
from datetime import datetime, timezone
from urllib.error import HTTPError

import pytest

from codex_statebar import config, i18n, resets

NOW = 1792936800
# conftest blanks reset_line for renderer tests; keep the real one here.
REAL_RESET_LINE = resets.reset_line


def stamp(offset):
    return datetime.fromtimestamp(NOW + offset, timezone.utc).isoformat()


def entry(**data):
    return {"fetched_at": NOW, "data": data}


def test_completed_and_banked_do_not_imply_account_quota():
    latest = {"announced_at": stamp(-8 * 3600), "reset_type": "regular"}
    assert resets.status_text(entry(latest_reset=latest), "zh", NOW) == "↻ 官方重置 · 8小时前"
    latest["reset_type"] = "banked"
    assert "Banked reset · 8h ago" in resets.status_text(entry(latest_reset=latest), "en", NOW)


def test_scheduled_deadline_never_turns_into_completed():
    scheduled = {"status": "scheduled", "scheduled_for": stamp(2 * 3600)}
    value = entry(scheduled_reset=scheduled)
    assert "2小时后" in resets.status_text(value, "zh", NOW)
    scheduled["scheduled_for"] = stamp(-1)
    assert "等待确认" in resets.status_text(value, "zh", NOW)
    scheduled["scheduled_for"] = None
    assert "等待确认" in resets.status_text(value, "zh", NOW)


@pytest.mark.parametrize("seconds,expected", [
    (1, "1分钟"), (59, "1分钟"), (60, "1分钟"),
    (61, "2分钟"), (3600, "1小时"), (4620, "1小时17分钟"),
    (7199, "2小时"), (86460, "1天1分钟"),
])
def test_scheduled_countdown_preserves_minutes(seconds, expected):
    value = entry(scheduled_reset={"status": "scheduled", "scheduled_for": stamp(seconds)})
    assert resets.status_text(value, "zh", NOW) == f"↻ 已公告重置 · {expected}后"


@pytest.mark.parametrize("offset", [4620, -1])
def test_scheduled_reset_hides_previous_completed_reset(offset):
    value = entry(
        scheduled_reset={"status": "scheduled", "scheduled_for": stamp(offset)},
        latest_reset={"announced_at": stamp(-23 * 3600), "reset_type": "regular"},
    )
    output = resets.status_text(value, "zh", NOW)
    assert "已公告重置" in output
    assert "官方重置" not in output
    if offset < 0:
        assert "等待确认" in output


def test_watch_is_expiring_ai_forecast_not_a_countdown():
    watch = {"observed_at": stamp(-100), "expires_at": stamp(100), "reset_chance_percent": 75,
             "text": "\x1b[31mremote injection"}
    value = entry(active_watch=watch)
    assert resets.status_text(value, "en", NOW) == "↻ AI forecast · reset possible (75%)"
    watch["expires_at"] = stamp(-1)
    assert resets.status_text(value, "en", NOW) == ""


def test_cache_freshness_and_bad_timestamps():
    value = entry(latest_reset={"announced_at": stamp(-500)})
    assert "cached" in resets.status_text(value, now=NOW+301)
    assert resets.status_text(value, now=NOW+3601) == ""
    value["fetched_at"] = float("nan")
    assert resets.status_text(value, now=NOW) == ""
    assert resets.epoch("2026-10-25T12:00:00") is None


@pytest.mark.parametrize("tag,lang", [("zh_CN.UTF-8", "zh"), ("zh-Hant", "zh-TW"),
    ("zh_TW.UTF-8", "zh-TW"), ("ja_JP.UTF-8", "ja"), ("ko-KR", "ko"), ("fr-FR", "en")])
def test_language_and_templates(tag, lang):
    assert i18n._normalize(tag) == lang
    assert resets.status_text(entry(latest_reset={"announced_at": stamp(-7200)}), lang, NOW)


def test_config_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"show_resets":false,"lang":"ja"}')
    cfg = config.load_config(path)
    assert cfg.show_resets is False
    assert i18n.ui_lang(cfg.lang) == "ja"


def test_refresh_uses_etag_and_retains_good_data_on_failure(monkeypatch):
    from codex_statebar.cache import atomic_write_text
    old = entry(latest_reset={"announced_at": stamp(-500)})
    old["etag"] = '"v1"'
    atomic_write_text(resets.root() / "resets.json", json.dumps(old))
    monkeypatch.setattr(resets.time, "time", lambda: NOW + 301)

    def unchanged(req, timeout, context):
        assert context.check_hostname
        assert req.get_header("If-none-match") == '"v1"'
        raise HTTPError(req.full_url, 304, "unchanged", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", unchanged)
    resets.main()
    assert resets.read_cache()["fetched_at"] == NOW + 301
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(resets.time, "time", lambda: NOW + 602)
    resets.main()
    assert resets.read_cache()["fetched_at"] == NOW + 301
    assert resets.read_cache()["checked_at"] == NOW + 602


def test_inflight_deduplicates_workers(monkeypatch):
    calls = []
    monkeypatch.setattr(resets.subprocess, "Popen", lambda *a, **kw: calls.append(a))
    resets.ensure_fresh({})
    resets.ensure_fresh({})
    assert len(calls) == 1


def test_all_styles_render_public_reset_without_color():
    from codex_statebar.styles import render
    for style in ("aurora", "classic", "capsule", "hairline"):
        out = render(style, msgs_pct=10, weekly_pct=20, reset_5h="2h", reset_7d="1d",
                     model="Codex", reset_text="↻ 官方重置 · 8小时前", use_color=False)
        assert "↻ 官方重置 8小时前" in out.splitlines()[0]
        assert "\x1b" not in out


def test_rate_limit_retry_after_is_respected(monkeypatch):
    monkeypatch.setattr(resets.time, "time", lambda: NOW)
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: (_ for _ in ()).throw(
        HTTPError(resets.URL, 429, "limited", {"Retry-After": "1800"}, None)))
    resets.main()
    assert resets.read_cache()["retry_at"] == NOW + 1800
    calls = []
    monkeypatch.setattr(resets.subprocess, "Popen", lambda *a, **kw: calls.append(a))
    resets.ensure_fresh(resets.read_cache(), NOW + 301)
    assert calls == []


def test_auto_language_uses_os_preference(monkeypatch):
    monkeypatch.setattr(i18n, "_cached", None)
    monkeypatch.setattr(i18n.sys, "platform", "darwin")
    monkeypatch.setattr(i18n, "_macos_lang", lambda: "ja")
    assert i18n.ui_lang("auto") == "ja"


def test_frozen_worker_dispatch(monkeypatch):
    from codex_statebar import cli
    monkeypatch.setattr(cli.sys, "argv", ["cxs", "-m", "codex_statebar.resets"])
    monkeypatch.setattr(resets, "main", lambda: 42)
    assert cli.main() == 42


@pytest.mark.parametrize("enabled,broken", [(True, False), (False, False), (True, True)])
def test_core_integration_is_optional_and_failure_isolated(monkeypatch, capsys, enabled, broken):
    from codex_statebar import core
    monkeypatch.setattr(config, "load_config", lambda: config.StatusbarConfig(
        show_resets=enabled, lang="ja", show_project_branch=False, show_ocs=False,
        show_party=False, show_language=False, show_cache_age=False, show_mode=False))
    monkeypatch.setattr(core, "parse_stdin_data", lambda: {
        "_has_stdin": True, "_is_codex": True, "_session_env": {},
        "rate_limit_7d_pct": 20, "rate_limit_7d_resets_at": NOW+86400,
        "model": {"id": "codex", "display_name": "Codex"}})
    calls = []
    def line(lang, quota=None):
        calls.append(lang)
        assert quota["rate_limit_7d_resets_at"] == NOW+86400
        if broken:
            raise OSError("cache unavailable")
        return "PUBLIC_RESET_SENTINEL"
    monkeypatch.setattr(resets, "reset_line", line)
    core.main(use_color=False, _suppress_side_effects=True)
    output = capsys.readouterr().out
    assert bool("PUBLIC_RESET_SENTINEL" in output) == (enabled and not broken)
    assert bool(calls) == enabled
    if enabled:
        assert calls == ["ja"]
    assert output.strip()


def quota(pct5, reset5, pct7, reset7):
    return {"rate_limit_pct": pct5, "rate_limit_resets_at": reset5,
            "rate_limit_7d_pct": pct7, "rate_limit_7d_resets_at": reset7}


def test_local_reset_overrides_inaccurate_scheduled_countdown():
    announced = {"status": "scheduled", "announced_at": stamp(-3600), "scheduled_for": stamp(4620)}
    value = entry(scheduled_reset=announced)
    assert resets.observe(quota(60, NOW + 3600, 70, NOW + 3 * 86400), NOW) is None
    # The announcement said 1h17m, but the account's windows restarted now.
    detected = resets.observe(quota(0, NOW + 5 * 3600 + 60, 0, NOW + 7 * 86400 + 60), NOW + 60)
    assert detected == NOW + 60
    output = resets.status_text(value, "zh", NOW + 180, detected)
    assert output == "✓ 额度已重置 · 本机实测 2分钟前"
    # Detection persists across renders while the API still says "scheduled".
    again = resets.observe(quota(1, NOW + 5 * 3600 + 60, 0, NOW + 7 * 86400 + 60), NOW + 4 * 3600)
    assert again == detected
    assert "额度已重置" in resets.status_text(value, "zh", NOW + 4 * 3600, again)
    # Once upstream resolves the announcement, normal history returns.
    done = entry(latest_reset={"announced_at": stamp(-3600), "reset_type": "regular"})
    done["fetched_at"] = NOW + 4 * 3600
    assert resets.status_text(done, "zh", NOW + 4 * 3600, again) == "↻ 官方重置 · 5小时前"


def test_natural_rollover_and_stale_sessions_are_not_resets():
    resets.observe(quota(80, NOW + 60, 30, NOW + 86400), NOW)
    # The 5h window ended on schedule: a new window is not an early reset.
    assert resets.observe(quota(2, NOW + 5 * 3600 + 120, 31, NOW + 86400), NOW + 120) is None
    # Another terminal still reporting the previous window is ignored.
    assert resets.observe(quota(80, NOW + 60, 30, NOW + 86400), NOW + 130) is None
    # Usage growing within the same window, and unknown windows, are ignored.
    assert resets.observe(quota(5, NOW + 5 * 3600 + 180, 35, NOW + 86400 + 30), NOW + 200) is None
    assert resets.observe(quota(None, None, None, None), NOW + 300) is None


def test_detected_reset_forces_refresh_and_renders_prominently(monkeypatch):
    from codex_statebar.cache import atomic_write_text
    from codex_statebar.styles import render_reset_segment
    from codex_statebar.themes import get_theme
    atomic_write_text(resets.root() / "resets.json", json.dumps(
        {"data": {}, "fetched_at": NOW, "checked_at": NOW}))
    calls = []
    monkeypatch.setattr(resets.subprocess, "Popen", lambda *a, **kw: calls.append(a))
    monkeypatch.setattr(resets.time, "time", lambda: NOW + 10)
    REAL_RESET_LINE("en", quota=quota(50, NOW + 3600, 50, NOW + 86400))
    assert calls == []  # within TTL
    monkeypatch.setattr(resets.time, "time", lambda: NOW + 20)
    line = REAL_RESET_LINE("en", quota=quota(0, NOW + 5 * 3600, 0, NOW + 7 * 86400))
    assert line == "✓ Quota reset · seen 1m ago"
    assert len(calls) == 1
    seg = render_reset_segment(line, theme=get_theme("graphite"))
    assert "\x1b[1m" in seg and "✓ Quota reset seen 1m ago" in seg
