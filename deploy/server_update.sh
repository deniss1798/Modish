#!/bin/bash
# Обновление backend Modish на VPS (полная синхронизация кода).
# Запускается автоматически скриптом update_server.bat (не вручную).
# Сервер: /opt/Modish, контейнеры modish_backend / modish_postgres / modish_redis,
# стек управляется корневым docker-compose.yml (проверено осмотром 01.07.2026).
set -e

ROOT=/opt/Modish
if [ ! -d "$ROOT/backend/app" ]; then
  echo "ERROR: $ROOT/backend/app не найден — структура сервера изменилась, остановка."
  exit 1
fi

SRC=/tmp/modish_update/backend
if [ ! -d "$SRC/app" ]; then
  echo "ERROR: файлы обновления не загружены в $SRC"
  exit 1
fi

# 1) Полный бэкап текущего кода backend (без .env он и так остаётся на месте)
STAMP=$(date +%Y%m%d_%H%M%S)
BK=/root/modish_backup_$STAMP
mkdir -p "$BK"
cp -a "$ROOT/backend/app" "$BK/app"
[ -d "$ROOT/backend/alembic" ] && cp -a "$ROOT/backend/alembic" "$BK/alembic"
[ -d "$ROOT/backend/scripts" ] && cp -a "$ROOT/backend/scripts" "$BK/scripts"
[ -d "$ROOT/backend/tests" ] && cp -a "$ROOT/backend/tests" "$BK/tests"
[ -f "$ROOT/backend/requirements.txt" ] && cp -a "$ROOT/backend/requirements.txt" "$BK/"
[ -f "$ROOT/backend/Dockerfile" ] && cp -a "$ROOT/backend/Dockerfile" "$BK/"
echo "Бэкап кода: $BK"

# 2) Синхронизация кода (.env на сервере НЕ трогаем — его нет в загрузке)
cp -a "$SRC/." "$ROOT/backend/"
echo "Код backend обновлён"

# 2.1) Windows-переносы строк ломают shell-скрипты в контейнере — чиним
find "$ROOT/backend" -name '*.sh' -exec sed -i 's/\r$//' {} + 2>/dev/null || true
chmod +x "$ROOT/backend/scripts/"*.sh 2>/dev/null || true
if grep -q $'\r' "$ROOT/backend/scripts/entrypoint.sh"; then
  echo "ERROR: в entrypoint.sh остались Windows-переносы — остановка."
  exit 1
fi
echo "Переносы строк в *.sh исправлены (проверено)"

# 3) Пересборка backend через корневой docker-compose
cd "$ROOT"
echo "Пересобираю backend-контейнер (2-4 минуты)..."
docker compose up -d --build

# 4) Ждём API
echo "Жду запуска API..."
for i in $(seq 1 45); do
  sleep 2
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo "API отвечает: $(curl -fsS http://127.0.0.1:8000/health)"
    break
  fi
  if [ "$i" = "45" ]; then
    echo "ERROR: API не ответил за 90 секунд. Логи:"
    docker logs modish_backend --tail 40 || true
    echo ""
    echo "Откатить код можно так:"
    echo "  cp -a $BK/app $ROOT/backend/ && cd $ROOT && docker compose up -d --build"
    exit 1
  fi
done

# 5) Миграции БД (если entrypoint не применил сам — команда безвредна)
docker exec modish_backend alembic upgrade head 2>/dev/null \
  && echo "Миграции БД применены" \
  || echo "Миграции: пропущено (уже применены при старте или alembic недоступен)"

# 6) Пересчёт каталога: style-теги, цвета, категории для существующих товаров
TOKEN=$(grep -E '^ADMIN_TOKEN=' "$ROOT/backend/.env" | head -1 | cut -d= -f2- | tr -d '\r')
if [ -z "$TOKEN" ]; then
  echo "WARNING: ADMIN_TOKEN не найден в backend/.env — пересчёт каталога пропущен."
else
  echo "Пересчитываю каталог (может занять минуту)..."
  RESULT=$(curl -fsS --max-time 600 -X POST http://127.0.0.1:8000/admin/catalog/renormalize \
    -H "Authorization: Bearer $TOKEN" || echo FAILED)
  echo "Результат пересчёта: $RESULT"
  if [ "$RESULT" = "FAILED" ]; then
    echo "WARNING: пересчёт не прошёл — сообщите об этом в чате Claude."
  fi
fi

rm -rf /tmp/modish_update
echo ""
echo "======================================"
echo "ГОТОВО! Сервер обновлён."
echo "Бэкап старого кода: $BK"
echo "======================================"
