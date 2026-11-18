"""Find complete CI artifacts built from exactly the current patched sources."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from codex_build_manifest import TARGETS, fingerprint_inputs


def command(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True,
                          text=True, timeout=60).stdout


def api(repo, endpoint):
    return json.loads(command('gh', 'api', f'repos/{repo}/{endpoint}'))


def matching_sources(root, revision):
    for path in fingerprint_inputs(root):
        relative = path.relative_to(root).as_posix()
        previous = subprocess.run(
            ['git', 'show', f'{revision}:{relative}'], cwd=root,
            capture_output=True, timeout=10,
        )
        if previous.returncode or previous.stdout != path.read_bytes():
            return False
    # Reject removed inputs too: comparing only current paths misses deletion.
    previous_paths = command('git', 'ls-tree', '-r', '--name-only', revision,
                             'patches', cwd=root).splitlines()
    return sorted(p for p in previous_paths if p.startswith('patches/codex-')
                  and p.endswith('.patch')) == sorted(
                      p.relative_to(root).as_posix()
                      for p in fingerprint_inputs(root) if p.suffix == '.patch')


def find_run(root, repo):
    runs = api(repo, 'actions/workflows/release-binaries.yml/runs?status=completed&per_page=20')
    expected = {f'codex-cxs-{target}' for target in TARGETS}
    for run in runs['workflow_runs']:
        if not matching_sources(root, run['head_sha']):
            continue
        artifacts = api(repo, f"actions/runs/{run['id']}/artifacts?per_page=100")
        available = {a['name'] for a in artifacts['artifacts'] if not a['expired']}
        if expected <= available:
            return str(run['id'])
    return ''


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    run = find_run(root, os.environ['GITHUB_REPOSITORY'])
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f'source_run={run}\n')
    print(f'Reusable patched Codex artifact run: {run or "none"}')
