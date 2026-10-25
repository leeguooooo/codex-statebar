import os
from pathlib import Path

from codex_statebar import codex_launcher, doctor, rollout, setup


def test_doctor_reports_codex_surfaces(monkeypatch, capsys):
    monkeypatch.setattr(setup, "is_statusline_configured", lambda: True)
    monkeypatch.setattr(doctor, "_binary_contains", lambda *_args: True)
    patched = codex_launcher.CodexBinary(
        Path("/tmp/codex-cxs"), "codex-cli 0.145.0", (0, 145, 0, 1), True
    )
    monkeypatch.setattr(
        codex_launcher,
        "inspect_install",
        lambda: codex_launcher.LaunchDecision(
            patched, patched, None, "patched Codex is current"
        ),
    )
    monkeypatch.setattr(rollout, "collect_status", lambda: {
        "rollout_path": "/tmp/rollout.jsonl", "model_id": "gpt-test",
        "context_used_pct": 25, "rate_limit_pct": 10,
        "rate_limit_7d_pct": 20,
    })
    assert doctor.run() == 0
    output = capsys.readouterr().out
    assert "native [tui] fallback" in output
    assert "rich command hook" in output
    assert "patched Codex external command enabled" in output
    assert "Codex version routing" in output
    assert "gpt-test" in output


def test_binary_contains_finds_marker_across_chunk_boundary(tmp_path):
    binary = tmp_path / "codex"
    binary.write_bytes(b"x" * (1024 * 1024 - 3) + b"CODEX_STATUS_LINE")
    assert doctor._binary_contains(str(binary), b"CODEX_STATUS_LINE")


def test_binary_contains_returns_false_for_missing_marker(tmp_path):
    binary = tmp_path / "codex"
    binary.write_bytes(b"stable-codex")
    assert not doctor._binary_contains(str(binary), b"CODEX_STATUS_LINE")


def _tree_snapshot(root: Path):
    entries = [(".", *_stat_key(root))]
    for dirpath, dirnames, filenames in os.walk(root):
        for name in sorted(dirnames + filenames):
            path = Path(dirpath, name)
            entries.append((str(path.relative_to(root)), *_stat_key(path)))
    return entries


def _stat_key(path: Path):
    st = os.lstat(path)
    return st.st_ino, st.st_mtime_ns, st.st_mode, st.st_size


def test_doctor_does_not_run_managed_wrapper_or_publish_links(tmp_path, monkeypatch, capsys):
    # The PATH codex is the managed wrapper. Running it would call launch and
    # publish the exec link, so doctor must neither run it nor change the directory.
    managed = tmp_path / "managed"
    managed.mkdir()
    wrapper = managed / "codex"
    wrapper.write_text(
        "#!/bin/sh\n# managed by codex-statebar\n: > \"$(dirname \"$0\")/ran\"\nexit 0\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    patched = managed / "codex-cxs"
    patched.write_text("#!/bin/sh\necho 'codex-cli 0.145.0'\n", encoding="utf-8")
    patched.chmod(0o755)
    home = tmp_path / "home"  # conftest 已创建并设为 HOME
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("CODEX_STATEBAR_MANAGED_DIR", str(managed))
    monkeypatch.setenv("PATH", os.pathsep.join((str(managed), "/usr/bin", "/bin")))
    monkeypatch.setattr(setup, "is_statusline_configured", lambda: True)
    monkeypatch.setattr(rollout, "collect_status", lambda: {})

    before = _tree_snapshot(managed)
    doctor.run()
    output = capsys.readouterr().out

    assert _tree_snapshot(managed) == before
    assert not (managed / ".cxs-exec").exists()
    assert not (managed / "ran").exists()
    assert "PATH routing wrapper not executed" in output
    assert "计划路径" in output and "失败时回退" in output


def test_doctor_does_not_run_cmux_shim_that_routes_to_managed_wrapper(tmp_path, monkeypatch, capsys):
    # cmux shims forward to the managed wrapper, which would publish links and write
    # files. Doctor must leave both directories exactly as they were.
    cmux = tmp_path / "cmux-cli-shims"
    cmux.mkdir()
    shim = cmux / "codex"
    shim.write_text(
        "#!/bin/sh\n# CMUX_CODEX_WRAPPER_SHIM\n: > \"$(dirname \"$0\")/ran\"\n"
        f"exec '{tmp_path}/managed/codex' \"$@\"\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    managed = tmp_path / "managed"
    managed.mkdir()
    wrapper = managed / "codex"
    wrapper.write_text(
        "#!/bin/sh\n# managed by codex-statebar\n: > \"$(dirname \"$0\")/ran-managed\"\nexit 0\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    patched = managed / "codex-cxs"
    patched.write_text("#!/bin/sh\necho 'codex-cli 0.145.0'\n", encoding="utf-8")
    patched.chmod(0o755)
    home = tmp_path / "home"  # conftest 已创建并设为 HOME
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("CODEX_STATEBAR_MANAGED_DIR", str(managed))
    monkeypatch.setenv("PATH", os.pathsep.join((str(cmux), str(managed), "/usr/bin", "/bin")))
    monkeypatch.setattr(setup, "is_statusline_configured", lambda: True)
    monkeypatch.setattr(rollout, "collect_status", lambda: {})

    before = (_tree_snapshot(cmux), _tree_snapshot(managed))
    doctor.run()
    output = capsys.readouterr().out

    assert (_tree_snapshot(cmux), _tree_snapshot(managed)) == before
    assert not (cmux / "ran").exists() and not (managed / "ran-managed").exists()
    assert not (managed / ".cxs-exec").exists()
    assert "PATH routing wrapper not executed" in output
