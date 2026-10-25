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
        # Existing Codex frontends resolve their tool host from this same
        # directory. A different native version cannot be swapped underneath
        # a running session safely. Keep the hint for an explicit upgrade.
        manifest_url = updater._asset_url(release, 'codex-cxs-manifest.json')
        native_changed = False
        if manifest_url and os.name != 'nt':
            manifest = json.loads(updater._fetch(manifest_url))
            version = str(manifest.get('codex_version') or '')
            if version:
                from .codex_launcher import inspect_install
                installed = inspect_install().patched
                native_changed = (installed is not None and
                                  installed.version[:3] != updater._version_tuple(version)[:3])
        if native_changed:
            ok = True
            message = f'cxs {latest} available; native Codex changes — exit Codex, then run: cxs upgrade'
        elif os.name == 'nt':
            ok = True
            message = f'cxs {latest} available; reinstall after closing cxs.exe (Windows locks running executables).'
        else:
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
    except Exception as exc:
        try:
            from .cache import atomic_write_text
            atomic_write_text(_root() / "upgrade_status.json", json.dumps({
                "ok": False, "message": f"{type(exc).__name__}: {exc}"[:2048],
                "checked_at": time.time()}))
        except Exception:
            pass
        return 1
    finally:
        lock.close()


if __name__ == '__main__':
    raise SystemExit(main())
