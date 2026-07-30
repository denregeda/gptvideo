#!/bin/bash
# Регрессия CA: идемпотентность, SAN, ротация и HTTP-политика.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TLS_TEST_DIR="$(mktemp -d)"
trap 'rm -rf "$TLS_TEST_DIR"' EXIT

run_tls() {
    TLS_OUTPUT_DIR="$TLS_TEST_DIR" \
    TLS_SERVER_NAME=display.local \
    TLS_EXTRA_SANS="$1" \
    TLS_LEGACY_HTTP="$2" \
        bash "$ROOT_DIR/tls/manage_tls.sh" ensure >/dev/null
}

run_tls "10.20.30.40,screen-server.local" true
ca_before="$(openssl x509 -in "$TLS_TEST_DIR/ca.crt" -noout -sha256 -fingerprint)"
cert_before="$(openssl x509 -in "$TLS_TEST_DIR/server.crt" -noout -sha256 -fingerprint)"

run_tls "10.20.30.40,screen-server.local" true
cert_repeat="$(openssl x509 -in "$TLS_TEST_DIR/server.crt" -noout -sha256 -fingerprint)"
[ "$cert_before" = "$cert_repeat" ] || {
    echo "ОШИБКА: идемпотентный запуск перевыпустил сертификат."; exit 1;
}

grep -qx 'DNS:screen-server.local' "$TLS_TEST_DIR/sans.txt"
grep -qx 'IP:10.20.30.40' "$TLS_TEST_DIR/sans.txt"
grep -q 'location /api/' "$TLS_TEST_DIR/http-policy.conf"

run_tls "10.20.30.41" false
ca_after="$(openssl x509 -in "$TLS_TEST_DIR/ca.crt" -noout -sha256 -fingerprint)"
[ "$ca_before" = "$ca_after" ] || {
    echo "ОШИБКА: при смене SAN изменился корневой CA."; exit 1;
}
grep -qx 'IP:10.20.30.41' "$TLS_TEST_DIR/sans.txt"
if grep -q 'location /api/' "$TLS_TEST_DIR/http-policy.conf"; then
    echo "ОШИБКА: строгий HTTP-режим оставил агентский API."; exit 1
fi

invalid_dir="$(mktemp -d)"
if TLS_OUTPUT_DIR="$invalid_dir" TLS_SERVER_NAME='bad/name' \
        bash "$ROOT_DIR/tls/manage_tls.sh" ensure >/dev/null 2>&1; then
    echo "ОШИБКА: некорректное имя TLS было принято."; exit 1
fi
rm -rf "$invalid_dir"

TLS_OUTPUT_DIR="$TLS_TEST_DIR" \
TLS_SERVER_NAME=display.local \
TLS_EXTRA_SANS="10.20.30.41" \
TLS_LEGACY_HTTP=false \
    bash "$ROOT_DIR/tls/manage_tls.sh" check >/dev/null

echo "✓ TLS: идемпотентность, SAN, CA и политики проверены"
