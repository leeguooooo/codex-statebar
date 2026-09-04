from codex_statebar import setup


def test_statusline_descriptor_uses_stable_codex_items():
    descriptor = setup._statusline_config()
    assert descriptor["items"] == list(setup.NATIVE_ITEMS)
    assert descriptor["external"] is False


def test_setup_creates_tui_section_and_preserves_root_keys(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('model = "gpt-5.6"\n', encoding="utf-8")
    changed, _ = setup._configure(path)
    text = path.read_text(encoding="utf-8")
    assert changed is True
    assert 'model = "gpt-5.6"' in text
    assert "[tui]" in text
    assert "status_line_use_colors = true" in text
    assert 'status_line = ["model-with-reasoning",' in text
    assert '"weekly-limit", "fast-mode"]' in text


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


def test_setup_replaces_multiline_status_line_without_orphans(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[tui]\nstatus_line = [\n    "command",\n    "/tmp/cxs",\n    "render",\n]\n'
        'status_line_use_colors = true\nterminal_title = []\n',
        encoding="utf-8",
    )
    setup._configure(path)
    text = path.read_text(encoding="utf-8")
    assert '"command"' not in text
    assert '"/tmp/cxs"' not in text
    assert '"render"' not in text
    assert "terminal_title = []" in text


def test_setup_is_idempotent(tmp_path):
    path = tmp_path / "config.toml"
    first, _ = setup._configure(path)
    second, message = setup._configure(path)
    assert first is True
    assert second is False
    assert "already configured" in message


def test_project_path_uses_dot_codex(tmp_path):
    assert setup.project_settings_path(tmp_path) == tmp_path / ".codex/config.toml"
