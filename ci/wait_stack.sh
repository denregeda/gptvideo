#!/bin/bash
# Ожидание готовности всего Docker-стека с ограниченным таймаутом.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

EXPECTED="ds_postgres ds_redis ds_minio ds_api ds_celery ds_nginx ds_ntp"
deadline=$((SECONDS + 180))

while [ "$SECONDS" -lt "$deadline" ]; do
    all_running=1
    for container in $EXPECTED; do
        state="$(docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null || true)"
        [ "$state" = "running" ] || all_running=0
    done
    if [ "$all_running" -eq 1 ] \
       && curl --cacert tls/generated/ca.crt -fsS \
            https://localhost/health >/dev/null 2>&1; then
        echo "✓ Все контейнеры запущены, HTTPS /health отвечает"
        exit 0
    fi
    sleep 3
done

echo "ОШИБКА: стек не стал готов за 180 секунд."
docker compose ps -a
docker compose logs --tail=80 api nginx postgres redis
exit 1
