"""Six-hour GitHub Release checks, detached from the render loop."""
import json
import os
import time
from pathlib import Path

INTERVAL_S = 6 * 3600


def _root():
    return Path.home() / '.cache/codex-statebar'


def _claim():
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    handle = (root / 'update.lock').open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def schedule():
    if os.environ.get('CODEX_STATEBAR_NO_UPDATE', '').lower() in ('1', 'true', 'yes'):
        return
    marker = _root() / 'last_update_check'
    now = time.time()
    try:
        if 0 <= now - marker.stat().st_mtime < INTERVAL_S:
            return
    except OSError:
        pass
    lock = _claim()
    if lock is None:
        return
    try:
        try:
            if 0 <= now - marker.stat().st_mtime < INTERVAL_S:
                return
        except OSError:
            pass
        marker.touch()
        import subprocess
        from .codex_launcher import module_command, independent_process_environment
        subprocess.Popen(module_command('codex_statebar.update_check'),
                         env=independent_process_environment(),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
    except (OSError, ValueError):
        # A failed spawn is retried next render; it never blocks the UI.
        try:
            marker.unlink()
        except OSError:
            pass
    finally:
        lock.close()


def run(*, check_only=False):
    from . import __version__
    from . import updater
    from .cache import atomic_write_text
    from .config import load_config
    release = json.loads(updater._fetch(updater.LATEST_API))
    latest = str(release.get('tag_name') or '').lstrip('v')
    if not latest or release.get('draft') or release.get('prerelease'):
        raise ValueError('No stable GitHub Release was found.')
    atomic_write_text(_root() / 'latest_version.json', json.dumps({
        'version': latest, 'checked_at': time.time()}))
    newer = updater._version_tuple(latest) > updater._version_tuple(__version__)
    if newer and not check_only and load_config().auto_upgrade:
        ok, message = updater.upgrade_current_install()
    else:
        ok = True
        message = f'cxs {latest} available; run: cxs upgrade' if newer else f'cxs {__version__} is current.'
    # Replace one small status file; do not accumulate background logs.
    atomic_write_text(_root() / 'upgrade_status.json', json.dumps({
        'ok': ok, 'message': message[:2048], 'checked_at': time.time()}))
    return ok, message


def main():
    # schedule()'s marker is the cross-session throttle; hold a different lock
    # throughout downloads/install so a slow update cannot race a later check.
    from .ocs import try_claim
    lock = try_claim('cxs-upgrade')
    if lock is None:
        return 0
    try:
        run()
        return 0
    except Exception:
        return 1
    finally:
        lock.close()


if __name__ == '__main__':
    raise SystemExit(main())
