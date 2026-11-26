"""Self-healing when Codex's standalone installer replaces our launcher."""
import os
import subprocess

import pytest

from codex_statebar import guard


def _install(tmp_path):
    (tmp_path / 'cxs').write_text('bin')
    (tmp_path / 'codex-cxs').write_text('bin')
    (tmp_path / 'codex-code-mode-host').write_text('bin')
    guard._write_launcher(tmp_path / 'codex')


def test_restores_launcher_replaced_by_standalone_installer(tmp_path):
    _install(tmp_path)
    official = tmp_path / '.codex/packages/standalone/current/bin'
    official.mkdir(parents=True)
    (official / 'codex').write_text('official')
    (official / 'codex-code-mode-host').write_text('official')
    for name in ('codex', 'codex-code-mode-host'):
        (tmp_path / name).unlink()
        os.symlink(official / name, tmp_path / name)
    calls = []
    fixed = guard.repair(tmp_path, reinstall=lambda: calls.append(1) or (True, 'ok'))
    assert fixed == ['codex', 'native'] and calls == [1]
    codex = tmp_path / 'codex'
    assert not codex.is_symlink() and guard._is_ours(codex)
    assert os.access(codex, os.X_OK)


@pytest.mark.skipif(os.name == "nt", reason="POSIX launcher")
def test_repaired_launcher_preserves_explicit_managed_directory(tmp_path):
    _install(tmp_path)
    cxs = tmp_path / "cxs"
    cxs.write_text('#!/bin/sh\nprintf "%s\\n" "$CODEX_STATEBAR_MANAGED_DIR"\n')
    cxs.chmod(0o755)
    launcher = tmp_path / "codex"
    launcher.unlink()
    assert guard.repair(tmp_path) == ["codex"]

    override = str(tmp_path / "preview")
    result = subprocess.run(
        [str(launcher)],
        env=dict(os.environ, CODEX_STATEBAR_MANAGED_DIR=override),
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    assert result.stdout.strip() == override


def test_healthy_install_is_a_no_op(tmp_path):
    _install(tmp_path)
    assert guard.repair(tmp_path, reinstall=lambda: 1 / 0) == []


def test_leaves_user_owned_codex_alone(tmp_path):
    _install(tmp_path)
    (tmp_path / 'codex').unlink()
    other = tmp_path / 'elsewhere'
    other.write_text('mine')
    os.symlink(other, tmp_path / 'codex')
    assert guard.repair(tmp_path, reinstall=lambda: 1 / 0) == []
    assert (tmp_path / 'codex').is_symlink()


def test_plist_watches_install_dir():
    body = guard.build_plist('/u/.local/bin/cxs', '/u/.local/bin')
    assert '<string>_guard</string>' in body
    assert '<key>WatchPaths</key>' in body and '/u/.local/bin</string>' in body
