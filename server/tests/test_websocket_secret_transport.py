"""WebSocket-секреты не должны попадать в URL и access log."""
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from deps import create_access_token, get_db
from routers import websockets
from ws_auth import (
    DASHBOARD_AUTH_PROTOCOL,
    agent_token_from_websocket,
    dashboard_token_from_websocket,
)

for _directory in ("/agent", os.path.join(os.path.dirname(__file__), "..", "..", "agent")):
    if os.path.isfile(os.path.join(_directory, "ds_ws_client.py")):
        sys.path.insert(0, os.path.abspath(_directory))
        break

from ds_ws_client import WSClient  # noqa: E402


class _HandshakeSocket:
    def __init__(self):
        self.request = b""
        self._response = b"HTTP/1.1 101 Switching Protocols\r\n\r\n"

    def sendall(self, data):
        self.request += data

    def recv(self, _size):
        response, self._response = self._response, b""
        return response


class _Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row

    def scalar(self):
        return 0


class _AgentDB:
    def execute(self, query, params=None):
        if "FROM screens" in str(query):
            token = (params or {}).get("t")
            row = SimpleNamespace(id=7, name="Test screen") \
                if token == "device-secret" else None
            return _Result(row)
        return _Result()


class _FakeWebSocket:
    def __init__(self, headers=None, protocols=None, query_token=None):
        self.headers = headers or {}
        self.scope = {"subprotocols": protocols or []}
        self.query_params = {"token": query_token} if query_token else {}


def _client():
    return WSClient(
        "http://server.local",
        7,
        "device-secret",
        on_command=lambda: None,
    )


def test_agent_websocket_url_contains_no_device_token():
    _host, _port, path = _client()._parse_url()

    assert path == "/api/ws/agent/7"
    assert "device-secret" not in path
    assert "token=" not in path


def test_agent_handshake_sends_token_only_in_header():
    client = _client()
    socket = _HandshakeSocket()

    assert client._handshake(socket, "server.local", "/api/ws/agent/7")

    request = socket.request.decode()
    request_line = request.splitlines()[0]
    assert request_line == "GET /api/ws/agent/7 HTTP/1.1"
    assert "X-Token: device-secret\r\n" in request
    assert "device-secret" not in request_line


def test_agent_handshake_rejects_header_injection():
    client = _client()
    client.token = "device-secret\r\nInjected: yes"
    socket = _HandshakeSocket()

    with pytest.raises(ValueError):
        client._handshake(socket, "server.local", "/api/ws/agent/7")

    assert socket.request == b""


def test_agent_auth_ignores_query_and_reads_x_token_header():
    ws = _FakeWebSocket(
        headers={"x-token": "device-secret"},
        query_token="leaked-query-secret",
    )
    assert agent_token_from_websocket(ws) == "device-secret"

    query_only = _FakeWebSocket(query_token="leaked-query-secret")
    assert agent_token_from_websocket(query_only) == ""


def test_dashboard_subprotocol_extracts_jwt_without_echoing_it():
    ws = _FakeWebSocket(protocols=["ds-auth", "signed.jwt.value"])

    token, accepted = dashboard_token_from_websocket(ws)

    assert token == "signed.jwt.value"
    assert accepted == DASHBOARD_AUTH_PROTOCOL
    assert accepted != token


@pytest.mark.parametrize("protocols", [
    [],
    ["ds-auth"],
    ["wrong-protocol", "signed.jwt.value"],
    ["ds-auth", "signed.jwt.value", "unexpected"],
    ["ds-auth", "bad\r\nInjected"],
])
def test_dashboard_rejects_malformed_subprotocol_credentials(protocols):
    token, accepted = dashboard_token_from_websocket(
        _FakeWebSocket(protocols=protocols))

    assert token == ""
    assert accepted is None


def test_dashboard_jwt_is_sent_as_subprotocol_not_query_string():
    source_path = os.path.join(
        os.path.dirname(__file__), "..", "static", "js", "core", "ws-dashboard.js")
    source = open(source_path, encoding="utf-8").read()

    assert "?token=" not in source
    assert "new WebSocket(wsUrl, ['ds-auth', wsToken])" in source


def test_agent_query_token_is_rejected_but_header_token_is_accepted():
    app = FastAPI()
    app.include_router(websockets.router)
    app.dependency_overrides[get_db] = lambda: _AgentDB()
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/ws/agent/7?token=device-secret"):
            pass
    assert error.value.code == 4401

    with client.websocket_connect(
            "/ws/agent/7", headers={"X-Token": "device-secret"}) as socket:
        socket.send_json({"type": "ping"})
        assert socket.receive_json() == {"type": "pong"}


def test_dashboard_query_token_is_rejected():
    app = FastAPI()
    app.include_router(websockets.router)
    app.dependency_overrides[get_db] = lambda: _AgentDB()
    token = create_access_token({"sub": "existing-user"})

    with pytest.raises(WebSocketDisconnect) as error:
        with TestClient(app).websocket_connect(
                f"/ws/dashboard?token={token}"):
            pass

    assert error.value.code == 4401
