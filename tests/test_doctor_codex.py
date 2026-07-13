from codex_statebar import doctor, rollout, setup


def test_doctor_reports_codex_surfaces(monkeypatch, capsys):
    monkeypatch.setattr(setup, "is_statusline_configured", lambda: True)
    monkeypatch.setattr(rollout, "collect_status", lambda: {
        "rollout_path": "/tmp/rollout.jsonl", "model_id": "gpt-test",
        "context_used_pct": 25, "rate_limit_pct": 10,
        "rate_limit_7d_pct": 20,
    })
    assert doctor.run() == 0
    output = capsys.readouterr().out
    assert "native [tui] fallback" in output
    assert "rich command hook" in output
    assert "gpt-test" in output
