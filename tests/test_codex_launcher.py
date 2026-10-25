import os
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
                  / '0.164.0-x' / 'bin' / 'codex-code-mode-host')
    link = managed / 'codex-code-mode-host'
    link.symlink_to(tmp_path / 'gone')
    patched = codex_launcher.CodexBinary(managed / 'codex-cxs', 'codex-cli 0.164.0', (0, 164, 0, 1), True)

    codex_launcher.ensure_code_mode_host(patched, None)
    assert link.resolve() == fresh.resolve()

    link.unlink()
    _host(link)
    codex_launcher.ensure_code_mode_host(patched, None)
    assert not link.is_symlink()
