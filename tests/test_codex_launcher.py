import os
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
    monkeypatch.setattr(codex_launcher.os, "execv", lambda path, argv: launched.append((path, argv)))

    assert codex_launcher.launch(["--version"]) == 127
    assert capsys.readouterr().err == ""
    assert launched == [(str(official.path), [str(official.path), "--version"])]


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
        "execv",
        lambda path, argv: launched.append(
            (
                path,
                argv,
                os.environ.get("PYINSTALLER_RESET_ENVIRONMENT"),
                os.environ.get("_PYI_ARCHIVE_FILE"),
                os.environ.get("_PYI_PARENT_PROCESS_LEVEL"),
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
