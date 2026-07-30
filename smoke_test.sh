#!/bin/bash
# smoke_test.sh — быстрый прогон ключевых сценариев ПЕРЕД каждым релизом.
# Запускать на сервере после `install_server.sh` / обновления.
# Проверяет: все контейнеры живы, вход, здоровье API, полный цикл
# «экран → медиа → плейлист → расписание», отчёты, биллинг, уведомления.
# Все тестовые данные создаются с префиксом SMOKE_ и удаляются в конце.
#
# Использование:   bash smoke_test.sh
# Переменные:      ADMIN_USER (по умолч. admin), ADMIN_PASS (admin123)
#
# Код возврата 0 — все проверки прошли; иначе число упавших проверок.
set -uo pipefail

ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASS="${ADMIN_PASS:-admin123}"
API="http://localhost:8000"           # внутри контейнера ds_api
DC="docker exec ds_api"               # выполняем запросы изнутри api-контейнера

PASS=0; FAIL=0
ok(){   echo "  ✓ $1"; PASS=$((PASS+1)); }
bad(){  echo "  ✗ $1"; FAIL=$((FAIL+1)); }
step(){ echo; echo "▶ $1"; }

# curl изнутри ds_api; печатает тело, код в глобальную HTTP
req(){ # req METHOD PATH [extra curl args...]
  local m="$1" p="$2"; shift 2
  RESP=$($DC curl -s -w $'\n%{http_code}' -X "$m" "$API$p" -H "Authorization: Bearer $TOKEN" "$@" 2>/dev/null)
  HTTP="${RESP##*$'\n'}"; BODY="${RESP%$'\n'*}"
}

echo "════════════════════════════════════════════"
echo " SMOKE TEST — Digital Signage $(date '+%Y-%m-%d %H:%M')"
echo "════════════════════════════════════════════"

# ── 1. Контейнеры ────────────────────────────────────────────────────────────
step "Контейнеры"
for c in ds_postgres ds_redis ds_minio ds_api ds_celery ds_nginx ds_ntp; do
  if docker ps --format '{{.Names}}' | grep -qx "$c"; then ok "$c запущен"; else bad "$c НЕ запущен"; fi
done

# ── 2. Здоровье API ──────────────────────────────────────────────────────────
step "API /health"
if $DC curl -sf "$API/health" >/dev/null 2>&1; then ok "/health отвечает"; else bad "/health не отвечает"; fi
if curl --cacert tls/generated/ca.crt -sf https://localhost/health >/dev/null 2>&1; then
  ok "HTTPS-цепочка и hostname проверены"
else
  bad "HTTPS nginx не прошёл проверку CA/hostname"
fi
HTTP_PANEL=$(curl -s -o /dev/null -w '%{http_code}' http://localhost/ 2>/dev/null)
[ "$HTTP_PANEL" = "308" ] \
  && ok "HTTP-панель перенаправляется на HTTPS" \
  || bad "HTTP-панель не перенаправлена: HTTP $HTTP_PANEL"

# ── 3. Вход ──────────────────────────────────────────────────────────────────
step "Аутентификация"
BACKUP_ANON_HTTP=$($DC curl -s -o /dev/null -w '%{http_code}' "$API/backups/1/download" 2>/dev/null)
[ "$BACKUP_ANON_HTTP" = "401" ] \
  && ok "скачивание бэкапа без входа запрещено" \
  || bad "бэкап доступен без входа: HTTP $BACKUP_ANON_HTTP"

TOKEN=$($DC curl -s -X POST "$API/token" -d "username=$ADMIN_USER&password=$ADMIN_PASS" \
        -H "Content-Type: application/x-www-form-urlencoded" 2>/dev/null \
        | python3 -c "import sys,json;print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null)
if [ -n "$TOKEN" ]; then ok "вход $ADMIN_USER — токен получен"; else
  bad "вход не удался"
  echo "Дальнейшие проверки невозможны без токена."
  echo "Если пароль admin был сменён (боевой сервер), передайте его так:"
  echo '  read -rsp "Пароль admin: " ADMIN_PASS; echo; ADMIN_PASS="$ADMIN_PASS" bash smoke_test.sh; unset ADMIN_PASS'
  exit $((FAIL))
fi
TOKEN_SV=$(printf '%s' "$TOKEN" | python3 -c "
import base64,json,sys
part=sys.stdin.read().split('.')[1]
part += '=' * (-len(part) % 4)
print(json.loads(base64.urlsafe_b64decode(part)).get('sv',''))
" 2>/dev/null)
[ -n "$TOKEN_SV" ] && ok "JWT содержит поколение отзыва сессии" \
                     || bad "JWT не содержит session_version"

# Живой Redis rate limit на заведомо несуществующей тестовой учётке. Адрес
# зарезервирован для документации, ключи и запись аудита удаляются сразу.
AUTH_PROBE="SMOKE_auth_$(date +%s)"
AUTH_PROBE_IP="198.51.100.77"
AUTH_LIMIT=$($DC sh -c 'printf %s "${AUTH_ACCOUNT_FAILURE_LIMIT:-5}"')
AUTH_CODES=""
for _ in $(seq 1 "$AUTH_LIMIT"); do
  AUTH_CODE=$($DC curl -s -o /dev/null -w '%{http_code}' -X POST "$API/token" \
    -H "X-Real-IP: $AUTH_PROBE_IP" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "username=$AUTH_PROBE&password=wrong-password" 2>/dev/null)
  AUTH_CODES="$AUTH_CODES $AUTH_CODE"
done
AUTH_KEYS=$($DC python -c "from auth_security import login_limiter; print(' '.join(login_limiter.keys_for('$AUTH_PROBE','$AUTH_PROBE_IP')))" 2>/dev/null)
[ -n "$AUTH_KEYS" ] && docker exec ds_redis redis-cli DEL $AUTH_KEYS >/dev/null 2>&1
PG_USER=$(docker exec ds_postgres printenv POSTGRES_USER 2>/dev/null)
PG_DB=$(docker exec ds_postgres printenv POSTGRES_DB 2>/dev/null)
docker exec ds_postgres psql -U "$PG_USER" -d "$PG_DB" -q \
  -c "DELETE FROM audit_log WHERE detail LIKE 'логин=$AUTH_PROBE;%';" >/dev/null 2>&1
AUTH_LAST="${AUTH_CODES##* }"
AUTH_PREV="${AUTH_CODES% *}"
if [ "$AUTH_LAST" = "429" ] && ! printf '%s' "$AUTH_PREV" | grep -qvE '^( 401)+$'; then
  ok "Redis ограничивает перебор входа (HTTP 429)"
else
  bad "rate limit входа не сработал: HTTP$AUTH_CODES"
fi

# ── 4. Полный цикл: экран → медиа → плейлист → расписание ────────────────────
step "Основной цикл эфира"
STAMP=$(date +%s)
# экран
req POST "/minipc/register?name=SMOKE_scr_$STAMP"
SID=$(echo "$BODY" | python3 -c "import sys,json;print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
DEVICE_TOKEN=$(echo "$BODY" | python3 -c "import sys,json;print(json.load(sys.stdin).get('token',''))" 2>/dev/null)
[ -n "$SID" ] && ok "экран создан (id=$SID)" || bad "экран не создан"
if [ -n "$SID" ] && [ -n "$DEVICE_TOKEN" ] && \
   docker exec \
     -e DS_SMOKE_SCREEN_ID="$SID" \
     -e DS_SMOKE_DEVICE_TOKEN="$DEVICE_TOKEN" \
     ds_api python /app/smoke_wss.py >/dev/null 2>&1; then
  ok "агентский WSS handshake с проверкой CA"
else
  bad "агентский WSS handshake не прошёл"
fi
# медиа (генерим тестовый ролик ffmpeg внутри контейнера)
$DC sh -c "ffmpeg -f lavfi -i testsrc=duration=1:size=160x120:rate=5 -y /tmp/smoke.mp4 -loglevel quiet" 2>/dev/null
req POST "/media/upload?title=SMOKE_media_$STAMP&category=service" -F "file=@/tmp/smoke.mp4"
MID=$(echo "$BODY" | python3 -c "import sys,json;print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
[ -n "$MID" ] && ok "медиа загружено (id=$MID)" || bad "загрузка медиа: HTTP $HTTP"
# реальная докачка: endpoint обязан вернуть только запрошенные 16 байт и HTTP 206
if [ -n "$MID" ]; then
  RANGE_RESULT=$($DC curl -s -o /tmp/smoke-range.bin -w '%{http_code} %{size_download}' \
    -H "Authorization: Bearer $TOKEN" -H "Range: bytes=0-15" \
    "$API/files/download/$MID" 2>/dev/null)
  [ "$RANGE_RESULT" = "206 16" ] \
    && ok "докачка медиа по Range (HTTP 206, 16 байт)" \
    || bad "докачка медиа повреждена: $RANGE_RESULT"
fi
# плейлист
req POST "/playlists?name=SMOKE_pl_$STAMP"
PID=$(echo "$BODY" | python3 -c "import sys,json;print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
[ -n "$PID" ] && ok "плейлист создан (id=$PID)" || bad "плейлист не создан"
# ролик в плейлист
if [ -n "$PID" ] && [ -n "$MID" ]; then
  req POST "/playlists/$PID/items?media_id=$MID"
  [ "$HTTP" = "200" ] && ok "ролик добавлен в плейлист" || bad "добавление в плейлист: HTTP $HTTP"
fi
# слот расписания (экран, Пн 10:00)
if [ -n "$SID" ] && [ -n "$PID" ]; then
  req POST "/schedule?screen_id=$SID&day_of_week=0&hour=10&playlist_id=$PID"
  [ "$HTTP" = "200" ] && ok "слот расписания назначен" || bad "назначение слота: HTTP $HTTP"
fi

# ── 5. Отчёты / биллинг / уведомления доступны ───────────────────────────────
step "Модули доступны"
req GET "/dashboard";              [ "$HTTP" = "200" ] && ok "дашборд" || bad "дашборд: HTTP $HTTP"
req GET "/reports/inventory";      [ "$HTTP" = "200" ] && ok "отчёт занятости" || bad "отчёт: HTTP $HTTP"
req GET "/billing/invoices";       [ "$HTTP" = "200" ] && ok "биллинг" || bad "биллинг: HTTP $HTTP"
req GET "/campaigns";              [ "$HTTP" = "200" ] && ok "кампании" || bad "кампании: HTTP $HTTP"
req GET "/moderation/pending";     [ "$HTTP" = "200" ] && ok "модерация" || bad "модерация: HTTP $HTTP"
req GET "/notifications/settings"; [ "$HTTP" = "200" ] && ok "настройки уведомлений" || bad "уведомления: HTTP $HTTP"
req GET "/system/selfcheck"
SELF_AUTH_OK=$(echo "$BODY" | python3 -c "import sys,json;c=json.load(sys.stdin).get('checks',[]);print('1' if any(x.get('id')=='auth_security' and x.get('status')=='ok' for x in c) else '')" 2>/dev/null)
SELF_TLS_OK=$(echo "$BODY" | python3 -c "import sys,json;c=json.load(sys.stdin).get('checks',[]);print('1' if any(x.get('id')=='tls' and x.get('status')=='ok' for x in c) else '')" 2>/dev/null)
[ "$HTTP" = "200" ] && [ "$SELF_AUTH_OK" = "1" ] && [ "$SELF_TLS_OK" = "1" ] \
  && ok "самодиагностика: TLS, защита входа и сессий" \
  || bad "самодиагностика авторизации: HTTP $HTTP"
req GET "/media/fillers";          [ "$HTTP" = "200" ] && ok "папка заглушек" || bad "заглушки: HTTP $HTTP"
req GET "/media/common";           [ "$HTTP" = "200" ] && ok "общая медиатека" || bad "общая медиатека: HTTP $HTTP"
req GET "/media/folders-all";      [ "$HTTP" = "200" ] && ok "список папок" || bad "папки: HTTP $HTTP"
req GET "/backups";                [ "$HTTP" = "200" ] && ok "бэкапы доступны администратору" || bad "бэкапы: HTTP $HTTP"
req GET "/reports/fillers";        [ "$HTTP" = "200" ] && ok "отчёт по заглушкам" || bad "отчёт заглушек: HTTP $HTTP"
[ -n "${SID:-}" ] && { req GET "/minipc/$SID/diagnostics"; [ "$HTTP" = "200" ] && ok "диагностика экрана (список)" || bad "диагностика: HTTP $HTTP"; }

# ── 6. Индивидуальные условия кампании ──────────────────────────────────────
step "Финансовые условия кампании"
req POST "/advertisers" -H "Content-Type: application/json" \
  -d "{\"name\":\"SMOKE_adv_$STAMP\"}"
AID=$(echo "$BODY" | python3 -c "import sys,json;print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
[ -n "$AID" ] && ok "рекламодатель для кампании создан (id=$AID)" || bad "рекламодатель не создан: HTTP $HTTP"

if [ -n "$AID" ]; then
  req GET "/advertisers/$AID/folders"
  FOLDERS_OK=$(echo "$BODY" | python3 -c "import sys,json;names={x.get('name') for x in json.load(sys.stdin)};print('1' if {'Видеореклама','Документы'} <= names else '')" 2>/dev/null)
  [ "$HTTP" = "200" ] && [ "$FOLDERS_OK" = "1" ] \
    && ok "две стандартные папки созданы автоматически" \
    || bad "стандартные папки рекламодателя не созданы"

  RENAMED_ADV="SMOKE_adv_renamed_$STAMP"
  req PATCH "/advertisers/$AID" -H "Content-Type: application/json" \
    -d "{\"name\":\"$RENAMED_ADV\"}"
  RENAMED_OK=$(echo "$BODY" | python3 -c "import sys,json;print('1' if json.load(sys.stdin).get('name')=='$RENAMED_ADV' else '')" 2>/dev/null)
  [ "$HTTP" = "200" ] && [ "$RENAMED_OK" = "1" ] \
    && ok "имя рекламодателя изменено без смены id" \
    || bad "имя рекламодателя не изменилось: HTTP $HTTP"

  req POST "/campaigns" -H "Content-Type: application/json" -d "{
    \"advertiser_id\":$AID,
    \"name\":\"SMOKE_campaign_$STAMP\",
    \"date_from\":\"$(date '+%Y-%m-%d')\",
    \"date_to\":\"$(date '+%Y-%m-%d')\",
    \"target_plays_per_day\":10,
    \"billing_mode\":\"per_play\",
    \"unit_price\":2.50,
    \"discount_amount\":5.00,
    \"discount_note\":\"Smoke-проверка\"
  }"
  CID=$(echo "$BODY" | python3 -c "import sys,json;print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
  [ -n "$CID" ] && ok "кампания с индивидуальными условиями создана (id=$CID)" || bad "кампания не создана: HTTP $HTTP"
fi

if [ -n "${CID:-}" ]; then
  req GET "/campaigns/$CID"
  FIN_OK=$(echo "$BODY" | python3 -c "import sys,json;f=json.load(sys.stdin).get('financial',{});print('1' if f.get('billing_mode')=='per_play' and f.get('unit_price')==2.5 and f.get('discount_amount')==5.0 else '')" 2>/dev/null)
  [ "$HTTP" = "200" ] && [ "$FIN_OK" = "1" ] \
    && ok "снимок тарифа, цены и скидки читается" \
    || bad "финансовые условия кампании повреждены"
fi

# ── 7. Уборка тестовых данных ────────────────────────────────────────────────
step "Уборка"
[ -n "${CID:-}" ] && req DELETE "/campaigns/$CID" && ok "кампания удалена"
[ -n "${AID:-}" ] && req DELETE "/advertisers/$AID" && ok "рекламодатель удалён"
[ -n "${PID:-}" ] && req DELETE "/playlists/$PID" && ok "плейлист удалён"
[ -n "${MID:-}" ] && req DELETE "/media/$MID"      && ok "медиа удалено"
[ -n "${SID:-}" ] && req DELETE "/minipc/$SID"     && ok "экран удалён"
$DC rm -f /tmp/smoke.mp4 /tmp/smoke-range.bin 2>/dev/null

# ── Итог ─────────────────────────────────────────────────────────────────────
echo
echo "════════════════════════════════════════════"
echo " ИТОГ: пройдено $PASS, провалено $FAIL"
echo "════════════════════════════════════════════"
[ "$FAIL" -eq 0 ] && echo "✅ Все проверки прошли — релиз можно выпускать." \
                  || echo "❌ Есть провалы — НЕ выпускать до устранения."
exit "$FAIL"
