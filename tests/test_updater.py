import io
import tarfile

from codex_statebar import updater


def test_version_tuple_handles_v_prefix_and_suffix():
    assert updater._version_tuple("v1.2.3") == (1, 2, 3)
    assert updater._version_tuple("1.2.3-beta1") == (1, 2, 3)


def test_asset_urls_select_target_and_checksum():
    release = {"assets": [
        {"name": "cxs-macos-arm64.tar.gz", "browser_download_url": "a"},
        {"name": "cxs-macos-arm64.tar.gz.sha256", "browser_download_url": "b"},
    ]}
    assert updater._asset_urls(release, "macos-arm64") == ("a", "b")


def test_upgrade_rejects_bad_checksum(tmp_path, monkeypatch):
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as bundle:
        data = b"binary"
        info = tarfile.TarInfo("cxs")
        info.size = len(data)
        bundle.addfile(info, io.BytesIO(data))
    archive = payload.getvalue()
    release = {
        "tag_name": "v99.0.0",
        "assets": [
            {"name": "cxs-test.tar.gz", "browser_download_url": "archive"},
            {"name": "cxs-test.tar.gz.sha256", "browser_download_url": "checksum"},
        ],
    }
    monkeypatch.setattr(updater, "_target", lambda: "test")
    monkeypatch.setattr(updater, "_destination", lambda: tmp_path / "cxs")
    monkeypatch.setattr(
        updater, "_fetch",
        lambda url: (json_bytes(release) if url == updater.LATEST_API
                     else archive if url == "archive" else b"0" * 64),
    )
    ok, message = updater.upgrade_current_install()
    assert ok is False
    assert "SHA-256" in message


def json_bytes(value):
    import json
    return json.dumps(value).encode()
