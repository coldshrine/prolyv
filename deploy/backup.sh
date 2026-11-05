#!/usr/bin/env bash
# Nightly SQLite backup using the online backup API (safe while the app is running in WAL mode).
# Keeps PROLYV_BACKUP_KEEP_DAYS (default 14) days of gzipped copies.
#
# Cron (as the ubuntu user, `crontab -e`):
#   0 3 * * * /home/ubuntu/prolyv/deploy/backup.sh >> /var/lib/prolyv/backups/backup.log 2>&1
#
# Restore: gunzip -k prolyv-YYYYMMDD-HHMMSS.db.gz, stop the service, replace /var/lib/prolyv/prolyv.db, start.

set -euo pipefail

DB="${PROLYV_DB:-/var/lib/prolyv/prolyv.db}"
DIR="${PROLYV_BACKUP_DIR:-/var/lib/prolyv/backups}"
KEEP_DAYS="${PROLYV_BACKUP_KEEP_DAYS:-14}"

command -v sqlite3 >/dev/null || { echo "sqlite3 is not installed (sudo apt install sqlite3)"; exit 1; }
[ -f "$DB" ] || { echo "database not found: $DB"; exit 1; }

mkdir -p "$DIR"
stamp="$(date +%Y%m%d-%H%M%S)"
out="$DIR/prolyv-$stamp.db"

sqlite3 "$DB" ".backup '$out'"
sqlite3 "$out" "PRAGMA integrity_check;" | grep -qx ok || { echo "integrity check failed for $out"; exit 1; }
gzip -f "$out"

find "$DIR" -name 'prolyv-*.db.gz' -mtime +"$KEEP_DAYS" -delete
echo "$(date -Is) backup written: $out.gz"
