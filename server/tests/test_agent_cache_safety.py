"""Регрессия безопасности локального медиакеша агента.

Потеря связи с сервером не равна успешному пустому манифесту. При сетевой
ошибке агент обязан продолжать офлайн-воспроизведение и не удалять ролики.
"""
import os
import sys
import types

import pytest


_AGENT_DIRS = [
    "/agent",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "agent",
    ),
]
for _directory in _AGENT_DIRS:
    if os.path.isfile(os.path.join(_directory, "ds_downloader.py")):
        sys.path.insert(0, _directory)
        break

# Production-образ API не содержит зависимостей агента. Для этих unit-тестов
# достаточно минимальной Session: реальный HTTP подменяется monkeypatch ниже.
_requests = sys.modules.get("requests")
if _requests is None:
    _requests = types.ModuleType("requests")
    sys.modules["requests"] = _requests
if not hasattr(_requests, "Session"):
    class _Session:
        def __init__(self):
            self.headers = {}

    _requests.Session = _Session

from ds_cleanup import Cleanup  # noqa: E402
from ds_downloader import Downloader, FileManifestUnavailable  # noqa: E402


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def _downloader(tmp_path, monkeypatch, response=None, error=None):
    downloader = Downloader("http://server", "token", str(tmp_path))

    def _get(*_args, **_kwargs):
        if error:
            raise error
        return response

    monkeypatch.setattr(downloader.session, "get", _get, raising=False)
    return downloader


def test_manifest_network_error_is_not_an_empty_list(tmp_path, monkeypatch):
    downloader = _downloader(
        tmp_path,
        monkeypatch,
        error=OSError("network unavailable"),
    )

    with pytest.raises(FileManifestUnavailable):
        downloader.get_file_list(7)


def test_invalid_manifest_is_rejected(tmp_path, monkeypatch):
    downloader = _downloader(tmp_path, monkeypatch, response=_Response({"files": []}))

    with pytest.raises(FileManifestUnavailable):
        downloader.get_file_list(7)


def test_cleanup_keeps_cache_when_manifest_is_unavailable(tmp_path, monkeypatch):
    cached = tmp_path / "offline.mp4"
    cached.write_bytes(b"cached media")
    downloader = _downloader(
        tmp_path,
        monkeypatch,
        error=OSError("network unavailable"),
    )

    assert Cleanup(downloader, 7, str(tmp_path)).run() == 0
    assert cached.read_bytes() == b"cached media"


def test_cleanup_accepts_successful_empty_manifest(tmp_path, monkeypatch):
    cached = tmp_path / "obsolete.mp4"
    cached.write_bytes(b"old media")
    downloader = _downloader(tmp_path, monkeypatch, response=_Response([]))

    assert Cleanup(downloader, 7, str(tmp_path)).run() == 1
    assert not cached.exists()


def test_sync_reports_manifest_failure_without_crashing(tmp_path, monkeypatch):
    downloader = _downloader(
        tmp_path,
        monkeypatch,
        error=OSError("network unavailable"),
    )

    assert downloader.sync_files(7) == (0, 1)


def test_legacy_cleanup_keeps_cache_when_manifest_is_unavailable(tmp_path, monkeypatch):
    cached = tmp_path / "offline.mp4"
    cached.write_bytes(b"cached media")
    downloader = _downloader(
        tmp_path,
        monkeypatch,
        error=OSError("network unavailable"),
    )

    assert downloader.cleanup_unused(7) == 0
    assert cached.exists()
