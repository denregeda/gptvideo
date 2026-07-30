"""Проверка TLS nginx изнутри серверного стека."""
from __future__ import annotations

import os
import socket
import ssl
import time


def check_tls_endpoint() -> tuple[str, str]:
    """Вернуть (ok|warn, описание) либо выбросить исключение при отказе."""
    host = os.getenv("TLS_CHECK_HOST", "nginx")
    port = int(os.getenv("TLS_CHECK_PORT", "443"))
    server_name = os.getenv("TLS_CHECK_SERVER_NAME", "nginx")
    ca_file = os.getenv("TLS_CA_FILE", "/etc/ds-tls/ca.crt")
    warn_days = int(os.getenv("TLS_RENEW_DAYS", "30"))

    context = ssl.create_default_context(cafile=ca_file)
    with socket.create_connection((host, port), timeout=3) as raw:
        with context.wrap_socket(raw, server_hostname=server_name) as tls:
            cert = tls.getpeercert()

    expires_at = ssl.cert_time_to_seconds(cert["notAfter"])
    days_left = int((expires_at - time.time()) / 86400)
    if days_left < 0:
        raise RuntimeError("сертификат истёк")
    status = "ok" if days_left >= warn_days else "warn"
    return status, f"цепочка и hostname проверены; осталось {days_left} дн."
