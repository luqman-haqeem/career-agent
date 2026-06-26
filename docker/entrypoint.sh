#!/bin/sh
# Runs as root: fix volume ownership, then drop to the non-root appuser.
set -e

# Mounting the named volume at .local/share/opencode makes Docker create the
# intermediate .local and .local/share as root, which then blocks appuser from
# creating sibling XDG dirs (OpenCode needs .local/state). Ensure the home dirs
# exist and are appuser-owned. Bind-mounted app data is already UID 1000.
mkdir -p /home/appuser/.local/state /home/appuser/.local/share/opencode /home/appuser/.cache
chown appuser:appuser /home/appuser/.local /home/appuser/.local/state \
    /home/appuser/.local/share /home/appuser/.cache 2>/dev/null || true
chown -R appuser:appuser /home/appuser/.local/share/opencode 2>/dev/null || true
chown appuser:appuser /app/memory /app/resumes /app/data 2>/dev/null || true

if [ -z "$OPENROUTER_API_KEY" ]; then
    echo "WARNING: OPENROUTER_API_KEY is not set — OpenCode cannot authenticate." >&2
fi

exec gosu appuser "$@"
