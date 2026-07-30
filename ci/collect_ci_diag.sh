#!/bin/bash
# Безопасная диагностика неуспешного CI для загрузки в GitHub artifacts.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
mkdir -p ci_artifacts

bash collect_diag.sh >/dev/null 2>&1 || true
for report in diag_server_*.txt; do
    [ -f "$report" ] && mv "$report" ci_artifacts/
done

docker compose ps -a > ci_artifacts/compose-ps.txt 2>&1 || true
docker compose config --images > ci_artifacts/compose-images.txt 2>&1 || true
bash tls/manage_tls.sh check > ci_artifacts/tls-check.txt 2>&1 || true

echo "✓ Диагностика CI собрана в ci_artifacts/"
