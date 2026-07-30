#!/bin/bash
# Полный сквозной прогон в чистом GitHub runner.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

[ "${CI:-}" = "true" ] && [ "${GITHUB_ACTIONS:-}" = "true" ] || {
    echo "ОШИБКА: интеграционный прогон разрешён только в GitHub Actions."; exit 1;
}

bash tls/manage_tls.sh ensure
docker compose up -d --build
bash ci/wait_stack.sh
bash migrate.sh
bash ci/prepare_test_admin.sh
bash smoke_test.sh
bash ui_test.sh

echo "✓ Интеграционный CI завершён без ошибок"
