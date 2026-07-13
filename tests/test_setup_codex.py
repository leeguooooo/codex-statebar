from codex_statebar import setup


def test_setup_creates_tui_section_and_preserves_root_keys(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('model = "gpt-5.6"\n', encoding="utf-8")
    changed, _ = setup._configure(path)
    text = path.read_text(encoding="utf-8")
    assert changed is True
    assert 'model = "gpt-5.6"' in text
    assert "[tui]" in text
    assert '"weekly-limit"' in text
    assert "status_line_use_colors = true" in text


def test_setup_updates_only_managed_tui_keys(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[tui]\nanimations = false\nstatus_line = ["model"]\n\n[features]\nhooks = true\n',
        encoding="utf-8",
    )
    setup._configure(path)
    text = path.read_text(encoding="utf-8")
    assert "animations = false" in text
    assert "[features]" in text and "hooks = true" in text
    assert text.count("status_line =") == 1


def test_setup_is_idempotent(tmp_path):
    path = tmp_path / "config.toml"
    first, _ = setup._configure(path)
    second, message = setup._configure(path)
    assert first is True
    assert second is False
    assert "already configured" in message


def test_project_path_uses_dot_codex(tmp_path):
    assert setup.project_settings_path(tmp_path) == tmp_path / ".codex/config.toml"
