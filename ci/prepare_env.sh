#!/bin/bash
# Одноразовое окружение GitHub Actions. На рабочем сервере не запускается.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

[ "${CI:-}" = "true" ] || {
    echo "ОШИБКА: prepare_env.sh разрешён только при CI=true."; exit 1;
}
[ ! -e .env ] || {
    echo "ОШИБКА: .env уже существует — не перезаписываю."; exit 1;
}

umask 077
DB_PASSWORD="ci_db_$(openssl rand -hex 16)"
MINIO_PASSWORD="ci_minio_$(openssl rand -hex 16)"
SECRET_KEY="$(openssl rand -hex 32)"

cat > .env <<EOF
POSTGRES_DB=display_system
POSTGRES_USER=display_user
DB_PASSWORD=$DB_PASSWORD
MINIO_USER=ds_minio
MINIO_PASSWORD=$MINIO_PASSWORD
SECRET_KEY=$SECRET_KEY
AUTH_ACCOUNT_FAILURE_LIMIT=5
AUTH_IP_FAILURE_LIMIT=20
AUTH_FAILURE_WINDOW_SECONDS=900
TLS_SERVER_NAME=display.local
TLS_EXTRA_SANS=
TLS_RENEW_DAYS=30
TLS_LEGACY_HTTP=true
EOF

echo "✓ Одноразовый .env для CI создан"
