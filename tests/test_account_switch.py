from codex_statebar import predict


def test_prediction_never_reads_account_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(predict, "_LATEST_PATH", tmp_path / "rate_latest.json")
    monkeypatch.setattr(predict, "_PROJECTION_PATH", tmp_path / "rate_projection.json")
    assert predict.account_id() is None
    assert predict._latest_path() == tmp_path / "rate_latest.json"
    assert predict._projection_path() == tmp_path / "rate_projection.json"
