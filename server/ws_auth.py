"""Извлечение WebSocket-секретов из заголовков, но никогда из URL."""
from typing import Optional

from fastapi import WebSocket

DASHBOARD_AUTH_PROTOCOL = "ds-auth"
MAX_WEBSOCKET_TOKEN_LENGTH = 4096


def _safe_token(value: Optional[str]) -> str:
    token = (value or "").strip()
    if not token or len(token) > MAX_WEBSOCKET_TOKEN_LENGTH:
        return ""
    if "\r" in token or "\n" in token:
        return ""
    return token


def agent_token_from_websocket(ws: WebSocket) -> str:
    """Токен устройства передаётся только заголовком X-Token."""
    return _safe_token(ws.headers.get("x-token"))


def dashboard_token_from_websocket(ws: WebSocket) -> tuple[str, Optional[str]]:
    """JWT браузера — второй WebSocket subprotocol; первый всегда ds-auth."""
    protocols = list(ws.scope.get("subprotocols") or [])
    if not protocols:
        raw = ws.headers.get("sec-websocket-protocol", "")
        protocols = [item.strip() for item in raw.split(",") if item.strip()]
    if len(protocols) != 2 or protocols[0] != DASHBOARD_AUTH_PROTOCOL:
        return "", None
    token = _safe_token(protocols[1])
    return (token, DASHBOARD_AUTH_PROTOCOL) if token else ("", None)
