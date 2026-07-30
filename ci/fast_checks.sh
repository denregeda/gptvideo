#!/bin/bash
# Быстрые проверки без запуска серверного стека.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "▶ Синтаксис shell"
while IFS= read -r -d '' file; do
    bash -n "$file"
done < <(git ls-files -z '*.sh')

echo "▶ Синтаксис Python"
PYCACHE_DIR="$(mktemp -d)"
trap 'rm -rf "$PYCACHE_DIR"' EXIT
PYTHONPYCACHEPREFIX="$PYCACHE_DIR" python3 -m compileall -q agent server

echo "▶ Синтаксис JavaScript"
while IFS= read -r -d '' file; do
    node --check "$file"
done < <(git ls-files -z '*.js')

echo "▶ Секреты и приватные ключи"
if git ls-files | grep -Eq '(^|/)\.env$|^tls/generated/|\.(key|pem)$'; then
    echo "ОШИБКА: в git найден секретный файл."; exit 1
fi
if git grep -I -n -E -- '-----BEGIN ([A-Z ]+ )?PRIVATE KEY-----'; then
    echo "ОШИБКА: в отслеживаемых файлах найден приватный ключ."; exit 1
fi

echo "▶ Docker Compose"
docker compose config --quiet

echo "▶ TLS-автоматизация"
bash ci/test_tls.sh

echo "✓ Быстрые проверки пройдены"
