#!/bin/bash
# Управление локальным CA, серверным сертификатом и HTTP-политикой.
# Идемпотентно: CA сохраняется, серверный сертификат обновляется заранее.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TLS_DIR="${TLS_OUTPUT_DIR:-$ROOT_DIR/tls/generated}"
ENV_FILE="${TLS_ENV_FILE:-$ROOT_DIR/.env}"
ACTION="${1:-ensure}"

env_value() {
    local key="$1" fallback="${2:-}" value=""
    if [ -f "$ENV_FILE" ]; then
        value="$(awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE")"
    fi
    printf '%s' "${value:-$fallback}"
}

SERVER_NAME="${TLS_SERVER_NAME:-$(env_value TLS_SERVER_NAME display.local)}"
EXTRA_SANS="${TLS_EXTRA_SANS:-$(env_value TLS_EXTRA_SANS "")}"
LEGACY_HTTP="${TLS_LEGACY_HTTP:-$(env_value TLS_LEGACY_HTTP true)}"
RENEW_DAYS="${TLS_RENEW_DAYS:-$(env_value TLS_RENEW_DAYS 30)}"
SERVER_DAYS="${TLS_SERVER_CERT_DAYS:-$(env_value TLS_SERVER_CERT_DAYS 825)}"
CA_DAYS="${TLS_CA_CERT_DAYS:-$(env_value TLS_CA_CERT_DAYS 3650)}"

case "$SERVER_NAME" in
    ""|*[!A-Za-z0-9._-]*)
        echo "ОШИБКА: TLS_SERVER_NAME содержит недопустимые символы."; exit 2 ;;
esac

case "$RENEW_DAYS:$SERVER_DAYS:$CA_DAYS" in
    *[!0-9:]*|"") echo "ОШИБКА: сроки TLS должны быть целыми числами."; exit 2 ;;
esac

command -v openssl >/dev/null 2>&1 || {
    echo "ОШИБКА: openssl не найден. Установите пакет openssl."; exit 2;
}

mkdir -p "$TLS_DIR"
chmod 700 "$TLS_DIR"

CA_KEY="$TLS_DIR/ca.key"
CA_CERT="$TLS_DIR/ca.crt"
SERVER_KEY="$TLS_DIR/server.key"
SERVER_CERT="$TLS_DIR/server.crt"
SANS_FILE="$TLS_DIR/sans.txt"
POLICY_FILE="$TLS_DIR/http-policy.conf"

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/ds-tls.XXXXXX")"
trap 'rm -rf "$tmp_dir"' EXIT

add_san() {
    local item="$1"
    [ -n "$item" ] || return 0
    case "$item" in
        DNS:*)
            case "${item#DNS:}" in
                ""|*[!A-Za-z0-9._-]*) echo "ОШИБКА: некорректный DNS SAN: $item" >&2; return 1 ;;
            esac
            printf '%s\n' "$item"
            ;;
        IP:*)
            case "${item#IP:}" in
                ""|*[!0-9.]*) echo "ОШИБКА: поддерживаются только IPv4 SAN: $item" >&2; return 1 ;;
            esac
            printf '%s\n' "$item"
            ;;
        *[!0-9.]* )
            case "$item" in
                *[!A-Za-z0-9._-]*) echo "ОШИБКА: некорректный DNS SAN: $item" >&2; return 1 ;;
            esac
            printf 'DNS:%s\n' "$item"
            ;;
        * ) printf 'IP:%s\n' "$item" ;;
    esac
}

key_matches_cert() {
    local key_file="$1" cert_file="$2"
    local key_hash cert_hash
    key_hash="$(openssl pkey -in "$key_file" -pubout 2>/dev/null | openssl sha256)"
    cert_hash="$(openssl x509 -in "$cert_file" -pubkey -noout 2>/dev/null | openssl sha256)"
    [ -n "$key_hash" ] && [ "$key_hash" = "$cert_hash" ]
}

{
    add_san "$SERVER_NAME"
    add_san "localhost"
    add_san "nginx"
    add_san "127.0.0.1"
    host_ips="$(hostname -I 2>/dev/null || true)"
    for ip_addr in $host_ips; do
        case "$ip_addr" in *:*) ;; *) add_san "$ip_addr" ;; esac
    done
    old_ifs="$IFS"; IFS=','
    for extra in $EXTRA_SANS; do
        extra="$(printf '%s' "$extra" | tr -d '[:space:]')"
        add_san "$extra"
    done
    IFS="$old_ifs"
} | sort -u > "$tmp_dir/sans.txt"

write_policy() {
    case "$(printf '%s' "$LEGACY_HTTP" | tr '[:upper:]' '[:lower:]')" in
        true|1|yes|on) cp "$ROOT_DIR/tls/http-compat.conf" "$tmp_dir/http-policy.conf" ;;
        false|0|no|off) cp "$ROOT_DIR/tls/http-enforce.conf" "$tmp_dir/http-policy.conf" ;;
        *) echo "ОШИБКА: TLS_LEGACY_HTTP должен быть true или false."; exit 2 ;;
    esac
    mv "$tmp_dir/http-policy.conf" "$POLICY_FILE"
    chmod 644 "$POLICY_FILE"
}

create_ca() {
    echo "• Создаю локальный удостоверяющий центр Digital Signage…"
    openssl req -x509 -newkey rsa:3072 -sha256 -nodes \
        -days "$CA_DAYS" \
        -subj "/CN=Digital Signage Local CA" \
        -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
        -addext "keyUsage=critical,keyCertSign,cRLSign" \
        -keyout "$tmp_dir/ca.key" -out "$tmp_dir/ca.crt" >/dev/null 2>&1
    mv "$tmp_dir/ca.key" "$CA_KEY"
    mv "$tmp_dir/ca.crt" "$CA_CERT"
    chmod 600 "$CA_KEY"
    chmod 644 "$CA_CERT"
}

create_server_cert() {
    echo "• Выпускаю TLS-сертификат для ${SERVER_NAME}…"
    {
        echo "[req]"
        echo "prompt = no"
        echo "distinguished_name = dn"
        echo "[dn]"
        echo "CN = $SERVER_NAME"
        echo "[server_ext]"
        echo "basicConstraints = critical,CA:FALSE"
        echo "keyUsage = critical,digitalSignature,keyEncipherment"
        echo "extendedKeyUsage = serverAuth"
        echo "subjectAltName = @alt_names"
        echo "[alt_names]"
        dns_n=0; ip_n=0
        while IFS= read -r san; do
            case "$san" in
                DNS:*) dns_n=$((dns_n + 1)); echo "DNS.$dns_n = ${san#DNS:}" ;;
                IP:*) ip_n=$((ip_n + 1)); echo "IP.$ip_n = ${san#IP:}" ;;
            esac
        done < "$tmp_dir/sans.txt"
    } > "$tmp_dir/server.cnf"

    openssl req -new -newkey rsa:2048 -nodes \
        -keyout "$tmp_dir/server.key" -out "$tmp_dir/server.csr" \
        -config "$tmp_dir/server.cnf" >/dev/null 2>&1
    openssl x509 -req -sha256 -days "$SERVER_DAYS" \
        -in "$tmp_dir/server.csr" \
        -CA "$CA_CERT" -CAkey "$CA_KEY" -CAcreateserial \
        -extfile "$tmp_dir/server.cnf" -extensions server_ext \
        -out "$tmp_dir/server.crt" >/dev/null 2>&1
    mv "$tmp_dir/server.key" "$SERVER_KEY"
    mv "$tmp_dir/server.crt" "$SERVER_CERT"
    cp "$tmp_dir/sans.txt" "$SANS_FILE"
    chmod 600 "$SERVER_KEY"
    chmod 644 "$SERVER_CERT" "$SANS_FILE"
    rm -f "$TLS_DIR/ca.srl"
}

check_tls() {
    local seconds=$((RENEW_DAYS * 86400))
    for path in "$CA_KEY" "$CA_CERT" "$SERVER_KEY" "$SERVER_CERT" "$SANS_FILE" "$POLICY_FILE"; do
        [ -s "$path" ] || { echo "ОШИБКА: отсутствует $path"; return 1; }
    done
    openssl verify -CAfile "$CA_CERT" "$SERVER_CERT" >/dev/null
    key_matches_cert "$CA_KEY" "$CA_CERT"
    key_matches_cert "$SERVER_KEY" "$SERVER_CERT"
    openssl x509 -checkend "$seconds" -noout -in "$SERVER_CERT" >/dev/null
    cmp -s "$tmp_dir/sans.txt" "$SANS_FILE"
    cert_end="$(openssl x509 -enddate -noout -in "$SERVER_CERT" | cut -d= -f2-)"
    echo "✓ TLS исправен; сертификат сервера действует до $cert_end"
}

case "$ACTION" in
    ensure)
        if [ ! -s "$CA_KEY" ] || [ ! -s "$CA_CERT" ]; then
            create_ca
        elif ! openssl x509 -checkend 31536000 -noout -in "$CA_CERT" >/dev/null; then
            echo "ОШИБКА: срок CA меньше года. Не меняю корень доверия автоматически."
            echo "Сохраните ca.key/ca.crt и выполните контролируемую ротацию агентов."
            exit 1
        fi

        renew=0
        [ -s "$SERVER_KEY" ] && [ -s "$SERVER_CERT" ] && [ -s "$SANS_FILE" ] || renew=1
        [ "$renew" -eq 1 ] || openssl verify -CAfile "$CA_CERT" "$SERVER_CERT" >/dev/null 2>&1 || renew=1
        [ "$renew" -eq 1 ] || key_matches_cert "$SERVER_KEY" "$SERVER_CERT" || renew=1
        [ "$renew" -eq 1 ] || openssl x509 -checkend "$((RENEW_DAYS * 86400))" -noout -in "$SERVER_CERT" >/dev/null 2>&1 || renew=1
        [ "$renew" -eq 1 ] || cmp -s "$tmp_dir/sans.txt" "$SANS_FILE" || renew=1
        [ "$renew" -eq 0 ] || create_server_cert
        write_policy
        check_tls
        ;;
    check)
        check_tls
        ;;
    *)
        echo "Использование: bash tls/manage_tls.sh [ensure|check]"
        exit 2
        ;;
esac
