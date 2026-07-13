import json
from pathlib import Path

from codex_statebar import rollout


def _write_rollout(path: Path, cwd: str) -> None:
    events = [
        {"type": "session_meta", "payload": {
            "id": "thread-1", "cwd": cwd, "cli_version": "0.144.1",
        }},
        {"type": "turn_context", "payload": {
            "model": "gpt-5.6", "effort": "high",
            "approval_policy": "never", "sandbox_policy": {"type": "danger-full-access"},
        }},
        {"type": "event_msg", "payload": {
            "type": "token_count",
            "info": {
                "model_context_window": 200000,
                "last_token_usage": {
                    "input_tokens": 49000, "output_tokens": 1000,
                    "total_tokens": 50000,
                },
            },
            "rate_limits": {
                "plan_type": "pro",
                "primary": {"used_percent": 12, "window_minutes": 300,
                            "resets_at": 2000000000},
                "secondary": {"used_percent": 34, "window_minutes": 10080,
                              "resets_at": 2000100000},
            },
        }},
    ]
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(json.dumps(item) for item in events) + "\n",
                    encoding="utf-8")


def test_collects_codex_context_limits_and_model(tmp_path, monkeypatch):
    home = tmp_path / ".codex"
    path = home / "sessions/2026/07/13/rollout-thread-1.jsonl"
    _write_rollout(path, str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(home))
    status = rollout.collect_status({"thread_id": "thread-1"})
    assert status["model_id"] == "gpt-5.6"
    assert status["effort_level"] == "high"
    assert status["context_used_pct"] == 25.0
    assert status["rate_limit_pct"] == 12.0
    assert status["rate_limit_7d_pct"] == 34.0
    assert status["plan_type"] == "pro"


def test_finds_newest_rollout_for_cwd(tmp_path, monkeypatch):
    home = tmp_path / ".codex"
    path = home / "sessions/2026/07/13/rollout-thread-1.jsonl"
    _write_rollout(path, str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(home))
    assert rollout.find_rollout(cwd=str(tmp_path)) == path


def test_external_payload_works_without_local_rollout(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "empty"))
    status = rollout.collect_status({
        "thread_id": "t", "model": "gpt-5.6", "reasoning_effort": "medium",
        "context": {"used_percent": 20, "window_tokens": 100000},
        "limits": {"weekly": {"used_percent": 9, "resets_at": 2000000000}},
    })
    assert status["model_id"] == "gpt-5.6"
    assert status["context_remaining_pct"] == 80.0
    assert status["rate_limit_7d_pct"] == 9
