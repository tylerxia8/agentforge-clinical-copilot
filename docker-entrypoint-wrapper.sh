#!/bin/sh
set -e

# If MYSQL_HOST is set and the database exists but has partial schema
# from a previous failed setup, drop and recreate it so
# auto_configure.php can run cleanly.
if [ -n "$MYSQL_HOST" ] && [ -n "$MYSQL_ROOT_PASS" ] && [ -n "$MYSQL_DATABASE" ]; then
  echo "[entrypoint-wrapper] Checking for stale partial schema..."
  # Wait for MySQL to be reachable (up to 60s)
  for i in $(seq 1 12); do
    if mysql -h"$MYSQL_HOST" -P"${MYSQL_PORT:-3306}" -u"${MYSQL_ROOT_USER:-root}" -p"$MYSQL_ROOT_PASS" -e "SELECT 1" >/dev/null 2>&1; then
      break
    fi
    echo "[entrypoint-wrapper] Waiting for MySQL... ($i/12)"
    sleep 5
  done

  # Check if the database has the marker table (meaning setup started
  # but never completed successfully)
  TABLE_EXISTS=$(mysql -h"$MYSQL_HOST" -P"${MYSQL_PORT:-3306}" -u"${MYSQL_ROOT_USER:-root}" -p"$MYSQL_ROOT_PASS" \
    -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$MYSQL_DATABASE' AND table_name='ccda_field_mapping'" 2>/dev/null || echo "0")

  if [ "$TABLE_EXISTS" = "1" ]; then
    echo "[entrypoint-wrapper] Found stale partial schema — dropping and recreating database '$MYSQL_DATABASE'."
    mysql -h"$MYSQL_HOST" -P"${MYSQL_PORT:-3306}" -u"${MYSQL_ROOT_USER:-root}" -p"$MYSQL_ROOT_PASS" \
      -e "DROP DATABASE IF EXISTS \`$MYSQL_DATABASE\`; CREATE DATABASE \`$MYSQL_DATABASE\`;"
    echo "[entrypoint-wrapper] Database reset complete."
  else
    echo "[entrypoint-wrapper] No stale schema detected — proceeding normally."
  fi
fi

# Delegate to the stock OpenEMR entrypoint
exec /var/www/localhost/htdocs/openemr/openemr.sh "$@"
