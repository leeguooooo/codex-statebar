"""Actual git/OCS integration, updater scheduling and appearance contracts."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from codex_statebar import config, identity, ocs, picker, styles, update_check
from codex_statebar.themes import get_theme

SID = '01a16e63-9345-7ec0-9980-4a19a6279df9'


def git(cwd, *args):
    return subprocess.run(['git', '-C', str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout


def test_real_worktree_identity_and_ghost_count(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-b', 'main')
    git(repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
        'commit', '--allow-empty', '-m', 'fixture')
    checkout = tmp_path / 'repo-feature'
    git(repo, 'worktree', 'add', '-b', 'feature', str(checkout))
    info = identity.resolve_identity({'workspace_current_dir': str(checkout)})
    assert info.project_name == 'repo'
    assert info.worktree_name == 'repo-feature'
    assert info.is_worktree and info.worktree_count == 1
    text = styles.render_identity_line(info, theme=get_theme('graphite'), dirty=None, use_color=False)
    assert text.startswith('worktree (🌳1) ⤷ repo')  # redundant branch name hidden
    main = identity.resolve_identity({'workspace_current_dir': str(repo)})
    assert not main.is_worktree and main.worktree_count == 1
    (checkout / '.git').unlink()  # prunable registry entry must not be counted
    identity._WORKTREE_COUNTS.clear()
    assert identity.resolve_identity({'workspace_current_dir': str(repo)}).worktree_count == 0


def test_ocs_exact_codex_thread_and_no_claude_identity():
    good = {'host': 'codex', 'id': 'codex-01a16e63', 'threadId': SID, 'name': 'boss'}
    assert ocs.parse_codex_whoami(json.dumps(good), SID) == {'id': 'codex-' + SID, 'name': 'boss'}
    assert ocs.parse_codex_whoami(json.dumps({**good, 'threadId': 'other'}), SID) is None
    assert ocs.parse_codex_whoami(json.dumps({**good, 'host': 'claude'}), SID) is None


@pytest.mark.skipif(os.name == 'nt', reason='executable fixture uses a POSIX shebang')
def test_ocs_refresh_calls_read_only_cli_for_exact_session(tmp_path, monkeypatch):
    fake = tmp_path / 'ocs'
    fake.write_text(f'''#!{sys.executable}
import json, os, sys
assert os.environ['CODEX_THREAD_ID'] == {SID!r}
if sys.argv[1:] == ['whoami','--json']:
 print(json.dumps({{'host':'codex','id':'codex-01a16e63','name':'boss','threadId':{SID!r}}}))
elif sys.argv[1:] == ['inbox','--json']:
 print('[{{"unread":3}}]')
elif sys.argv[1:] == ['lan','status','--json']:
 print('{{"running":true,"peers":[]}}')
else:
 sys.exit(2)
''')
    fake.chmod(0o755)
    monkeypatch.setattr(ocs, 'find_ocs', lambda: str(fake))
    ocs.refresh(SID)
    assert ocs.ocs_label(SID, spawn=False) == 'boss · codex-' + SID
    assert ocs.ocs_status(SID) == {'unread': 3, 'lan': {'running': True, 'peers': [], 'more': 0}}


def test_ocs_future_inflight_does_not_block(monkeypatch):
    ocs.mark_inflight(SID)
    p = ocs._inflight_path(SID)
    p.write_text(json.dumps({'ts': time.time() + 3600}))
    assert not ocs.is_inflight(SID)


def test_ocs_locks_are_bounded_and_exclusive():
    first = ocs.try_claim(SID)
    assert first is not None
    try:
        assert ocs.try_claim(SID) is None
    finally:
        first.close()
    for i in range(1000):
        lock = ocs.try_claim(str(i))
        if lock:
            lock.close()
    assert len(list(ocs._cache_root().glob('*.lock'))) <= 256


def test_scheduler_throttles_and_handles_future_clock(monkeypatch):
    monkeypatch.delenv('CODEX_STATEBAR_NO_UPDATE')
    calls = []
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: calls.append(a))
    for _ in range(100):
        update_check.schedule()
    assert len(calls) == 1
    marker = update_check._root() / 'last_update_check'
    future = time.time() + 10000
    os.utime(marker, (future, future))
    update_check.schedule()
    assert len(calls) == 2


def test_update_checks_write_hint_and_honor_disabled_install(monkeypatch):
    from codex_statebar import updater
    calls = []
    monkeypatch.setattr(updater, '_fetch', lambda _: b'{"tag_name":"v99.0.0"}')
    monkeypatch.setattr(updater, 'upgrade_current_install', lambda: calls.append(True) or (True, 'upgraded'))
    monkeypatch.setattr(config, 'load_config', lambda: config.StatusbarConfig(auto_upgrade=False))
    assert update_check.run()[0]
    assert not calls
    assert styles._update_hint() == '99.0.0'
    monkeypatch.setattr(config, 'load_config', lambda: config.StatusbarConfig(auto_upgrade=True))
    assert update_check.run()[0]
    assert calls == [True]


def test_picker_applies_and_acknowledges_new_looks(tmp_path, monkeypatch):
    path = tmp_path / 'config.json'
    monkeypatch.setattr(config, 'CONFIG_PATH', path)
    monkeypatch.setattr(config, 'CLAUDE_CONFIG_PATH', tmp_path / 'none')
    picker.apply('thin')
    cfg = config.load_config()
    assert cfg.style == 'aurora' and cfg.bar_glyph == 'thin'
    assert cfg.looks_seen == config.LOOKS_VERSION
    out = styles.render('aurora', msgs_pct=None, weekly_pct=30, reset_5h='', reset_7d='',
                        model='Codex', bar_glyph=cfg.bar_glyph, use_color=False,
                        show_five_hour=False, ocs_text='codex-' + SID, ocs_unread=3,
                        looks_hint=True)
    assert '━' in out and '5h' not in out
    assert 'ocs codex-' + SID in out and '✉3' in out and 'cxs theme' in out


def test_narrow_stack_drop_and_looks_language():
    args = dict(msgs_pct=None, weekly_pct=30, reset_5h='', reset_7d='',
                model='a very long model name', show_five_hour=False,
                use_color=False, max_width=38)
    stack = styles.render('aurora', **args, narrow_layout='stack')
    drop = styles.render('aurora', **args, narrow_layout='drop')
    assert stack.count('\n') > drop.count('\n')
    zh = styles.render('aurora', **args, ui_lang='zh', looks_hint=True)
    assert '外观有更新' in zh


def test_safe_repository_links_strip_credentials_and_clip(tmp_path):
    from codex_statebar._display import clip_lines
    assert identity.remote_web_url('https://user:secret@example.com/owner/repo.git') == 'https://example.com/owner/repo'
    assert identity.remote_web_url('file:///secret') is None
    info = identity.IdentityInfo('repo', True, 'feature/a', False, None, '/repo', web_url='https://example.com/o/r')
    text = styles.render_identity_line(info, theme=get_theme('graphite'), dirty=False)
    assert '\x1b]8;;https://example.com/o/r' in text
    assert '/tree/feature/a' in text
    clipped = clip_lines(text, 28)
    assert '\x1b]8;;\x07' in clipped
    assert 'https://' not in styles.render_identity_line(info, theme=get_theme('graphite'), dirty=False, use_color=False)
