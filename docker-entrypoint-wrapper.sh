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
  elif [ "$TABLE_COUNT" -gt "0" ] && [ "$VERSION_ROWS" -gt "0" ]; then
    # Complete schema → setup must not run a second time (auto_configure.php
    # uses CREATE TABLE, not CREATE TABLE IF NOT EXISTS, so a re-run crashes).
    # Don't delete the file — the stock openemr.sh entrypoint shells out to
    # it and will crash-loop if it's missing. Replace with a no-op stub
    # instead so the include succeeds and immediately exits clean.
    echo "[entrypoint-wrapper] Complete schema detected ($TABLE_COUNT tables, $VERSION_ROWS version rows) — neutralising auto_configure.php."
    # The stock openemr.sh greps this file's output for a success marker,
    # not just exit code, so we MUST echo "OpenEMR configured." (the same
    # string the real auto_configure.php prints on success).
    cat > /var/www/localhost/htdocs/openemr/auto_configure.php <<'PHP'
<?php
// Replaced by docker-entrypoint-wrapper.sh: schema already complete,
// so setup must not re-run (auto_configure.php is non-idempotent).
echo "OpenEMR configured.\n";
exit(0);
PHP
  else
    echo "[entrypoint-wrapper] Empty database — proceeding with fresh setup."
  fi
fi

# Ensure the entrypoint is invokable. Some runtime hooks in the image
# strip the execute bit on /var/www files when fixing ownership; using
# `sh` avoids depending on it.
exec sh /var/www/localhost/htdocs/openemr/openemr.sh "$@"
