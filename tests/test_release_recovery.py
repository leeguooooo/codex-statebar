import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pending = load('pending_release')


@pytest.mark.parametrize('releases,expected', [
    ([], 'v0.5.18'),
    ([{'tagName': 'v0.5.18', 'isDraft': True}], 'v0.5.18'),
    ([{'tagName': 'v0.5.17', 'isDraft': False}], 'v0.5.18'),
    ([{'tagName': 'v0.5.18', 'isDraft': False}], ''),
])
def test_missing_or_draft_release_is_retried(releases, expected):
    assert pending.pending_tag('0.5.18', releases) == expected


@pytest.fixture
def artifacts(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return load('find_codex_artifacts')


def test_recovery_skips_incomplete_and_expired_artifacts(artifacts, monkeypatch, tmp_path):
    monkeypatch.setattr(artifacts, 'matching_sources', lambda *args: True)
    complete = [{'name': 'codex-cxs-' + target, 'expired': False}
                for target in artifacts.TARGETS]
    responses = [
        {'workflow_runs': [{'id': n, 'head_sha': str(n)} for n in (3, 2, 1)]},
        {'artifacts': complete[:-1]},
        {'artifacts': [dict(a, expired=True) for a in complete]},
        {'artifacts': complete},
    ]
    monkeypatch.setattr(artifacts, 'api', lambda *args: responses.pop(0))
    assert artifacts.find_run(tmp_path, 'owner/repo') == '1'


def test_recovery_rejects_changed_and_deleted_patch_sources(artifacts, tmp_path):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(tmp_path), *args], text=True).strip()
    git('init')
    (tmp_path / 'scripts').mkdir()
    build = tmp_path / 'scripts/build-patched-codex.sh'
    build.write_text('build fixture')
    (tmp_path / 'patches').mkdir()
    patch = tmp_path / 'patches/codex-fixture.patch'
    patch.write_text('patch fixture')
    git('add', '.')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '-m', 'fixture')
    revision = git('rev-parse', 'HEAD')
    assert artifacts.matching_sources(tmp_path, revision)
    patch.write_text('changed')
    assert not artifacts.matching_sources(tmp_path, revision)
    patch.unlink()
    assert not artifacts.matching_sources(tmp_path, revision)


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX PTY acceptance')
def test_silent_terminal_times_out_and_is_reaped(tmp_path, monkeypatch):
    import os
    import time
    verifier = load('verify_embedded_codex')
    pid_file = tmp_path / 'pid'
    monkeypatch.setenv('FIXTURE_PID_FILE', str(pid_file))
    binary = tmp_path / 'silent-codex'
    binary.write_text(f'#!{sys.executable}\n' + '''import os, signal, time
from pathlib import Path
os.close(os.open('/dev/tty', os.O_RDWR))
Path(os.environ['FIXTURE_PID_FILE']).write_text(str(os.getpid()))
signal.signal(signal.SIGINT, signal.SIG_IGN)
time.sleep(60)
''')
    binary.chmod(0o755)
    started = time.monotonic()
    with pytest.raises(AssertionError, match='footer acceptance failed'):
        verifier.verify(binary, timeout=1)
    assert time.monotonic() - started < 5
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX PTY acceptance')
def test_terminal_resize_acceptance_uses_controlling_tty(tmp_path):
    verifier = load('verify_embedded_codex')
    binary = tmp_path / 'fixture-codex'
    binary.write_text(f'#!{sys.executable}\n' + '''import os, signal, time
os.close(os.open('/dev/tty', os.O_RDWR))
def resized(*args):
    print('40' * 8, flush=True)
signal.signal(signal.SIGWINCH, resized)
print('AURORA_LINE_ONE AURORA_PROJECT_LINE AURORA_MODE_LINE', flush=True)
time.sleep(60)
''')
    binary.chmod(0o755)
    result = verifier.verify(binary, timeout=3)
    assert result['embedded_three_rows']
    assert result['resize_columns'] == 40
