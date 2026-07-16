from codex_statebar import doctor, rollout, setup


def test_doctor_reports_codex_surfaces(monkeypatch, capsys):
    monkeypatch.setattr(setup, "is_statusline_configured", lambda: True)
    monkeypatch.setattr(doctor, "_binary_contains", lambda *_args: True)
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
    assert "gpt-test" in output


def test_binary_contains_finds_marker_across_chunk_boundary(tmp_path):
    binary = tmp_path / "codex"
    binary.write_bytes(b"x" * (1024 * 1024 - 3) + b"CODEX_STATUS_LINE")
    assert doctor._binary_contains(str(binary), b"CODEX_STATUS_LINE")


def test_binary_contains_returns_false_for_missing_marker(tmp_path):
    binary = tmp_path / "codex"
    binary.write_bytes(b"stable-codex")
    assert not doctor._binary_contains(str(binary), b"CODEX_STATUS_LINE")
