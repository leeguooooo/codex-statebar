"""Drop Codex's own workspace crates from a cargo target dir before caching it.

Every Codex release bumps the workspace version, so those artifacts are rebuilt
anyway; third-party dependencies mostly stay the same between releases. Keeping
only the dependencies lets the next Codex version restore a warm cache while the
cache stays small enough for all platforms to fit the repository quota.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess


def workspace_names(manifest):
    metadata = json.loads(subprocess.run(
        ['cargo', 'metadata', '--no-deps', '--format-version', '1',
         '--manifest-path', str(manifest)],
        check=True, capture_output=True, text=True,
    ).stdout)
    packages, targets = set(), set()
    for package in metadata['packages']:
        packages.add(package['name'])
        for target in package['targets']:
            targets.add(target['name'])
    return packages, targets


def prune(target_dir, packages, targets):
    target_dir = Path(target_dir)
    hashed = re.compile(r'^(?:lib)?(?P<name>.+)-[0-9a-f]{16}(?:\..*)?$')
    crate_names = {name.replace('-', '_') for name in packages | targets}
    removed = 0
    for profile in [target_dir / 'release', *target_dir.glob('*/release')]:
        if not profile.is_dir():
            continue
        for sub in ('.fingerprint', 'build', 'deps', 'incremental'):
            directory = profile / sub
            if not directory.is_dir():
                continue
            for entry in directory.iterdir():
                match = hashed.match(entry.name)
                if not match or match.group('name').replace('-', '_') not in crate_names:
                    continue
                if entry.is_dir() and not entry.is_symlink():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
                removed += 1
        for name in targets:
            for entry in (profile / name, profile / (name + '.d')):
                if entry.is_file():
                    entry.unlink()
                    removed += 1
    return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', help='codex-rs/Cargo.toml of the built source')
    parser.add_argument('target_dir')
    args = parser.parse_args()
    packages, targets = workspace_names(args.manifest)
    removed = prune(args.target_dir, packages, targets)
    print(f'pruned {removed} workspace artifacts from {args.target_dir}')


if __name__ == '__main__':
    main()
