#!/bin/sh
# Runs the app as PUID:PGID (TrueNAS "apps" = 568) so moved files keep the right owner.
set -e
PUID="${PUID:-568}"
PGID="${PGID:-568}"
mkdir -p "${CONFIG_DIR:-/config}" "${TRASH_DIR:-/trash}"
chown "$PUID:$PGID" "${CONFIG_DIR:-/config}" "${TRASH_DIR:-/trash}" 2>/dev/null || true
if [ "$(id -u)" = "0" ] && [ "$PUID" != "0" ]; then
  exec setpriv --reuid="$PUID" --regid="$PGID" --groups="$PGID" --inh-caps=-all python -m doctor
fi
exec python -m doctor
