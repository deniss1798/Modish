#!/bin/bash
# Первый деплой Modish на чистый VPS (Ubuntu/Debian).
# Запуск на сервере: bash setup-vps.sh
set -euo pipefail

MODISH_DIR="${MODISH_DIR:-/opt/modish}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$(openssl rand -hex 16)}"
JWT_SECRET="${JWT_SECRET:-$(openssl rand -hex 32)}"

echo "==> Modish setup in $MODISH_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "==> Installing Docker..."
  curl -fsSL https://get.docker.com | sh
  systemctl enable docker
  systemctl start docker
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin required. Install docker-compose-plugin."
  exit 1
fi

mkdir -p "$MODISH_DIR"

if [ ! -f "$MODISH_DIR/backend/app/main.py" ]; then
  echo ""
  echo "!!! Проект ещё не на сервере."
  echo "С вашего ПК (Windows PowerShell) загрузите файлы:"
  echo ""
  echo "  scp -r C:\\Users\\User\\Desktop\\modish\\backend root@194.87.118.236:$MODISH_DIR/"
  echo "  scp -r C:\\Users\\User\\Desktop\\modish\\deploy root@194.87.118.236:$MODISH_DIR/"
  echo ""
  echo "Или клонируйте git-репозиторий в $MODISH_DIR"
  echo "После загрузки запустите этот скрипт снова."
  exit 1
fi

ENV_FILE="$MODISH_DIR/backend/.env"
if [ ! -f "$ENV_FILE" ]; then
  echo "==> Creating backend/.env"
  cat > "$ENV_FILE" <<EOF
DATABASE_URL=postgresql+psycopg://modish:${POSTGRES_PASSWORD}@db:5432/modish
JWT_SECRET=${JWT_SECRET}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
RATE_LIMIT_PER_MINUTE=120
EOF
  echo "Saved secrets to $ENV_FILE"
else
  echo "==> Using existing $ENV_FILE"
  # Ensure POSTGRES_PASSWORD is in .env for compose
  if ! grep -q '^POSTGRES_PASSWORD=' "$ENV_FILE"; then
    echo "POSTGRES_PASSWORD=${POSTGRES_PASSWORD}" >> "$ENV_FILE"
  fi
fi

# Export for docker compose variable substitution
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

echo "==> Building and starting containers..."
cd "$MODISH_DIR"
docker compose -f deploy/docker-compose.prod.yml up -d --build

echo "==> Waiting for API..."
for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1/health" >/dev/null 2>&1; then
    echo "API is up!"
    curl -s "http://127.0.0.1/health"
    echo ""
    break
  fi
  sleep 2
done

echo ""
echo "==> Done. Проверка снаружи:"
echo "  curl http://194.87.118.236/health"
echo ""
echo "==> Импорт каталога (после настройки ADMIN_CATALOG_TOKEN в .env):"
echo "  curl -X POST http://127.0.0.1/admin/catalog/alpha-bootstrap -H \"Authorization: Bearer YOUR_ADMIN_TOKEN\""
echo ""
