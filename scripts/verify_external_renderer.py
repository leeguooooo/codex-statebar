"""Verify full live quota rendering without any session, rollout, or authentication."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

root = Path(__file__).resolve().parent.parent
results = []
with tempfile.TemporaryDirectory(prefix='cxs-render-parity-') as directory:
    home = Path(directory)
    codex = home / '.codex'
    codex.mkdir()
    (codex / 'codex-statebar.json').write_text(json.dumps({
        'style': 'aurora', 'looks_seen': 0, 'show_resets': False,
        'show_party': False, 'show_ocs': False,
    }))
    for width in [118, 78, 38]:
        payload = {'_codex_live': True, 'cwd': str(home),
            'model': {'id': 'gpt-6.1-terra', 'display_name': 'GPT-6.1 Terra'},
            'context': {'used_percent': 35, 'window_tokens': 1000000},
            'reasoning_effort': 'low', 'fast_mode': False, 'terminal_columns': width,
            'limits': {
                'five_hour': {'used_percent': 100, 'resets_at': int(time.time()) + 7320},
                'weekly': {'used_percent': 19, 'resets_at': int(time.time()) + 460800},
            }}
        env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), CODEX_HOME=str(codex),
                   PYTHONIOENCODING='utf-8',
                   PYTHONPATH=str(root / 'src'), CODEX_STATEBAR_NO_UPDATE='1')
        code = 'from codex_statebar.core import main; main(_suppress_side_effects=True, use_color=False)'
        process = subprocess.run([sys.executable, '-c', code], input=json.dumps(payload),
            text=True, encoding='utf-8', capture_output=True, cwd=home, env=env)
        assert process.returncode == 0, process.stderr
        plain = process.stdout
        assert '5h' in plain and '7d' in plain, plain
        assert '100%' in plain and '19%' in plain, plain
        assert '⏰--' not in plain, plain
        assert 'New looks' in plain, plain
        assert 'effort:low' in plain and 'fast:off' in plain, plain
        assert len(plain.strip().splitlines()) <= 6, plain
        results.append({'columns': width, 'without_rollout': True, 'rendered': plain.rstrip()})
print(json.dumps(results, indent=2))
