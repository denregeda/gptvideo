"""Одноразовая WSS-проверка smoke_test.sh без вывода токена устройства."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, "/agent")
from ds_ws_client import WSClient  # noqa: E402


def main() -> int:
    client = WSClient(
        os.environ.get("DS_SMOKE_WSS_URL", "https://nginx"),
        int(os.environ["DS_SMOKE_SCREEN_ID"]),
        os.environ["DS_SMOKE_DEVICE_TOKEN"],
        on_command=lambda: None,
        ca_file=os.environ.get("TLS_CA_FILE", "/etc/ds-tls/ca.crt"),
    )
    host, port, path = client._parse_url()
    sock = client._open_socket(host, port)
    try:
        return 0 if client._handshake(sock, host, path) else 1
    finally:
        sock.close()


if __name__ == "__main__":
    raise SystemExit(main())
