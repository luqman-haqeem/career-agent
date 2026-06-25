#!/bin/sh
# Runs as root: fix volume ownership, then drop to the non-root appuser.
set -e

# Named volumes mount root-owned; hand them to appuser. Bind-mounted app data
# is already UID 1000, so this is a no-op there.
chown -R appuser:appuser /home/appuser/.local/share/opencode 2>/dev/null || true
chown appuser:appuser /app/memory /app/resumes /app/data 2>/dev/null || true

if [ -z "$OPENROUTER_API_KEY" ]; then
    echo "WARNING: OPENROUTER_API_KEY is not set — OpenCode cannot authenticate." >&2
fi

exec gosu appuser "$@"
