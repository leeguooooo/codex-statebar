"""Verify the frozen renderer and OCS helper against isolated real worktrees."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

SID = '11111111-2222-4333-8444-555555555555'
ANSI = re.compile(r'\x1b(?:\][^\x07\x1b]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~])')


def verify(entrypoint):
    entrypoint = str(Path(entrypoint).resolve())
    with tempfile.TemporaryDirectory(prefix='cxs-feature-acceptance-') as directory:
        root = Path(directory)
        home = root / 'home'
        home.mkdir()
        env = {**os.environ, 'HOME': str(home), 'USERPROFILE': str(home),
               'CODEX_HOME': str(home / '.codex'), 'CODEX_STATEBAR_NO_UPDATE': '1',
               'CODEX_STATEBAR_RUNTIME_DIR': str(root / 'runtime'),
               'PYINSTALLER_RESET_ENVIRONMENT': '1', 'CODEX_STATUS_LINE': '1',
               'LANG': 'en_US.UTF-8'}
        env.pop('NO_COLOR', None)
        repo = root / 'project'
        repo.mkdir()

        def run(command, **kwargs):
            return subprocess.run(command, env=env, check=True, capture_output=True,
                                  text=True, timeout=30, **kwargs)

        def git(*args):
            return run(['git', '-C', str(repo), *args])

        git('init', '-b', 'main')
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
            'commit', '--allow-empty', '-m', 'fixture')
        git('remote', 'add', 'origin', 'https://user:fixture-secret@example.com/o/project.git')
        tree = root / 'feature'
        git('worktree', 'add', '-b', 'feature', str(tree))
        bin_dir = root / 'bin'
        bin_dir.mkdir()
        fake = bin_dir / 'ocs'
        fake.write_text(f'''#!{sys.executable}
import json, os, sys
assert os.environ['CODEX_THREAD_ID'] == {SID!r}
if sys.argv[1:] == ['whoami','--json']:
 print(json.dumps({{'host':'codex','threadId':{SID!r},'id':'codex-11111111','name':'fixture'}}))
elif sys.argv[1:] == ['inbox','--json']:
 print('[{{"unread":3}}]')
elif sys.argv[1:] == ['lan','status','--json']:
 print('{{"running":true,"peers":[{{"label":"fixture-peer"}}]}}')
else:
 sys.exit(2)
''')
        fake.chmod(0o755)
        env['PATH'] = str(bin_dir) + os.pathsep + env.get('PATH', '')
        # Keep unrelated layout checks deterministic and offline.
        run([entrypoint, 'config', 'set', 'show_resets', 'false'])
        run([entrypoint, '-m', 'codex_statebar.ocs', SID])
        rollout = root / f'rollout-{SID}.jsonl'
        rollout.write_text(json.dumps({'type':'session_meta','payload':{
            'id': SID, 'cwd':str(tree), 'source':'cli'}}) + '\n')
        payload = {'_codex_live':True, 'thread_id':SID, 'rollout_path':str(rollout),
                   'cwd':str(tree), 'codex_version':'0.164.0', 'model':'gpt-feature-probe',
                   'terminal_columns':160, 'reasoning_effort':'high', 'fast_mode': True,
                   'context':{'used_percent':20,'window_tokens':200000},
                   'limits':{'weekly':{'used_percent':73}}}
        run([entrypoint, '-m', 'codex_statebar._git_refresh', str(tree)])
        result = run([entrypoint, 'render'], input=json.dumps(payload), cwd=tree)
        plain = ANSI.sub('', result.stdout)
        for token in ('73%', '20%', 'worktree (🌳1)', 'project', 'ocs fixture',
                      'codex-' + SID, '3 unread', 'fixture-peer', 'cxs theme', 'effort:high', 'fast:on'):
            assert token in plain, (token, plain)
        assert '5h' not in plain.lower() and 'fixture-secret' not in result.stdout
        assert '→0%' not in plain, plain
        assert len(plain.splitlines()) <= 6, plain
        # The real frozen config command applies the new look and dismisses its notice.
        run([entrypoint, 'config', 'set', 'style', 'aurora'])
        run([entrypoint, 'config', 'set', 'bar_glyph', 'thin'])
        result = run([entrypoint, 'render'], input=json.dumps(payload), cwd=tree)
        plain = ANSI.sub('', result.stdout)
        assert '━' in plain and 'cxs theme' not in plain, plain
        # Pending announcements take precedence over localized reset history.
        now = time.time()
        reset_cache = home / '.cache/codex-statebar/resets.json'
        reset_cache.parent.mkdir(parents=True, exist_ok=True)
        data = {'latest_reset': {'announced_at': datetime.fromtimestamp(
                    now - 8 * 3600, timezone.utc).isoformat(), 'reset_type': 'regular'},
                'scheduled_reset': {'status': 'scheduled', 'scheduled_for':
                    datetime.fromtimestamp(now + 77 * 60, timezone.utc).isoformat()}}
        reset_cache.write_text(json.dumps({'data': data, 'fetched_at': now, 'checked_at': now}))
        run([entrypoint, 'config', 'set', 'show_resets', 'true'])
        run([entrypoint, 'config', 'set', 'lang', 'ja'])
        result = run([entrypoint, 'render'], input=json.dumps(payload), cwd=tree)
        plain = ANSI.sub('', result.stdout)
        assert 'リセット告知 1時間17分後' in plain and '公式リセット' not in plain, plain
        data.pop('scheduled_reset')
        reset_cache.write_text(json.dumps({'data': data, 'fetched_at': now, 'checked_at': now}))
        result = run([entrypoint, 'render'], input=json.dumps(payload), cwd=tree)
        plain = ANSI.sub('', result.stdout)
        assert '公式リセット 8時間前' in plain and 'リセット告知' not in plain, plain
        run([entrypoint, 'config', 'set', 'show_resets', 'false'])
        result = run([entrypoint, 'render'], input=json.dumps(payload), cwd=tree)
        assert 'リセット' not in result.stdout, result.stdout
        return {'public_resets_localized_and_toggleable':True, 'frozen_ocs_helper':True, 'exact_thread':True, 'unread_and_lan':True,
                'real_worktree':True, 'new_looks_notice':True, 'thin_look_applied':True,
                'credentials_stripped':True, 'within_six_native_rows':True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('entrypoint')
    args = parser.parse_args()
    print(json.dumps(verify(args.entrypoint), indent=2))
