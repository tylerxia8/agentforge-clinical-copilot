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

  # A complete OpenEMR setup populates the `version` table as one of its
  # final steps.  If the DB has tables but `version` is empty or missing,
  # the previous setup was partial and we need to reset.
  TABLE_COUNT=$(mysql -h"$MYSQL_HOST" -P"${MYSQL_PORT:-3306}" -u"${MYSQL_ROOT_USER:-root}" -p"$MYSQL_ROOT_PASS" \
    -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$MYSQL_DATABASE'" 2>/dev/null || echo "0")

  VERSION_ROWS=$(mysql -h"$MYSQL_HOST" -P"${MYSQL_PORT:-3306}" -u"${MYSQL_ROOT_USER:-root}" -p"$MYSQL_ROOT_PASS" \
    -N -e "SELECT COUNT(*) FROM \`$MYSQL_DATABASE\`.version" 2>/dev/null || echo "0")

  if [ "$TABLE_COUNT" -gt "0" ] && [ "$VERSION_ROWS" = "0" ]; then
    echo "[entrypoint-wrapper] Found partial schema ($TABLE_COUNT tables, no version rows) — dropping and recreating database '$MYSQL_DATABASE'."
    mysql -h"$MYSQL_HOST" -P"${MYSQL_PORT:-3306}" -u"${MYSQL_ROOT_USER:-root}" -p"$MYSQL_ROOT_PASS" \
      -e "DROP DATABASE IF EXISTS \`$MYSQL_DATABASE\`; CREATE DATABASE \`$MYSQL_DATABASE\`;"
    echo "[entrypoint-wrapper] Database reset complete."
  else
    echo "[entrypoint-wrapper] No stale schema detected — proceeding normally."
  fi
fi

# Delegate to the stock OpenEMR entrypoint
exec /var/www/localhost/htdocs/openemr/openemr.sh "$@"
