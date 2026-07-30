"""Единая настройка защищённого транспорта агента."""
from __future__ import annotations

import os
from pathlib import Path


def configure_transport(server_section) -> tuple[str, str | None]:
    """Вернуть базовый URL и настроить CA для всех requests.Session."""
    scheme = server_section.get("scheme", "http").strip().lower()
    if scheme not in {"http", "https"}:
        raise ValueError("scheme должен быть http или https")

    host = server_section["host"].strip()
    port = server_section.get(
        "port", "443" if scheme == "https" else "80").strip()
    if not host or not port.isdigit():
        raise ValueError("Некорректные host/port сервера")

    default_port = "443" if scheme == "https" else "80"
    authority = host if port == default_port else f"{host}:{port}"
    ca_file = server_section.get("ca_file", "").strip() or None

    if ca_file:
        if scheme != "https":
            raise ValueError("ca_file допустим только для HTTPS")
        if not Path(ca_file).is_file():
            raise FileNotFoundError(
                f"CA-сертификат не найден: {ca_file}")
        os.environ["REQUESTS_CA_BUNDLE"] = ca_file

    return f"{scheme}://{authority}", ca_file
