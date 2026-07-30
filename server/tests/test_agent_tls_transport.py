"""Агент должен проверять TLS одинаково для REST и WebSocket."""
import os
import sys

import pytest

for _directory in (
    "/agent",
    os.path.join(os.path.dirname(__file__), "..", "..", "agent"),
):
    if os.path.isfile(os.path.join(_directory, "ds_transport.py")):
        sys.path.insert(0, os.path.abspath(_directory))
        break

from ds_transport import configure_transport  # noqa: E402
from ds_ws_client import WSClient  # noqa: E402


def test_https_transport_sets_private_ca_for_requests(tmp_path, monkeypatch):
    ca_file = tmp_path / "ca.crt"
    ca_file.write_text("test certificate placeholder")
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)

    url, configured_ca = configure_transport({
        "scheme": "https",
        "host": "display.local",
        "port": "443",
        "ca_file": str(ca_file),
    })

    assert url == "https://display.local"
    assert configured_ca == str(ca_file)
    assert os.environ["REQUESTS_CA_BUNDLE"] == str(ca_file)


def test_legacy_http_config_remains_backward_compatible(monkeypatch):
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)

    url, configured_ca = configure_transport({
        "host": "10.0.119.100",
        "port": "80",
    })

    assert url == "http://10.0.119.100"
    assert configured_ca is None
    assert "REQUESTS_CA_BUNDLE" not in os.environ


def test_https_rejects_missing_ca_file():
    with pytest.raises(FileNotFoundError):
        configure_transport({
            "scheme": "https",
            "host": "display.local",
            "ca_file": "/missing/ds-ca.crt",
        })


def test_websocket_wraps_tcp_in_verified_tls(monkeypatch, tmp_path):
    ca_file = tmp_path / "ca.crt"
    ca_file.write_text("placeholder")
    raw_socket = object()
    tls_socket = object()

    class _Context:
        def wrap_socket(self, raw, server_hostname):
            assert raw is raw_socket
            assert server_hostname == "display.local"
            return tls_socket

    monkeypatch.setattr(
        "ds_ws_client.socket.create_connection",
        lambda address, timeout: (
            raw_socket
            if address == ("display.local", 443) and timeout == 10
            else None
        ),
    )
    monkeypatch.setattr(
        "ds_ws_client.ssl.create_default_context",
        lambda cafile: _Context() if cafile == str(ca_file) else None,
    )
    client = WSClient(
        "https://display.local",
        7,
        "device-secret",
        on_command=lambda: None,
        ca_file=str(ca_file),
    )

    assert client._open_socket("display.local", 443) is tls_socket
