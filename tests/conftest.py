import pytest


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """Background refresh subprocesses must never write the developer's caches."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("CODEX_STATEBAR_NO_UPDATE", "1")


@pytest.fixture(autouse=True)
def _isolate_rate_latest(tmp_path, monkeypatch):
    """Keep every test off the real ~/.cache/codex-statebar/rate_latest.json.

    predict.reconcile_account (reached via forecast() and core.main's render
    path) reads+writes that shared account-global store. Without isolation tests
    would pollute the developer's real cache and leak state into each other.
    Each test gets its own throwaway path."""
    try:
        import codex_statebar.predict as predict
        monkeypatch.setattr(predict, "_LATEST_PATH", tmp_path / "rate_latest.json")
        monkeypatch.setattr(predict, "_PROJECTION_PATH", tmp_path / "rate_projection.json")
        # Keep tests on the exact isolated paths above.
        monkeypatch.setattr(predict, "account_id", lambda: None)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _isolate_maintenance(tmp_path, monkeypatch):
    from codex_statebar import maintenance
    monkeypatch.setattr(maintenance, "_default_root", lambda: tmp_path / "maintenance-cache")
    monkeypatch.setattr(maintenance, "_last_run", 0.0)
