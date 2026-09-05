"""Exercise the real native entrypoint with a small onedir-shaped fixture."""
import os
import io
import shutil
import signal
import subprocess
import tarfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from scripts.build_runtime_launcher import build
from codex_statebar import updater


pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX release launcher")


@pytest.fixture
def launcher(tmp_path):
    if not shutil.which("cc"):
        pytest.skip("C compiler is unavailable")
    bundle = tmp_path / "bundle"
    (bundle / "_internal").mkdir(parents=True)
    (bundle / "_internal/version").write_text("runtime-ok\n")
    (bundle / "_internal/current").symlink_to("version")
    program = bundle / "cxs"
    program.write_text(
        '#!/bin/sh\n'
        'cat "$(dirname "$0")/_internal/current"\n'
        'printf "%s\\n" "$CODEX_STATEBAR_ENTRYPOINT" "$@"\n'
        'if [ "${1:-}" = "--hold" ]; then while :; do sleep 1; done; fi\n'
    )
    program.chmod(0o755)
    entry = tmp_path / "install dir/cxs"
    digest = build(bundle, entry)
    cache = tmp_path / "runtime cache"
    env = {**os.environ, "CODEX_STATEBAR_RUNTIME_DIR": str(cache)}
    return entry, cache, digest, env


def run(launcher, *args):
    entry, _cache, _digest, env = launcher
    return subprocess.run([str(entry), *args], env=env, text=True,
                          capture_output=True, timeout=15)


def test_repeated_launches_reuse_runtime_and_preserve_args(launcher):
    entry, cache, digest, _env = launcher
    first = run(launcher, "argument with spaces", "--version")
    assert first.returncode == 0, first.stderr
    assert first.stdout.splitlines() == [
        "runtime-ok", str(entry), "argument with spaces", "--version",
    ]
    executable = cache / digest / "cxs"
    before = executable.stat()
    for _ in range(12):
        result = run(launcher)
        assert result.returncode == 0, result.stderr
    after = executable.stat()
    assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)
    assert {p.name for p in cache.iterdir()} == {digest, digest + ".lock"}
    assert (cache / digest / "_internal/current").is_symlink()


def test_concurrent_cold_starts_publish_only_one_runtime(launcher):
    _entry, cache, digest, _env = launcher
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: run(launcher), range(24)))
    assert all(r.returncode == 0 for r in results), [r.stderr for r in results]
    assert {p.name for p in cache.iterdir()} == {digest, digest + ".lock"}
    assert (cache / digest / ".complete").is_file()


def test_recovers_incomplete_extract_without_touching_old_runtime(launcher):
    _entry, cache, digest, _env = launcher
    stage = cache / (digest + ".staging")
    stage.mkdir(parents=True)
    (stage / "partial").write_text("interrupted extraction")
    (cache / (digest + ".lock")).touch()
    incomplete = cache / digest
    incomplete.mkdir()
    (incomplete / "broken").touch()
    previous = cache / "previous-release"
    previous.mkdir()
    (previous / "cxs").write_text("in-use runtime")
    result = run(launcher)
    assert result.returncode == 0, result.stderr
    assert not stage.exists()
    assert not (incomplete / "broken").exists()
    assert (previous / "cxs").read_text() == "in-use runtime"


def test_forced_exit_does_not_create_another_runtime(launcher):
    entry, cache, digest, env = launcher
    proc = subprocess.Popen([str(entry), "--hold"], env=env, stdout=subprocess.PIPE,
                            text=True, start_new_session=True)
    try:
        assert proc.stdout.readline().strip() == "runtime-ok"
        inode = (cache / digest / "cxs").stat().st_ino
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
        proc.stdout.close()
    result = run(launcher)
    assert result.returncode == 0, result.stderr
    assert (cache / digest / "cxs").stat().st_ino == inode
    assert {p.name for p in cache.iterdir()} == {digest, digest + ".lock"}


def test_cached_runtime_does_not_require_writable_cache(launcher):
    _entry, cache, digest, _env = launcher
    assert run(launcher).returncode == 0
    cache.chmod(0o500)
    try:
        assert run(launcher).returncode == 0
    finally:
        cache.chmod(0o700)


def test_build_rejects_missing_runtime(tmp_path):
    with pytest.raises(ValueError, match="Missing onedir"):
        build(tmp_path / "missing", tmp_path / "cxs")


def test_recursive_version_probe_is_rejected_before_unpacking(launcher):
    entry, cache, _digest, env = launcher
    env = {**env, "CODEX_STATEBAR_VERSION_PROBE": "1"}
    result = subprocess.run([str(entry), "_launch-codex", "--version"], env=env,
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 126
    assert "recursive" in result.stderr
    assert not cache.exists(), "recursive probe must not initialize another runtime"


def test_legacy_single_member_upgrade_installs_complete_runtime(launcher, tmp_path):
    entry, _cache, _digest, env = launcher
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
        bundle.add(entry, arcname="cxs")
    destination = tmp_path / "legacy-cxs"
    destination.write_bytes(b"old executable")
    # This extraction method is unchanged from the old single-file updater.
    updater._extract_member(archive.getvalue(), "cxs", destination)
    os.replace(destination.with_name(destination.name + ".new"), destination)
    result = subprocess.run([str(destination), "--version"], env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["runtime-ok", str(destination), "--version"]
