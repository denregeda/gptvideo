#!/bin/bash
# Уборка только одноразового CI-стека; не должна скрывать исходную ошибку job.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

[ "${CI:-}" = "true" ] && [ "${GITHUB_ACTIONS:-}" = "true" ] || {
    echo "ОШИБКА: cleanup.sh разрешён только в GitHub Actions."; exit 1;
}

if [ ! -f .env ]; then
    echo "• CI .env отсутствует — Docker-стек не создавался"
    exit 0
fi

docker compose down -v --remove-orphans \
    || echo "⚠ Не удалось полностью убрать CI-стек; runner будет уничтожен GitHub"
