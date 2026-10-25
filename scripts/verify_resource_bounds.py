"""Soak the actual POSIX executable against an isolated synthetic rollout.

This is a bounded regression check, not proof of leak freedom over all inputs.
It never reads the developer's Codex sessions or configuration.
"""
import argparse
import fcntl
import json
import os
import re
from pathlib import Path
import signal
import stat
import subprocess
import tempfile
import time


def footprint(root):
    count = size = 0
    for path in root.rglob('*'):
        try:
            state = path.lstat()
        except FileNotFoundError:
            # Atomic cache writes rename temp files while we are sampling.
            continue
        if stat.S_ISREG(state.st_mode):
            count += 1
            size += state.st_size
    return count, size


def verify(entrypoint, seconds=60):
    entrypoint = str(Path(entrypoint).resolve())
    with tempfile.TemporaryDirectory(prefix='cxs-resource-soak-') as directory:
        root = Path(directory)
        home = root / 'home'
        home.mkdir()
        codex = home / '.codex'
        sessions = codex / 'sessions'
        sessions.mkdir(parents=True)
        (codex / 'codex-statebar.json').write_text(json.dumps({'show_ocs': False, 'looks_seen': 1}))
        rollout = sessions / 'rollout-resource-probe.jsonl'
        events = [
            {'type': 'session_meta', 'payload': {'id': 'resource-probe', 'cwd': str(home), 'source': 'cli'}},
            {'type': 'turn_context', 'payload': {'model': 'gpt-test', 'effort': 'high'}},
            {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
                'model_context_window': 200000, 'last_token_usage': {
                    'input_tokens': 39000, 'output_tokens': 1000, 'total_tokens': 40000}},
                'rate_limits': {'secondary': {'used_percent': 72, 'window_minutes': 10080,
                                             'resets_at': int(time.time() + 6 * 86400)}}}},
        ]
        rollout.write_text('\n'.join(map(json.dumps, events)) + '\n')
        env = {**os.environ, 'CODEX_STATEBAR_NO_UPDATE': '1', 'HOME': str(home), 'CODEX_HOME': str(codex),
               'CODEX_STATEBAR_RUNTIME_DIR': str(root / 'runtime'), 'TMPDIR': str(root),
               'PYINSTALLER_RESET_ENVIRONMENT': '1'}
        subprocess.run([entrypoint, '--version'], env=env, check=True, capture_output=True, timeout=30)
        probe_repo = home / 'git-probe'
        probe_repo.mkdir()
        subprocess.run(['git', 'init', '-q', str(probe_repo)], check=True)
        subprocess.run([entrypoint, '-m', 'codex_statebar._git_refresh', str(probe_repo)],
                       env=env, check=True, capture_output=True, timeout=15)
        git_state = next((home / '.cache/codex-statebar/git').glob('*.json'))
        assert json.loads(git_state.read_text())['dirty'] is False
        payload = {'_codex_live': True, 'thread_id': None, 'codex_version': '0.164.0',
                   'cwd': str(home), 'model': 'gpt-ui-probe', 'terminal_columns': 40,
                   'context': {'used_percent': 20, 'window_tokens': 200000},
                   'limits': {'weekly': {'used_percent': 73}}}
        rendered = subprocess.run([entrypoint, 'render'], input=json.dumps(payload),
                                  env={**env, 'CODEX_STATUS_LINE': '1', 'CODEX_STATEBAR_STYLE': 'aurora'},
                                  check=True, capture_output=True, text=True, timeout=15)
        plain = re.sub(r'\x1b\[[0-9;]*m', '', rendered.stdout)
        assert '73%' in plain and '20%' in plain and '5h' not in plain.lower(), plain
        assert all(len(line) <= 40 for line in plain.splitlines()), plain
        process = subprocess.Popen([entrypoint, 'watch', '--interval', '0.2', '--session',
                                    'resource-probe', '--style', 'aurora', '--no-color'],
                                   cwd=home, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        samples = []
        try:
            for index in range(seconds):
                time.sleep(1)
                assert process.poll() is None, 'watch exited during soak'
                if index == 0:
                    lease = next(p for p in (root / 'runtime').glob('*.lock') if len(p.name) == 69)
                    with lease.open('r') as handle:
                        try:
                            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            pass
                        else:
                            raise AssertionError('runtime lease was lost across exec')
                rss = int(subprocess.check_output(['ps', '-o', 'rss=', '-p', str(process.pid)], text=True))
                if index >= 4:
                    count, size = footprint(root)
                    samples.append((rss, count, size))
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
            _, error = process.communicate(timeout=15)
        assert process.returncode == 0 and not error, (process.returncode, error)
        assert not list(root.rglob('_MEI*')), 'per-launch extraction returned'
        assert len(samples) >= 2
        memory_growth = max(0, samples[-1][0] - samples[0][0])
        disk_growth = max(0, samples[-1][2] - samples[0][2])
        file_growth = max(0, samples[-1][1] - samples[0][1])
        assert memory_growth < 16 * 1024, 'RSS grew by more than 16 MiB after warmup'
        assert disk_growth < 65536 and file_growth <= 2, 'persistent state accumulated'
        return {'seconds': seconds, 'rss_min_mb': round(min(s[0] for s in samples) / 1024, 2),
                'rss_max_mb': round(max(s[0] for s in samples) / 1024, 2),
                'rss_growth_kb': memory_growth, 'disk_growth_bytes': disk_growth,
                'file_growth': file_growth, 'new_MEI_directories': 0, 'clean_exit': True,
                'native_payload_width_verified': True, 'frozen_git_helper_verified': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('entrypoint')
    parser.add_argument('--seconds', type=int, default=60)
    args = parser.parse_args()
    if args.seconds < 10:
        parser.error('--seconds must be at least 10')
    print(json.dumps(verify(args.entrypoint, args.seconds), indent=2))
