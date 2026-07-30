"""Регрессия атомарной загрузки медиа на агенте Astra Linux."""
import hashlib
import os
import sys
import types


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

_requests = sys.modules.get("requests")
if _requests is None:
    _requests = types.ModuleType("requests")
    sys.modules["requests"] = _requests
if not hasattr(_requests, "Session"):
    class _Session:
        def __init__(self):
            self.headers = {}

    _requests.Session = _Session

import ds_media_transfer  # noqa: E402
from ds_downloader import Downloader  # noqa: E402


def _md5(data):
    return hashlib.md5(data).hexdigest()


class _Response:
    def __init__(self, status, chunks, headers=None, before_read=None):
        self.status_code = status
        self.chunks = chunks
        self.headers = headers or {}
        self.before_read = before_read

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_content(self, chunk_size=None):
        if self.before_read:
            self.before_read()
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


class _SequenceSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.headers = {}

    def get(self, url, headers=None, **_kwargs):
        self.requests.append({"url": url, "headers": headers or {}})
        if not self.responses:
            raise OSError("no response")
        return self.responses.pop(0)


def _downloader(tmp_path, monkeypatch, responses):
    downloader = Downloader("http://server", "token", str(tmp_path))
    downloader.session = _SequenceSession(responses)
    monkeypatch.setattr(ds_media_transfer.time, "sleep", lambda _seconds: None)
    return downloader


def test_interrupted_download_preserves_current_media(tmp_path, monkeypatch):
    current = tmp_path / "ad.mp4"
    current.write_bytes(b"CURRENT")
    response = _Response(200, [b"NEW-", OSError("connection lost")])
    downloader = _downloader(tmp_path, monkeypatch, [response])

    assert downloader.download_file(1, "ad.mp4", _md5(b"NEW-FILE"), 8) is False
    assert current.read_bytes() == b"CURRENT"
    assert (tmp_path / ".ad.mp4.part").read_bytes() == b"NEW-"


def test_server_ignoring_range_restarts_part_file(tmp_path, monkeypatch):
    part = tmp_path / ".ad.mp4.part"
    part.write_bytes(b"OLD-")
    complete = b"COMPLETE"
    response = _Response(200, [complete])
    downloader = _downloader(tmp_path, monkeypatch, [response])

    assert downloader.download_file(1, "ad.mp4", _md5(complete), len(complete))
    assert (tmp_path / "ad.mp4").read_bytes() == complete
    assert not part.exists()
    assert downloader.session.requests[0]["headers"] == {"Range": "bytes=4-"}


def test_verified_download_replaces_file_only_after_full_write(tmp_path, monkeypatch):
    current = tmp_path / "ad.mp4"
    current.write_bytes(b"OLD")
    complete = b"NEW-CONTENT"
    observed = []
    response = _Response(
        200,
        [complete],
        before_read=lambda: observed.append(current.read_bytes()),
    )
    downloader = _downloader(tmp_path, monkeypatch, [response])

    assert downloader.download_file(1, "ad.mp4", _md5(complete), len(complete))
    assert observed == [b"OLD"]
    assert current.read_bytes() == complete


def test_hash_mismatch_never_replaces_current_media(tmp_path, monkeypatch):
    current = tmp_path / "ad.mp4"
    current.write_bytes(b"CURRENT")
    response = _Response(200, [b"BAD-DATA"])
    downloader = _downloader(tmp_path, monkeypatch, [response])

    assert downloader.download_file(1, "ad.mp4", _md5(b"EXPECTED"), 8) is False
    assert current.read_bytes() == b"CURRENT"
    assert not (tmp_path / ".ad.mp4.part").exists()


def test_valid_range_response_resumes_part_file(tmp_path, monkeypatch):
    part = tmp_path / ".ad.mp4.part"
    part.write_bytes(b"FIRST-")
    complete = b"FIRST-SECOND"
    response = _Response(
        206,
        [b"SECOND"],
        headers={"Content-Range": "bytes 6-11/12"},
    )
    downloader = _downloader(tmp_path, monkeypatch, [response])

    assert downloader.download_file(1, "ad.mp4", _md5(complete), len(complete))
    assert (tmp_path / "ad.mp4").read_bytes() == complete
    assert downloader.session.requests[0]["headers"] == {"Range": "bytes=6-"}


def test_invalid_complete_part_restarts_without_range(tmp_path, monkeypatch):
    part = tmp_path / ".ad.mp4.part"
    part.write_bytes(b"BAD-DATA")
    complete = b"EXPECTED"
    downloader = _downloader(tmp_path, monkeypatch, [_Response(200, [complete])])

    assert downloader.download_file(1, "ad.mp4", _md5(complete), len(complete))
    assert downloader.session.requests[0]["headers"] == {}
    assert (tmp_path / "ad.mp4").read_bytes() == complete


def test_short_response_is_resumed_on_next_attempt(tmp_path, monkeypatch):
    complete = b"FIRST-SECOND"
    responses = [
        _Response(200, [b"FIRST-"]),
        _Response(206, [b"SECOND"], headers={"Content-Range": "bytes 6-11/12"}),
    ]
    downloader = _downloader(tmp_path, monkeypatch, responses)

    assert downloader.download_file(1, "ad.mp4", _md5(complete), len(complete))
    assert downloader.session.requests[1]["headers"] == {"Range": "bytes=6-"}
    assert (tmp_path / "ad.mp4").read_bytes() == complete
