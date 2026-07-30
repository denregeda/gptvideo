#!/bin/bash
# Создаёт переносимый комплект агента с публичным CA (без приватных ключей).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${1:-$ROOT_DIR/agent_bundle}"
TLS_DIR="${TLS_OUTPUT_DIR:-$ROOT_DIR/tls/generated}"

bash "$ROOT_DIR/tls/manage_tls.sh" ensure
[ -s "$TLS_DIR/ca.crt" ] || { echo "ОШИБКА: не найден $TLS_DIR/ca.crt"; exit 1; }
[ ! -e "$DEST" ] || { echo "ОШИБКА: каталог уже существует: $DEST"; exit 1; }

mkdir -p "$DEST"
cp "$ROOT_DIR/install.sh" "$DEST/"
cp "$TLS_DIR/ca.crt" "$DEST/ca.crt"
cp -R "$ROOT_DIR/agent" "$DEST/agent"
(
    cd "$DEST"
    if command -v sha256sum >/dev/null 2>&1; then
        find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum
    else
        find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 shasum -a 256
    fi > SHA256SUMS
)
chmod 644 "$DEST/ca.crt"
echo "✓ Комплект агента создан: $DEST"
echo "  В нём есть только публичный ca.crt; приватные ключи не экспортируются."
