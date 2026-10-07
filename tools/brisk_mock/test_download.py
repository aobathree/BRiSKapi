"""Downloader checks with local fake HTTP responses, never a broker session."""
import gzip
import hashlib
import importlib.util
from io import BytesIO
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("brisk_download", Path(__file__).with_name("download_mock.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_download_gzip_cache_repair_and_pins(tmp_path, monkeypatch):
    payloads = {"a": b"public fixture", "b": b"second fixture"}
    manifest = {"assets": {name: {"url": name, "sha256": hashlib.sha256(data).hexdigest()}
                            for name, data in payloads.items()}}
    monkeypatch.setattr(module.json, "loads", lambda _: manifest)
    calls = []

    def response(url, timeout):
        calls.append(url)
        return BytesIO(gzip.compress(payloads[url]) if url == "a" else payloads[url])

    monkeypatch.setattr(module, "urlopen", response)
    module.download(tmp_path)
    assert calls == ["a", "b"]
    assert (tmp_path / "a").read_bytes() == payloads["a"]
    module.download(tmp_path)
    assert calls == ["a", "b"]
    (tmp_path / "b").write_bytes(b"broken")
    module.download(tmp_path)
    assert calls == ["a", "b", "b"]
    monkeypatch.setattr(module, "urlopen", lambda *a, **kw: BytesIO(b"changed"))
    (tmp_path / "a").unlink()
    with pytest.raises(ValueError, match="asset changed"):
        module.download(tmp_path)
    assert not (tmp_path / "a").exists()
