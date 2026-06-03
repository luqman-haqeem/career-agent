#!/bin/sh
# Runs as root: seed subscription credentials, fix volume ownership, then drop
# to the non-root appuser to run the bot.
set -e

CLAUDE_DIR="$HOME/.claude"
mkdir -p "$CLAUDE_DIR"

# Seed the subscription OAuth token from the read-only mount (if provided).
# Copied into the container's own Claude state so it never writes back to the
# host file. The CLI refreshes the access token here using the refresh token.
if [ -f /seed/credentials.json ]; then
    cp /seed/credentials.json "$CLAUDE_DIR/.credentials.json"
    chmod 600 "$CLAUDE_DIR/.credentials.json"
fi

# Named volumes mount root-owned; hand them to appuser. Bind-mounted app data
# is already UID 1000, so this is a no-op there.
chown -R appuser:appuser "$CLAUDE_DIR" 2>/dev/null || true
chown appuser:appuser /app/memory /app/resumes /app/data 2>/dev/null || true

if [ ! -f "$CLAUDE_DIR/.credentials.json" ]; then
    echo "WARNING: no Claude credentials found. Mount ~/.claude/.credentials.json " \
         "to /seed/credentials.json (see docker-compose.yml)." >&2
fi

exec gosu appuser "$@"
