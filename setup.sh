#!/bin/bash

set -e

echo "===== Setup ====="

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    uv venv
else
    echo "Virtual environment already exists."
fi

echo "Installing dependencies..."
uv sync

echo "Installing Playwright Chromium..."
uv run playwright install chromium

echo "Setting up database..."
set -a
[ -f .env ] && source .env
set +a
DB_URL="${DATABASE_URL:-postgresql+psycopg://souq_ai:souq_ai@localhost:5432/souq_ai}"

if [[ "$DB_URL" == postgresql* ]]; then
    eval "$(uv run python - "$DB_URL" <<'PYEOF'
import sys
from urllib.parse import urlsplit

u = urlsplit(sys.argv[1].split("+", 1)[0] + "://" + sys.argv[1].split("://", 1)[1])
print(f"DB_USER={u.username}")
print(f"DB_PASS={u.password}")
print(f"DB_HOST={u.hostname}")
print(f"DB_PORT={u.port or 5432}")
print(f"DB_NAME={u.path.lstrip('/')}")
PYEOF
)"

    if command -v psql >/dev/null 2>&1; then
        PGPASSWORD="" psql -h "$DB_HOST" -p "$DB_PORT" -U "$(whoami)" -d postgres -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$DB_USER') THEN
      CREATE ROLE $DB_USER LOGIN PASSWORD '$DB_PASS';
   END IF;
END
\$\$;
SELECT 'CREATE DATABASE $DB_NAME OWNER $DB_USER'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$DB_NAME')\gexec
SQL
    else
        echo "psql not found on PATH; skipping automatic role/database creation."
        echo "Ensure database '$DB_NAME' and role '$DB_USER' exist before running the app."
    fi
fi

echo "Creating database tables..."
uv run python -c "from db import init_db; init_db()"

