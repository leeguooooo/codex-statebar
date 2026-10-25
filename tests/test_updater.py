import io
import hashlib
import json
import os
import tarfile

import codex_statebar
import pytest
from codex_statebar import updater


def test_version_tuple_handles_v_prefix_and_suffix():
    assert updater._version_tuple("v1.2.3") == (1, 2, 3)
    assert updater._version_tuple("1.2.3-beta1") == (1, 2, 3)


def test_frozen_upgrade_targets_entrypoint_not_cached_runtime(tmp_path, monkeypatch):
    entry = tmp_path / "bin/cxs"
    monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater.sys, "executable", str(tmp_path / "cache/hash/cxs"))
    monkeypatch.setenv("CODEX_STATEBAR_ENTRYPOINT", str(entry))
    assert updater._destination() == entry


def test_legacy_frozen_upgrade_keeps_executable_destination(tmp_path, monkeypatch):
    entry = tmp_path / "cxs"
    monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater.sys, "executable", str(entry))
    monkeypatch.delenv("CODEX_STATEBAR_ENTRYPOINT", raising=False)
    assert updater._destination() == entry


def test_asset_urls_select_target_and_checksum():
    release = {"assets": [
        {"name": "cxs-macos-arm64.tar.gz", "browser_download_url": "a"},
        {"name": "cxs-macos-arm64.tar.gz.sha256", "browser_download_url": "b"},
    ]}
    assert updater._asset_urls(release, "macos-arm64") == ("a", "b")


def test_ssl_context_uses_system_ca_bundle(tmp_path, monkeypatch):
    ca_file = tmp_path / "cert.pem"
    ca_file.write_text("test certificate bundle")
    calls = []
    monkeypatch.setattr(updater, "SYSTEM_CA_FILES", (ca_file,))
    monkeypatch.setattr(
        updater.ssl,
        "create_default_context",
        lambda **kwargs: calls.append(kwargs) or object(),
    )

    context = updater._ssl_context()

    assert context is not None
    assert calls == [{"cafile": str(ca_file)}]


def test_upgrade_rejects_bad_checksum(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_statebar, "__version__", "0.1.1", raising=False)
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as bundle:
        data = b"binary"
        info = tarfile.TarInfo("cxs")
        info.size = len(data)
        bundle.addfile(info, io.BytesIO(data))
    archive = payload.getvalue()
    release = {
        "tag_name": "v0.1.1",
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
    stale = tmp_path / "cxs.new"
    stale.write_bytes(b"partial")
    ok, message = updater.upgrade_current_install()
    assert ok is False
    assert "SHA-256" in message
    assert not stale.exists()


@pytest.mark.skipif(os.name == "nt", reason="Windows releases do not ship codex-cxs")
def test_upgrade_replaces_cxs_and_patched_codex_together(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_statebar, "__version__", "0.1.1", raising=False)

    def archive(member, data):
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz") as bundle:
            info = tarfile.TarInfo(member)
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
        return payload.getvalue()

    cxs_archive = archive("cxs", b"new-cxs")
    codex_archive = archive("codex-cxs", b"new-codex")
    assets = {
        "cxs-archive": cxs_archive,
        "cxs-checksum": hashlib.sha256(cxs_archive).hexdigest().encode(),
        "codex-archive": codex_archive,
        "codex-checksum": hashlib.sha256(codex_archive).hexdigest().encode(),
    }
    release = {
        "tag_name": "v0.1.1",
        "assets": [
            {"name": "cxs-test.tar.gz", "browser_download_url": "cxs-archive"},
            {
                "name": "cxs-test.tar.gz.sha256",
                "browser_download_url": "cxs-checksum",
            },
            {
                "name": "codex-cxs-test.tar.gz",
                "browser_download_url": "codex-archive",
            },
            {
                "name": "codex-cxs-test.tar.gz.sha256",
                "browser_download_url": "codex-checksum",
            },
        ],
    }
    cxs = tmp_path / "cxs"
    cxs.write_bytes(b"old-cxs")
    (tmp_path / "codex-cxs").write_bytes(b"old-codex")
    monkeypatch.setattr(updater, "_target", lambda: "test")
    monkeypatch.setattr(updater, "_destination", lambda: cxs)
    monkeypatch.setattr(
        updater,
        "_fetch",
        lambda url: json_bytes(release) if url == updater.LATEST_API else assets[url],
    )

    ok, message = updater.upgrade_current_install()

    assert ok is True, message
    assert cxs.read_bytes() == b"new-cxs"
    assert (tmp_path / "codex-cxs").read_bytes() == b"new-codex"
    assert "Reinstalled cxs and codex-cxs" in message


@pytest.mark.skipif(os.name == "nt", reason="Windows releases do not ship codex-cxs")
def test_upgrade_follows_patched_codex_source_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_statebar, "__version__", "0.1.2", raising=False)

    def archive(member, data):
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz") as bundle:
            info = tarfile.TarInfo(member)
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
        return payload.getvalue()

    cxs_archive = archive("cxs", b"new-cxs")
    codex_archive = archive("codex-cxs", b"baseline-codex")
    latest = {
        "tag_name": "v0.1.2",
        "assets": [
            {"name": "cxs-test.tar.gz", "browser_download_url": "cxs-archive"},
            {
                "name": "cxs-test.tar.gz.sha256",
                "browser_download_url": "cxs-checksum",
            },
            {
                "name": "codex-cxs-manifest.json",
                "browser_download_url": "manifest",
            },
        ],
    }
    baseline = {
        "tag_name": "v0.1.1",
        "assets": [
            {
                "name": "codex-cxs-test.tar.gz",
                "browser_download_url": "codex-archive",
            },
            {
                "name": "codex-cxs-test.tar.gz.sha256",
                "browser_download_url": "codex-checksum",
            },
        ],
    }
    responses = {
        updater.LATEST_API: json_bytes(latest),
        "manifest": json_bytes({"source_tag": "v0.1.1"}),
        f"{updater.RELEASE_TAG_API}/v0.1.1": json_bytes(baseline),
        "cxs-archive": cxs_archive,
        "cxs-checksum": hashlib.sha256(cxs_archive).hexdigest().encode(),
        "codex-archive": codex_archive,
        "codex-checksum": hashlib.sha256(codex_archive).hexdigest().encode(),
    }
    cxs = tmp_path / "cxs"
    cxs.write_bytes(b"old-cxs")
    (tmp_path / "codex-cxs").write_bytes(b"old-codex")
    monkeypatch.setattr(updater, "_target", lambda: "test")
    monkeypatch.setattr(updater, "_destination", lambda: cxs)
    monkeypatch.setattr(updater, "_fetch", responses.__getitem__)

    ok, message = updater.upgrade_current_install()

    assert ok is True, message
    assert cxs.read_bytes() == b"new-cxs"
    assert (tmp_path / "codex-cxs").read_bytes() == b"baseline-codex"


def json_bytes(value):
    import json
    return json.dumps(value).encode()


@pytest.mark.skipif(os.name == "nt", reason="Windows releases do not ship codex-cxs")
def test_upgrade_installs_code_mode_host_over_fallback_link(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_statebar, "__version__", "0.1.1", raising=False)

    def archive(member, data):
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz") as bundle:
            info = tarfile.TarInfo(member)
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
        return payload.getvalue()

    assets, listing = {}, []
    for binary, data in (("cxs", b"new-cxs"), ("codex-cxs", b"new-codex"),
                         ("codex-code-mode-host", b"new-host")):
        packed = archive(binary, data)
        assets[binary] = packed
        assets[binary + ".sha256"] = hashlib.sha256(packed).hexdigest().encode()
        listing += [
            {"name": f"{binary}-test.tar.gz", "browser_download_url": binary},
            {"name": f"{binary}-test.tar.gz.sha256",
             "browser_download_url": binary + ".sha256"},
        ]
    release = {"tag_name": "v0.1.1", "assets": listing}
    cxs = tmp_path / "cxs"
    cxs.write_bytes(b"old-cxs")
    (tmp_path / "codex-cxs").write_bytes(b"old-codex")
    elsewhere = tmp_path / "official-host"
    elsewhere.write_bytes(b"official")
    (tmp_path / "codex-code-mode-host").symlink_to(elsewhere)
    monkeypatch.setattr(updater, "_target", lambda: "test")
    monkeypatch.setattr(updater, "_destination", lambda: cxs)
    monkeypatch.setattr(
        updater,
        "_fetch",
        lambda url: json_bytes(release) if url == updater.LATEST_API else assets[url],
    )

    ok, message = updater.upgrade_current_install()

    assert ok is True, message
    host = tmp_path / "codex-code-mode-host"
    assert not host.is_symlink() and host.read_bytes() == b"new-host"
    assert os.access(host, os.X_OK)
    assert elsewhere.read_bytes() == b"official"


def test_upgrade_refuses_concurrent_run_and_cleans_staging(tmp_path, monkeypatch):
    cxs = tmp_path / "cxs"
    cxs.write_bytes(b"old-cxs")
    monkeypatch.setattr(updater, "_target", lambda: "test")
    monkeypatch.setattr(updater, "_destination", lambda: cxs)

    with (tmp_path / ".cxs-upgrade.lock").open("a+") as held:
        assert updater._try_lock(held)
        ok, message = updater.upgrade_current_install()
    assert ok is False and "already running" in message

    def fail(url):
        raise OSError("network down")

    monkeypatch.setattr(updater, "_fetch", fail)
    ok, message = updater.upgrade_current_install()
    assert ok is False and "network down" in message
    assert cxs.read_bytes() == b"old-cxs"


def _native_release(tag, source_tag, cxs_data, *, codex_data=b"codex", host_data=b"host",
                    fingerprint="f" * 64, codex_version="0.164.0"):
    def archive(member, data):
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz") as bundle:
            info = tarfile.TarInfo(member)
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
        return payload.getvalue()

    assets, listing = {}, []
    for binary, data in (("cxs", cxs_data), ("codex-cxs", codex_data),
                         ("codex-code-mode-host", host_data)):
        packed = archive(binary, data)
        url = f"{tag}/{binary}"
        assets[url] = packed
        assets[url + ".sha256"] = hashlib.sha256(packed).hexdigest().encode()
        listing += [
            {"name": f"{binary}-test.tar.gz", "browser_download_url": url},
            {"name": f"{binary}-test.tar.gz.sha256", "browser_download_url": url + ".sha256"},
        ]
    manifest = {"schema": 1, "source_tag": source_tag, "fingerprint": fingerprint,
                "codex_version": codex_version, "assets": {}}
    assets[f"{tag}/manifest"] = json_bytes(manifest)
    listing.append({"name": "codex-cxs-manifest.json",
                    "browser_download_url": f"{tag}/manifest"})
    return {"tag_name": tag, "assets": listing}, assets


_PUBLISHED = {}


@pytest.fixture(autouse=True)
def _fresh_published():
    _PUBLISHED.clear()


def _serve(monkeypatch, release, assets, fetched):
    # Earlier releases stay downloadable, as patched Codex reuse requires.
    _PUBLISHED[release["tag_name"]] = (release, assets)

    def fetch(url):
        fetched.append(url)
        if url == updater.LATEST_API:
            return json_bytes(release)
        if url.startswith(updater.RELEASE_TAG_API + "/"):
            return json_bytes(_PUBLISHED[url.rsplit("/", 1)[1]][0])
        for _release, published in [(release, assets), *_PUBLISHED.values()]:
            if url in published:
                return published[url]
        raise KeyError(url)
    monkeypatch.setattr(updater, "_fetch", fetch)


@pytest.mark.skipif(os.name == "nt", reason="Windows releases do not ship codex-cxs")
def test_cxs_only_release_skips_unchanged_native_binaries(tmp_path, monkeypatch):
    cxs = tmp_path / "cxs"
    cxs.write_bytes(b"old-cxs")
    monkeypatch.setattr(updater, "_target", lambda: "test")
    monkeypatch.setattr(updater, "_destination", lambda: cxs)

    monkeypatch.setattr(codex_statebar, "__version__", "0.3.0", raising=False)
    release, assets = _native_release("v0.3.1", "v0.3.1", b"cxs-031")
    fetched = []
    _serve(monkeypatch, release, assets, fetched)
    ok, message = updater.upgrade_current_install()
    assert ok is True, message
    receipt = json.loads((tmp_path / ".codex-cxs-receipt.json").read_text())
    assert receipt["source_tag"] == "v0.3.1"
    assert receipt["files"]["codex-cxs"] == hashlib.sha256(b"codex").hexdigest()
    assert receipt["files"]["codex-code-mode-host"] == hashlib.sha256(b"host").hexdigest()

    # Next release reuses the same native build: only cxs is downloaded.
    monkeypatch.setattr(codex_statebar, "__version__", "0.3.1", raising=False)
    release, assets = _native_release("v0.4.0", "v0.3.1", b"cxs-040")
    fetched = []
    _serve(monkeypatch, release, assets, fetched)
    ok, message = updater.upgrade_current_install()
    assert ok is True, message
    assert cxs.read_bytes() == b"cxs-040"
    assert not any(url.endswith(("codex-cxs", "codex-code-mode-host")) for url in fetched)
    assert "Upgraded cxs from v0.4.0" in message
    assert not list(tmp_path.glob("*.new"))


@pytest.mark.skipif(os.name == "nt", reason="Windows releases do not ship codex-cxs")
@pytest.mark.parametrize("change", ["tampered", "native_build", "symlinked_host"])
def test_native_binaries_reinstall_when_build_or_disk_changes(tmp_path, monkeypatch, change):
    cxs = tmp_path / "cxs"
    cxs.write_bytes(b"old-cxs")
    monkeypatch.setattr(updater, "_target", lambda: "test")
    monkeypatch.setattr(updater, "_destination", lambda: cxs)
    monkeypatch.setattr(codex_statebar, "__version__", "0.3.0", raising=False)
    release, assets = _native_release("v0.3.1", "v0.3.1", b"cxs-031")
    _serve(monkeypatch, release, assets, [])
    assert updater.upgrade_current_install()[0]

    host = tmp_path / "codex-code-mode-host"
    source_tag, codex_data = "v0.3.1", b"codex"
    if change == "tampered":
        (tmp_path / "codex-cxs").write_bytes(b"truncated")
    elif change == "native_build":
        source_tag, codex_data = "v0.4.0", b"codex-165"
    else:
        other = tmp_path / "official-host"
        other.write_bytes(b"official")
        host.unlink()
        host.symlink_to(other)

    monkeypatch.setattr(codex_statebar, "__version__", "0.3.1", raising=False)
    release, assets = _native_release("v0.4.0", source_tag, b"cxs-040", codex_data=codex_data)
    _serve(monkeypatch, release, assets, [])
    ok, message = updater.upgrade_current_install()

    assert ok is True, message
    assert (tmp_path / "codex-cxs").read_bytes() == codex_data
    assert not host.is_symlink() and host.read_bytes() == b"host"
    receipt = json.loads((tmp_path / ".codex-cxs-receipt.json").read_text())
    assert receipt["source_tag"] == source_tag


@pytest.mark.skipif(os.name == "nt", reason="Windows releases do not ship codex-cxs")
def test_failed_native_download_keeps_receipt_and_leaves_no_staging(tmp_path, monkeypatch):
    cxs = tmp_path / "cxs"
    cxs.write_bytes(b"old-cxs")
    monkeypatch.setattr(updater, "_target", lambda: "test")
    monkeypatch.setattr(updater, "_destination", lambda: cxs)
    monkeypatch.setattr(codex_statebar, "__version__", "0.3.0", raising=False)
    release, assets = _native_release("v0.3.1", "v0.3.1", b"cxs-031")
    _serve(monkeypatch, release, assets, [])
    assert updater.upgrade_current_install()[0]
    receipt_before = (tmp_path / ".codex-cxs-receipt.json").read_text()

    monkeypatch.setattr(codex_statebar, "__version__", "0.3.1", raising=False)
    release, assets = _native_release("v0.4.0", "v0.4.0", b"cxs-040", codex_data=b"codex-165")
    assets["v0.4.0/codex-code-mode-host.sha256"] = b"0" * 64
    _serve(monkeypatch, release, assets, [])
    ok, message = updater.upgrade_current_install()

    assert ok is False and "SHA-256" in message
    assert cxs.read_bytes() == b"cxs-031"
    assert (tmp_path / "codex-cxs").read_bytes() == b"codex"
    assert (tmp_path / ".codex-cxs-receipt.json").read_text() == receipt_before
    assert not list(tmp_path.glob("*.new"))


def test_rate_limited_api_uses_public_release_metadata(monkeypatch):
    import urllib.error
    def fetch(url):
        if url == updater.LATEST_API:
            raise urllib.error.HTTPError(url, 403, 'rate limit', {}, None)
        assert url.endswith('/releases/latest/download/cxs-release.json')
        return b'{"tag_name":"v99.0.0","assets":[]}'
    monkeypatch.setattr(updater, '_fetch', fetch)
    assert updater.latest_release()['tag_name'] == 'v99.0.0'


def test_rate_limited_baseline_keeps_checksum_urls(monkeypatch):
    import urllib.error
    def fetch(url):
        raise urllib.error.HTTPError(url, 403, 'rate limit', {}, None)
    monkeypatch.setattr(updater, '_fetch', fetch)
    release = updater._baseline_release('v0.3.0')
    archive, checksum = updater._asset_urls(release, 'macos-arm64', 'codex-cxs')
    assert archive.endswith('/v0.3.0/codex-cxs-macos-arm64.tar.gz')
    assert checksum == archive + '.sha256'
