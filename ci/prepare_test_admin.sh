#!/bin/bash
# Возвращает встроенному admin тестовый пароль только в изолированном CI.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

[ "${CI:-}" = "true" ] && [ "${GITHUB_ACTIONS:-}" = "true" ] || {
    echo "ОШИБКА: prepare_test_admin.sh разрешён только в GitHub Actions."; exit 1;
}

set -a
. ./.env
set +a

docker compose exec -T postgres psql \
    -v ON_ERROR_STOP=1 \
    -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -c 'UPDATE users
        SET password_hash = $$$2b$12$1rFNE5wroo/gieqz2mQYce4d.Dy6nVQv0pQS.kefuruC6Q7ma9VRG$$,
            must_change_password = FALSE,
            session_version = session_version + 1
        WHERE username = $$admin$$;' >/dev/null

echo "✓ Тестовый admin подготовлен"

login_code="$(docker compose exec -T api curl -s -o /dev/null -w '%{http_code}' \
    -X POST http://localhost:8000/token \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    -d 'username=admin&password=admin123')"
[ "$login_code" = "200" ] || {
    echo "ОШИБКА: тестовый admin не прошёл вход (HTTP $login_code)."; exit 1;
}

echo "✓ Тестовый вход admin проверен"
