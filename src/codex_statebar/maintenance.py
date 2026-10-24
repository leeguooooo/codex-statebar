"""Bound disposable state; never traverse Codex rollouts or configuration."""
import os
import time
from pathlib import Path

INTERVAL_S = 1800
TEMP_AGE_S = 3600
SESSION_AGE_S = 86400
CACHE_AGE_S = 30 * 86400
MAX_IDLE_SESSIONS = 256
MAX_CACHE_FILES = 1024
LOG_BYTES = 1024 * 1024
_last_run = 0.0


def _default_root():
    return Path.home() / '.cache/codex-statebar'


def _files(directory):
    try:
        yield from (p for p in directory.iterdir() if p.is_file() and not p.is_symlink())
    except OSError:
        return


def _remove(path):
    try:
        path.unlink()
    except OSError:
        pass


def _temps(directory, now):
    for path in _files(directory):
        try:
            if (path.name.startswith('.') and path.name.endswith('.tmp')
                    and now - path.stat().st_mtime > TEMP_AGE_S):
                _remove(path)
        except OSError:
            continue


def maintain_cache(root=None, *, now=None, force=False):
    """At most one sweep per 30 minutes across standalone invocations.

    Recent writes are protected even beyond the idle-entry caps. Cached git,
    balance and session data can always be rebuilt. Symlink targets and
    unknown session files are never removed.
    """
    global _last_run
    now = time.time() if now is None else now
    if not force and 0 <= now - _last_run < INTERVAL_S:
        return
    _last_run = now
    root = Path(root) if root is not None else _default_root()
    marker = root / '.maintenance'
    try:
        if not force and 0 <= now - marker.stat().st_mtime < INTERVAL_S:
            return
    except OSError:
        pass
    try:
        if not root.is_dir():
            return
        _temps(root, now)
        for name in ('daemon.log', 'daemon.log.1', 'daemon.stdout.log', 'daemon.stderr.log'):
            path = root / name
            if path.is_symlink() or not path.exists():
                continue
            if path.stat().st_size > LOG_BYTES:
                # In-place truncation also bounds an already open launchd fd.
                with path.open('r+b') as handle:
                    handle.truncate(0)
        for name in ('git', 'balance'):
            directory = root / name
            if directory.is_symlink():
                continue
            _temps(directory, now)
            entries = sorted((p.stat().st_mtime, p) for p in _files(directory)
                             if p.suffix in ('.json', '.inflight'))
            for index, (modified, path) in enumerate(entries):
                if now - modified > CACHE_AGE_S or (len(entries) - index > MAX_CACHE_FILES
                                                     and now - modified > TEMP_AGE_S):
                    _remove(path)
        sessions = root / 'sessions'
        if sessions.is_dir() and not sessions.is_symlink():
            entries = []
            for directory in sessions.iterdir():
                if directory.is_symlink() or not directory.is_dir():
                    continue
                _temps(directory, now)
                modified = max((p.stat().st_mtime for p in _files(directory)),
                               default=directory.stat().st_mtime)
                entries.append((modified, directory))
            entries.sort()
            for index, (modified, directory) in enumerate(entries):
                if now - modified <= SESSION_AGE_S and (len(entries) - index <= MAX_IDLE_SESSIONS
                                                         or now - modified <= TEMP_AGE_S):
                    continue
                for name in ('last_stdin.json', 'rendered.ansi', 'rendered.meta.json', 'party_scan.json'):
                    _remove(directory / name)
                try:
                    directory.rmdir()
                except OSError:
                    pass
        marker.touch()
        os.utime(marker, (now, now))
    except OSError:
        # Cache maintenance is best-effort; a concurrent writer or read-only
        # filesystem must never take down a render.
        return
