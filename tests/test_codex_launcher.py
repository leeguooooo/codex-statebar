import os
import shutil
from types import SimpleNamespace
from pathlib import Path

import pytest

from codex_statebar import codex_launcher


pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="The version-aware Codex launcher is installed on macOS/Linux only",
)


def _binary(path: Path, version: str) -> Path:
    path.write_text(f"#!/bin/sh\necho 'codex-cli {version}'\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_newer_official_codex_wins_over_patched_build(tmp_path):
    managed = tmp_path / "managed"
    official_dir = tmp_path / "official"
    managed.mkdir()
    official_dir.mkdir()
    _binary(managed / "codex-cxs", "0.144.1")
    _binary(official_dir / "codex", "0.145.0")
    _binary(managed / "codex", "9.9.9")

    decision = codex_launcher.inspect_install(
        managed_dir=managed,
        path_value=os.pathsep.join((str(managed), str(official_dir))),
    )

    assert decision.selected is decision.official
    assert decision.selected.path == official_dir / "codex"
    assert "newer" in decision.reason


def test_current_patched_codex_wins_over_older_official_build(tmp_path):
    managed = tmp_path / "managed"
    official_dir = tmp_path / "official"
    managed.mkdir()
    official_dir.mkdir()
    _binary(managed / "codex-cxs", "0.145.0")
    _binary(official_dir / "codex", "0.144.4")

    decision = codex_launcher.inspect_install(
        managed_dir=managed,
        path_value=str(official_dir),
    )

    assert decision.selected is decision.patched
    assert decision.selected.path == managed / "codex-cxs"


def test_launcher_falls_back_to_official_when_patched_binary_is_missing(tmp_path):
    managed = tmp_path / "managed"
    official_dir = tmp_path / "official"
    managed.mkdir()
    official_dir.mkdir()
    _binary(official_dir / "codex", "0.145.0")

    decision = codex_launcher.inspect_install(
        managed_dir=managed,
        path_value=str(official_dir),
    )

    assert decision.selected is decision.official
    assert "not installed" in decision.reason


def test_stable_release_sorts_after_same_version_prerelease():
    assert codex_launcher._version_key("codex-cli 0.146.0") > (
        codex_launcher._version_key("codex-cli 0.146.0-alpha.3")
    )


def test_cached_runtime_finds_patched_codex_beside_entrypoint(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_STATEBAR_MANAGED_DIR", raising=False)
    monkeypatch.setattr(codex_launcher.sys, "frozen", True, raising=False)
    monkeypatch.setenv("CODEX_STATEBAR_ENTRYPOINT", str(tmp_path / "bin/cxs"))
    monkeypatch.setattr(codex_launcher.sys, "executable", str(tmp_path / "cache/hash/cxs"))
    assert codex_launcher._managed_dir() == tmp_path / "bin"


def test_chatgpt_app_embedded_codex_is_not_selected(tmp_path):
    managed = tmp_path / "managed"
    official_dir = tmp_path / "official"
    app_dir = tmp_path / "ChatGPT.app" / "Contents" / "Resources"
    managed.mkdir()
    official_dir.mkdir()
    app_dir.mkdir(parents=True)
    _binary(managed / "codex-cxs", "0.144.1")
    _binary(official_dir / "codex", "0.144.4")
    _binary(app_dir / "codex", "0.146.0-alpha.3")

    decision = codex_launcher.inspect_install(
        managed_dir=managed,
        path_value=os.pathsep.join((str(app_dir), str(official_dir))),
    )

    assert decision.official.path == official_dir / "codex"


def test_launcher_stays_quiet_when_routing_to_newer_official(
    tmp_path, monkeypatch, capsys
):
    patched = codex_launcher.CodexBinary(
        tmp_path / "codex-cxs", "codex-cli 0.145.0", (0, 145, 0, 1), True
    )
    official = codex_launcher.CodexBinary(
        tmp_path / "codex", "codex-cli 0.153.2", (0, 153, 2, 1), False
    )
    decision = codex_launcher.LaunchDecision(
        official, patched, official, "official Codex is newer than the patched build"
    )
    launched = []
    monkeypatch.setattr(codex_launcher, "inspect_install", lambda: decision)
    monkeypatch.setattr(
        codex_launcher.os,
        "execve",
        lambda path, argv, env: launched.append((path, argv, env)),
    )

    assert codex_launcher.launch(["--version"]) == 127
    assert capsys.readouterr().err == ""
    assert len(launched) == 1
    assert launched[0][:2] == (
        str(official.path),
        [str(official.path), "--version"],
    )


def test_frozen_launcher_resets_pyinstaller_environment_for_nested_cxs(
    tmp_path, monkeypatch
):
    official = codex_launcher.CodexBinary(
        tmp_path / "codex", "codex-cli 0.153.2", (0, 153, 2, 1), False
    )
    decision = codex_launcher.LaunchDecision(
        official, None, official, "patched Codex is not installed"
    )
    launched = []
    monkeypatch.setattr(codex_launcher, "inspect_install", lambda: decision)
    monkeypatch.setattr(codex_launcher.sys, "frozen", True, raising=False)
    monkeypatch.delenv("PYINSTALLER_RESET_ENVIRONMENT", raising=False)
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", "/tmp/cxs")
    monkeypatch.setenv("_PYI_PARENT_PROCESS_LEVEL", "1")
    monkeypatch.setattr(
        codex_launcher.os,
        "execve",
        lambda path, argv, env: launched.append(
            (
                path,
                argv,
                env.get("PYINSTALLER_RESET_ENVIRONMENT"),
                env.get("_PYI_ARCHIVE_FILE"),
                env.get("_PYI_PARENT_PROCESS_LEVEL"),
            )
        ),
    )

    assert codex_launcher.launch(["mcp", "list"]) == 127
    assert launched == [
        (
            str(official.path),
            [str(official.path), "mcp", "list"],
            "1",
            None,
            None,
        )
    ]


def test_probe_uses_independent_environment_and_cold_start_budget(
    tmp_path, monkeypatch
):
    binary = tmp_path / "codex"
    binary.touch()
    calls = []
    monkeypatch.setattr(codex_launcher.sys, "frozen", True, raising=False)
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", "/tmp/cxs")
    monkeypatch.setenv("_PYI_PARENT_PROCESS_LEVEL", "1")

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout="codex-cli 0.153.2\n",
            stderr="",
        )

    monkeypatch.setattr(codex_launcher.subprocess, "run", fake_run)

    probed = codex_launcher._probe(binary)

    assert probed is not None
    assert calls[0][1]["timeout"] == 5
    environment = calls[0][1]["env"]
    assert environment["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert environment["CODEX_STATEBAR_VERSION_PROBE"] == "1"
    assert "_PYI_ARCHIVE_FILE" not in environment
    assert "_PYI_PARENT_PROCESS_LEVEL" not in environment


@pytest.mark.parametrize("layout", ["cmux-cli-shims/surface", "custom-wrapper"])
def test_cmux_routing_wrapper_is_not_probed(tmp_path, monkeypatch, layout):
    managed = tmp_path / "managed"
    official = tmp_path / "official"
    shim_dir = tmp_path / layout
    for directory in (managed, official, shim_dir):
        directory.mkdir(parents=True)
    real = _binary(official / "codex", "0.153.2")
    shim = shim_dir / "codex"
    shim.write_text('#!/bin/sh\n# CMUX_CODEX_WRAPPER_SHIM\nexit 99\n')
    shim.chmod(0o755)
    calls = []

    def probe(path):
        calls.append(path)
        assert path != shim, "routing wrapper must never be executed"
        return codex_launcher.CodexBinary(path, "codex-cli 0.153.2", (0, 153, 2, 1), False)

    monkeypatch.setattr(codex_launcher, "_probe", probe)
    decision = codex_launcher.inspect_install(
        managed_dir=managed,
        path_value=os.pathsep.join(map(str, (shim_dir, managed, official))),
    )
    assert calls == [real]
    assert decision.selected.path == real


def test_reentrant_probe_stops_before_discovery_or_spawn(monkeypatch, capsys):
    monkeypatch.setenv("CODEX_STATEBAR_VERSION_PROBE", "1")

    def forbidden(*args, **kwargs):
        pytest.fail("recursive version probe attempted discovery or execution")

    monkeypatch.setattr(codex_launcher, "inspect_install", forbidden)
    monkeypatch.setattr(codex_launcher.subprocess, "run", forbidden)
    monkeypatch.setattr(codex_launcher.os, "execve", forbidden)
    assert codex_launcher.launch(["--version"]) == 126
    assert codex_launcher._probe(Path("/unused/codex")) is None
    assert "recursive" in capsys.readouterr().err


def test_unrecognized_wrapper_receives_probe_guard(tmp_path, monkeypatch):
    import shlex
    import sys

    monkeypatch.delenv("CODEX_STATEBAR_VERSION_PROBE", raising=False)
    wrapper = tmp_path / "codex"
    # Safety sentinel: even if the guard regresses, the fixture cannot recurse.
    # It exits 99 before invoking the launcher when the marker is missing.
    code = (
        "import os,sys; from codex_statebar import codex_launcher as c; "
        "sys.exit(99) if not os.environ.get('CODEX_STATEBAR_VERSION_PROBE') else None; "
        "c.inspect_install=lambda:sys.exit(98); "
        "sys.exit(c.launch(['--version']))"
    )
    wrapper.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -c {shlex.quote(code)}\n")
    wrapper.chmod(0o755)
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1] / "src"))
    original = codex_launcher.subprocess.run
    outputs = []

    def observed(*args, **kwargs):
        result = original(*args, **kwargs)
        outputs.append(result)
        return result

    monkeypatch.setattr(codex_launcher.subprocess, "run", observed)
    assert codex_launcher._probe(wrapper) is None
    assert len(outputs) == 1
    assert outputs[0].returncode == 126
    assert "recursive" in outputs[0].stderr


def test_probe_ignores_successful_empty_output(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_launcher.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout="", stderr=""))
    assert codex_launcher._probe(tmp_path / "codex") is None


def test_patched_launcher_enables_renderer_without_changing_native_config(tmp_path, monkeypatch):
    import json
    patched = codex_launcher.CodexBinary(tmp_path / 'codex-cxs', 'codex-cli 0.164.0', (0, 164, 0, 1), True)
    monkeypatch.setattr(codex_launcher, 'inspect_install', lambda: codex_launcher.LaunchDecision(patched, patched, None, 'patched current'))
    monkeypatch.setenv('CODEX_STATEBAR_ENTRYPOINT', '/installed/cxs')
    calls = []
    monkeypatch.setattr(codex_launcher.os, 'execve', lambda p, a, e: calls.append(e))
    codex_launcher.launch([])
    assert json.loads(calls[0]['CODEX_STATEBAR_STATUS_COMMAND']) == ['/installed/cxs', 'render']


def test_official_fallback_does_not_inherit_external_command(tmp_path, monkeypatch):
    official = codex_launcher.CodexBinary(tmp_path / 'codex', 'codex-cli 0.165.0', (0, 165, 0, 1), False)
    monkeypatch.setattr(codex_launcher, 'inspect_install', lambda: codex_launcher.LaunchDecision(official, None, official, 'official newer'))
    monkeypatch.setenv('CODEX_STATEBAR_STATUS_COMMAND', '["old", "render"]')
    calls = []
    monkeypatch.setattr(codex_launcher.os, 'execve', lambda p, a, e: calls.append(e))
    codex_launcher.launch([])
    assert 'CODEX_STATEBAR_STATUS_COMMAND' not in calls[0]


def test_frozen_helper_uses_installed_entrypoint(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_launcher.sys, 'frozen', True, raising=False)
    monkeypatch.setenv('CODEX_STATEBAR_ENTRYPOINT', '/installed/cxs')
    assert codex_launcher.module_command('codex_statebar._git_refresh', '/repo') == [
        '/installed/cxs', '-m', 'codex_statebar._git_refresh', '/repo']


@pytest.fixture(autouse=True)
def _apple_silicon(monkeypatch):
    monkeypatch.setattr(codex_launcher, '_host_platform', lambda: ('aarch64', 'apple-darwin'))


def _host(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_patched_launch_links_code_mode_host_from_npm_official(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex-home'))
    managed = tmp_path / 'managed'
    managed.mkdir()
    package = tmp_path / 'lib' / 'node_modules' / '@openai' / 'codex'
    (package / 'bin').mkdir(parents=True)
    _binary(package / 'bin' / 'codex.js', '0.164.0')
    host = _host(package / 'node_modules' / '@openai' / 'codex-darwin-arm64'
                 / 'vendor' / 'aarch64-apple-darwin' / 'bin' / 'codex-code-mode-host')
    patched = codex_launcher.CodexBinary(managed / 'codex-cxs', 'codex-cli 0.164.0', (0, 164, 0, 1), True)
    official = codex_launcher.CodexBinary(package / 'bin' / 'codex.js', 'codex-cli 0.164.0', (0, 164, 0, 1), False)
    monkeypatch.setattr(codex_launcher, 'inspect_install', lambda: codex_launcher.LaunchDecision(patched, patched, official, 'patched current'))
    monkeypatch.setattr(codex_launcher.os, 'execve', lambda p, a, e: None)

    codex_launcher.launch([])

    link = managed / 'codex-code-mode-host'
    assert link.is_symlink() and link.resolve() == host.resolve()


def test_code_mode_host_prefers_version_matched_codex_package(tmp_path, monkeypatch):
    home = tmp_path / 'codex-home'
    monkeypatch.setenv('CODEX_HOME', str(home))
    managed = tmp_path / 'managed'
    managed.mkdir()
    releases = home / 'packages' / 'app-server-daemon' / 'releases'
    _host(releases / '0.163.0-aarch64-apple-darwin' / 'bin' / 'codex-code-mode-host')
    matched = _host(releases / '0.164.0-aarch64-apple-darwin' / 'bin' / 'codex-code-mode-host')
    patched = codex_launcher.CodexBinary(managed / 'codex-cxs', 'codex-cli 0.164.0', (0, 164, 0, 1), True)

    assert codex_launcher.ensure_code_mode_host(patched, None) == managed / 'codex-code-mode-host'
    assert (managed / 'codex-code-mode-host').resolve() == matched.resolve()


def test_code_mode_host_replaces_dangling_link_and_keeps_real_file(tmp_path, monkeypatch):
    home = tmp_path / 'codex-home'
    monkeypatch.setenv('CODEX_HOME', str(home))
    managed = tmp_path / 'managed'
    managed.mkdir()
    fresh = _host(home / 'packages' / 'app-server-daemon' / 'releases'
                  / '0.164.0-aarch64-apple-darwin' / 'bin' / 'codex-code-mode-host')
    link = managed / 'codex-code-mode-host'
    link.symlink_to(tmp_path / 'gone')
    patched = codex_launcher.CodexBinary(managed / 'codex-cxs', 'codex-cli 0.164.0', (0, 164, 0, 1), True)

    codex_launcher.ensure_code_mode_host(patched, None)
    assert link.resolve() == fresh.resolve()

    link.unlink()
    _host(link)
    codex_launcher.ensure_code_mode_host(patched, None)
    assert not link.is_symlink()


def test_code_mode_host_rejects_other_version_or_architecture(tmp_path, monkeypatch):
    home = tmp_path / 'codex-home'
    monkeypatch.setenv('CODEX_HOME', str(home))
    managed = tmp_path / 'managed'
    managed.mkdir()
    releases = home / 'packages' / 'app-server-daemon' / 'releases'
    _host(releases / '0.164.0-x86_64-apple-darwin' / 'bin' / 'codex-code-mode-host')
    official_dir = tmp_path / 'official'
    official_dir.mkdir()
    _binary(official_dir / 'codex', '0.165.0')
    _host(official_dir / 'codex-code-mode-host')
    patched = codex_launcher.CodexBinary(managed / 'codex-cxs', 'codex-cli 0.164.0', (0, 164, 0, 1), True)
    official = codex_launcher.CodexBinary(official_dir / 'codex', 'codex-cli 0.165.0', (0, 165, 0, 1), False)
    stale = managed / 'codex-code-mode-host'
    stale.symlink_to(official_dir / 'codex-code-mode-host')

    assert codex_launcher.ensure_code_mode_host(patched, official) is None
    assert not stale.exists() and not stale.is_symlink()


def _launch_recording(monkeypatch, decision):
    launched = []
    monkeypatch.setattr(codex_launcher, "inspect_install", lambda: decision)
    monkeypatch.setattr(
        codex_launcher.os,
        "execve",
        lambda path, argv, env: launched.append((path, argv, env)),
    )
    return launched


def _patched_decision(path: Path, version: str = "0.145.0"):
    patched = codex_launcher.CodexBinary(
        path, f"codex-cli {version}", (0, 145, 0, 1), True
    )
    return codex_launcher.LaunchDecision(patched, patched, None, "patched Codex is current")


def _alias_files(managed: Path):
    return sorted(p for p in (managed / ".cxs-exec").rglob("*") if p.is_file())


def test_patched_launch_execs_hardlink_named_codex(tmp_path, monkeypatch):
    managed = tmp_path / "managed"
    managed.mkdir()
    patched = _binary(managed / "codex-cxs", "0.145.0")
    decision = _patched_decision(patched)
    launched = _launch_recording(monkeypatch, decision)

    assert codex_launcher.launch(["--version"]) == 127
    alias = codex_launcher.exec_alias_path(decision.selected)
    assert alias.name == "codex"
    assert alias.parent.parent == managed / ".cxs-exec"
    assert launched == [(str(alias), [str(alias), "--version"], launched[0][2])]
    assert os.path.samefile(alias, patched)


def test_alias_is_rebuilt_after_update_replaces_patched_binary(tmp_path, monkeypatch):
    managed = tmp_path / "managed"
    managed.mkdir()
    patched = _binary(managed / "codex-cxs", "0.145.0")
    old = _patched_decision(patched)
    _launch_recording(monkeypatch, old)
    codex_launcher.launch([])
    old_alias = codex_launcher.exec_alias_path(old.selected)

    updated = tmp_path / "updated"
    _binary(updated, "0.146.0")
    os.replace(updated, patched)
    new = _patched_decision(patched, "0.146.0")
    launched = _launch_recording(monkeypatch, new)
    codex_launcher.launch([])

    new_alias = codex_launcher.exec_alias_path(new.selected)
    assert new_alias != old_alias
    assert launched[0][0] == str(new_alias)
    assert os.path.samefile(new_alias, patched)
    assert old_alias.parent.exists(), "the previous version stays for sessions started before the upgrade"
    assert not list((managed / ".cxs-exec").rglob(".codex.*.tmp"))

    newer = tmp_path / "newer"
    _binary(newer, "0.147.0")
    os.replace(newer, patched)
    _launch_recording(monkeypatch, _patched_decision(patched, "0.147.0"))
    codex_launcher.launch([])
    assert not old_alias.parent.exists(), "versions older than the previous one are pruned"
    assert new_alias.parent.exists()


def test_alias_failure_falls_back_to_patched_path(tmp_path, monkeypatch):
    managed = tmp_path / "managed"
    managed.mkdir()
    patched = _binary(managed / "codex-cxs", "0.145.0")
    launched = _launch_recording(monkeypatch, _patched_decision(patched))

    def refuse_link(*_args, **_kwargs):
        raise OSError("cross-device link")

    monkeypatch.setattr(codex_launcher.os, "link", refuse_link)

    assert codex_launcher.launch(["--version"]) == 127
    assert launched == [(str(patched), [str(patched), "--version"], launched[0][2])]
    assert not _alias_files(managed)


def test_exec_failure_after_publish_retries_selected_path_once(tmp_path, monkeypatch, capsys):
    managed = tmp_path / "managed"
    managed.mkdir()
    patched = _binary(managed / "codex-cxs", "0.145.0")
    decision = _patched_decision(patched)
    monkeypatch.setattr(codex_launcher, "inspect_install", lambda: decision)
    calls = []

    def execve(path, argv, env):
        calls.append((path, argv, env))
        if path != str(patched):
            raise OSError("Exec format error")

    monkeypatch.setattr(codex_launcher.os, "execve", execve)

    assert codex_launcher.launch(["--version"]) == 127
    alias = codex_launcher.exec_alias_path(decision.selected)
    assert alias.is_file(), "the link was published before exec failed"
    assert [c[0] for c in calls] == [str(alias), str(patched)]
    assert calls[0][1] == [str(alias), "--version"]
    assert calls[1][1] == [str(patched), "--version"]
    assert calls[0][2] == calls[1][2]
    assert capsys.readouterr().err == ""


def test_both_exec_failures_report_once_without_recursive_retry(tmp_path, monkeypatch, capsys):
    managed = tmp_path / "managed"
    managed.mkdir()
    patched = _binary(managed / "codex-cxs", "0.145.0")
    monkeypatch.setattr(codex_launcher, "inspect_install", lambda: _patched_decision(patched))
    calls = []

    def execve(path, argv, env):
        calls.append(path)
        raise OSError("Exec format error")

    monkeypatch.setattr(codex_launcher.os, "execve", execve)

    assert codex_launcher.launch([]) == 127
    assert calls == [str(codex_launcher.exec_alias_path(_patched_decision(patched).selected)), str(patched)]
    assert "could not start" in capsys.readouterr().err


def test_stale_launcher_cannot_overwrite_newer_alias(tmp_path, monkeypatch):
    # A selects v1 and publishes its link, then pauses before exec.
    # The updater replaces the binary; B publishes v2 and execs it.
    # A resumes: its own version directory is kept as the previous version, so it
    # runs the v1 binary it selected (never a v1/v2 mix); a pruned path falls back.
    managed = tmp_path / "managed"
    managed.mkdir()
    patched = _binary(managed / "codex-cxs", "0.145.0")
    decision_a = _patched_decision(patched)
    candidates_a = codex_launcher._candidate_paths(decision_a.selected)
    alias_a = candidates_a[0]
    assert alias_a == codex_launcher.exec_alias_path(decision_a.selected)

    updated = tmp_path / "updated"
    _binary(updated, "0.146.0")
    os.replace(updated, patched)

    decision_b = _patched_decision(patched, "0.146.0")
    launched_b = _launch_recording(monkeypatch, decision_b)
    codex_launcher.launch([])
    alias_b = codex_launcher.exec_alias_path(decision_b.selected)
    assert launched_b[0][0] == str(alias_b)
    assert os.path.samefile(alias_b, patched)
    assert "0.146.0" in alias_b.read_text(encoding="utf-8")
    assert "0.145.0" in alias_a.read_text(encoding="utf-8"), "A's pinned link still points at v1"

    calls_a = []

    def execve_a(path, argv, env):
        calls_a.append(path)
        if path == str(alias_a) and not alias_a.exists():
            raise FileNotFoundError(path)

    monkeypatch.setattr(codex_launcher.os, "execve", execve_a)
    assert codex_launcher._exec_chain(candidates_a, []) == 127
    assert calls_a == [str(alias_a)], "A execs its own v1 link"

    shutil.rmtree(alias_a.parent)
    calls_a.clear()
    assert codex_launcher._exec_chain(candidates_a, []) == 127
    assert calls_a == [str(alias_a), str(patched)], "a pruned link falls back to the source path"


def test_official_launch_does_not_create_alias(tmp_path, monkeypatch):
    managed = tmp_path / "managed"
    official_dir = tmp_path / "official"
    managed.mkdir()
    official_dir.mkdir()
    official_path = _binary(official_dir / "codex", "0.153.2")
    official = codex_launcher.CodexBinary(
        official_path, "codex-cli 0.153.2", (0, 153, 2, 1), False
    )
    decision = codex_launcher.LaunchDecision(official, None, official, "patched Codex is not installed")
    launched = _launch_recording(monkeypatch, decision)

    assert codex_launcher.launch([]) == 127
    assert launched == [(str(official_path), [str(official_path)], launched[0][2])]
    assert not (managed / ".cxs-exec").exists()


def test_symlinked_alias_root_is_refused_and_external_data_kept(tmp_path, monkeypatch):
    managed = tmp_path / "managed"
    managed.mkdir()
    patched = _binary(managed / "codex-cxs", "0.145.0")
    external = tmp_path / "external"
    external.mkdir()
    (external / "keep.txt").write_text("user data", encoding="utf-8")
    (managed / ".cxs-exec").symlink_to(external, target_is_directory=True)
    launched = _launch_recording(monkeypatch, _patched_decision(patched))

    assert codex_launcher.launch([]) == 127
    assert launched == [(str(patched), [str(patched)], launched[0][2])]
    assert sorted(p.name for p in external.iterdir()) == ["keep.txt"]
    assert (external / "keep.txt").read_text(encoding="utf-8") == "user data"


def test_prune_keeps_unknown_entries_and_symlinked_version_dirs(tmp_path, monkeypatch):
    managed = tmp_path / "managed"
    managed.mkdir()
    patched = _binary(managed / "codex-cxs", "0.145.0")
    root = managed / ".cxs-exec"
    root.mkdir()
    unknown = root / "notes"
    unknown.mkdir()
    (unknown / "keep.txt").write_text("unknown", encoding="utf-8")
    stale = root / "1-2-3"
    stale.mkdir()
    (stale / "codex").write_text("old", encoding="utf-8")
    previous = root / "1-2-4"
    previous.mkdir()
    (previous / "codex").write_text("previous", encoding="utf-8")
    os.utime(stale, ns=(1_000_000_000, 1_000_000_000))
    external = tmp_path / "external"
    external.mkdir()
    (external / "keep.txt").write_text("user data", encoding="utf-8")
    (root / "4-5-6").symlink_to(external, target_is_directory=True)
    _launch_recording(monkeypatch, _patched_decision(patched))

    codex_launcher.launch([])

    assert (unknown / "keep.txt").read_text(encoding="utf-8") == "unknown"
    assert not stale.exists(), "stale version directory is pruned"
    assert previous.exists(), "the newest other version is kept"
    assert (root / "4-5-6").is_symlink()
    assert (external / "keep.txt").read_text(encoding="utf-8") == "user data"


def test_alias_directory_links_the_code_mode_host_beside_the_alias(tmp_path, monkeypatch):
    # Codex looks for codex-code-mode-host beside the executable it was started as.
    managed = tmp_path / "managed"
    managed.mkdir()
    source = managed / "codex-cxs"
    source.write_bytes(b"patched")
    source.chmod(0o755)
    host = managed / codex_launcher._code_mode_host_name()
    host.write_bytes(b"host")
    host.chmod(0o755)
    patched = codex_launcher.CodexBinary(source, "codex-cli 0.164.0", (0, 164, 0, 1), True)
    monkeypatch.setattr(codex_launcher, "ensure_code_mode_host", lambda selected, official: host)
    launched = _launch_recording(monkeypatch, codex_launcher.LaunchDecision(patched, patched, None, "patched current"))

    codex_launcher.launch([])

    alias = Path(launched[0][0])
    assert alias.name == "codex"
    beside = alias.parent / codex_launcher._code_mode_host_name()
    assert not beside.is_symlink() and os.path.samefile(beside, host), "release host is pinned by a hard link"


def test_alias_is_skipped_when_the_code_mode_host_cannot_be_linked(tmp_path, monkeypatch):
    managed = tmp_path / "managed"
    managed.mkdir()
    source = managed / "codex-cxs"
    source.write_bytes(b"patched")
    source.chmod(0o755)
    host = managed / codex_launcher._code_mode_host_name()
    host.write_bytes(b"host")
    patched = codex_launcher.CodexBinary(source, "codex-cli 0.164.0", (0, 164, 0, 1), True)
    monkeypatch.setattr(codex_launcher, "ensure_code_mode_host", lambda selected, official: host)

    real_link = os.link

    def refuse_host(src, dst, *args, **kwargs):
        if Path(src).name == codex_launcher._code_mode_host_name():
            raise OSError("read-only")
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(codex_launcher.os, "link", refuse_host)
    launched = _launch_recording(monkeypatch, codex_launcher.LaunchDecision(patched, patched, None, "patched current"))

    codex_launcher.launch([])

    assert launched[0][0] == str(source), "without the host beside it, the alias would break code mode"


def _write_exec(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_upgrade_after_publish_keeps_binary_and_host_paired(tmp_path, monkeypatch):
    # A publishes binary A + host A, then the updater replaces both with B.
    # A's directory must still hold the A pair, never binary A with host B.
    managed = tmp_path / "managed"
    managed.mkdir()
    source = _write_exec(managed / "codex-cxs", "binary-A")
    host = _write_exec(managed / codex_launcher._code_mode_host_name(), "host-A")
    patched = codex_launcher.CodexBinary(source, "codex-cli 0.164.0", (0, 164, 0, 1), True)
    candidates_a = codex_launcher._candidate_paths(patched, host)
    alias_a = candidates_a[0]
    assert alias_a.name == "codex"

    for name, text in [(host.name, "host-B"), (source.name, "binary-B")]:
        staged = _write_exec(managed / (name + ".new"), text)
        os.replace(staged, managed / name)

    host_a = alias_a.parent / codex_launcher._code_mode_host_name()
    assert alias_a.read_text(encoding="utf-8") == "binary-A"
    assert host_a.read_text(encoding="utf-8") == "host-A"


def test_no_alias_while_the_updater_holds_its_lock(tmp_path, monkeypatch):
    # Mid-upgrade the host may already be new while the binary is still old;
    # a launcher must not pair them, so it falls back to the source path.
    import fcntl
    managed = tmp_path / "managed"
    managed.mkdir()
    source = _write_exec(managed / "codex-cxs", "binary-A")
    host = _write_exec(managed / codex_launcher._code_mode_host_name(), "host-B")
    patched = codex_launcher.CodexBinary(source, "codex-cli 0.164.0", (0, 164, 0, 1), True)
    with (managed / ".cxs-upgrade.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert codex_launcher._candidate_paths(patched, host) == [source]
    assert not (managed / ".cxs-exec").exists()
