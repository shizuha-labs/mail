#!/bin/bash
set -e

# Wait for database to be ready
DB_HOST="${POSTGRES_HOST:-shizuha-db}"
DB_PORT="${POSTGRES_PORT:-5432}"
echo "Waiting for database at $DB_HOST:$DB_PORT..."
while ! python -c 'import socket,sys; socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=1).close()' "$DB_HOST" "$DB_PORT" 2>/dev/null; do
  sleep 0.1
done
echo "Database started"

# Create database if it doesn't exist using Django management command
echo "Creating database if it doesn't exist..."
python manage.py createdb

# Create migrations for any model changes (dev mode with volume mounts)
echo "Creating migrations if needed..."
python manage.py makemigrations --noinput

# Run migrations
echo "Running migrations..."
python manage.py migrate --noinput

# Administrator accounts are explicit operator actions. To create a local
# Django administrator, run manage.py createsuperuser interactively.

# Collect static files for nginx to serve
echo "Collecting static files..."
python manage.py collectstatic --noinput

echo "Setup complete. Starting services with supervisord..."

# Execute the main command (supervisord)
exec "$@"
