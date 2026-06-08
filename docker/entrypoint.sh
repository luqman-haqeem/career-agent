#!/bin/sh
# Runs as root: seed subscription credentials, fix volume ownership, then drop
# to the non-root appuser to run the bot.
set -e

CLAUDE_DIR="$HOME/.claude"
mkdir -p "$CLAUDE_DIR"

SEED=/seed/.credentials.json
DEST="$CLAUDE_DIR/.credentials.json"

# Seed the subscription OAuth token from the read-only mount (if provided).
# Copied into the container's own Claude state so it never writes back to the
# host file. The CLI refreshes the access token here using the refresh token.
if [ -f "$SEED" ]; then
    cp "$SEED" "$DEST"
    chmod 600 "$DEST"
fi

# Named volumes mount root-owned; hand them to appuser. Bind-mounted app data
# is already UID 1000, so this is a no-op there.
chown -R appuser:appuser "$CLAUDE_DIR" 2>/dev/null || true
chown appuser:appuser /app/memory /app/resumes /app/data 2>/dev/null || true

if [ ! -f "$DEST" ]; then
    echo "WARNING: no Claude credentials found. Mount ~/.claude " \
         "to /seed (see docker-compose.yml)." >&2
fi

# Auto-reseed: there is ONE OAuth identity whose refresh token rotates, so the
# host CLI and this container can drift apart and the container's token goes
# stale (401). Mirror the host's credentials in, but ONLY when the host file is
# newer than ours ("-nt") — so the host wins while it's active, and the
# container keeps its own self-refreshed token when the host is idle/off.
# Interval (seconds) is configurable via RESEED_INTERVAL (default 300).
reseed_loop() {
    while true; do
        sleep "${RESEED_INTERVAL:-300}"
        if [ -f "$SEED" ] && [ "$SEED" -nt "$DEST" ]; then
            cp "$SEED" "$DEST" 2>/dev/null \
                && chown appuser:appuser "$DEST" 2>/dev/null || true
        fi
    done
}
reseed_loop &

exec gosu appuser "$@"
