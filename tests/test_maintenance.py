import os
from pathlib import Path

from codex_statebar import daemon, maintenance, service


def old_file(path, age, now=2000000000):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('cached')
    os.utime(path, (now - age, now - age))
    return path


def test_nested_orphan_temps_and_expired_cache_removed(tmp_path):
    old = old_file(tmp_path / 'git/.state.dead.tmp', 7200)
    fresh = old_file(tmp_path / 'git/.state.live.tmp', 10)
    git = old_file(tmp_path / 'git/old.json', 31 * 86400)
    unrelated = old_file(tmp_path / 'anything.json', 31 * 86400)
    maintenance.maintain_cache(tmp_path, now=2000000000, force=True)
    assert not old.exists() and not git.exists()
    assert fresh.exists() and unrelated.exists()


def test_idle_session_count_is_bounded_without_deleting_fresh_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(maintenance, 'MAX_IDLE_SESSIONS', 2)
    for i in range(6):
        old_file(tmp_path / f'sessions/s{i}/party_scan.json', 8000 - i)
    fresh = old_file(tmp_path / 'sessions/active/last_stdin.json', 1)
    maintenance.maintain_cache(tmp_path, now=2000000000, force=True)
    assert fresh.exists()
    assert len(list((tmp_path / 'sessions').iterdir())) == 2


def test_maintenance_does_not_follow_symlinks(tmp_path):
    external = tmp_path / 'external'
    cache = tmp_path / 'cache'
    cache.mkdir()
    secret = old_file(external / 'protected.json', 40 * 86400)
    (cache / 'git').symlink_to(external, target_is_directory=True)
    maintenance.maintain_cache(cache, now=2000000000, force=True)
    assert secret.exists()


def test_daemon_log_is_bounded_with_one_rotated_file(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, 'log_path', lambda: tmp_path / 'daemon.log')
    (tmp_path / 'daemon.log').write_bytes(b'x' * (1024 * 1024))
    daemon._log('test')
    assert (tmp_path / 'daemon.log').stat().st_size < 100
    assert (tmp_path / 'daemon.log.1').stat().st_size == 1024 * 1024
    daemon._log('y' * 2000000)
    assert (tmp_path / 'daemon.log').stat().st_size < 5000


def test_service_does_not_create_unbounded_external_logs():
    assert 'daemon.stderr.log' not in service._build_launchd_plist('/tmp/cxs')
    assert 'StandardError=null' in service._build_systemd_unit('/tmp/cxs')
