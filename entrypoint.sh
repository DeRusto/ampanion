#!/bin/sh
# Own /config as PUID:PGID, then drop root. Skipped when started with --user.
set -e
if [ "$(id -u)" = 0 ]; then
  PUID="${PUID:-1000}" PGID="${PGID:-1000}"
  mkdir -p "$AMPANION_CONFIG"
  chown -R "$PUID:$PGID" "$AMPANION_CONFIG"
  exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups env HOME="$AMPANION_CONFIG" "$@"
fi
exec "$@"
