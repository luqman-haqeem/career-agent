FROM python:3.12-slim

# gosu: entrypoint fixes volume ownership as root then drops privileges.
# curl/ca-certificates: install the OpenCode CLI + tectonic.
# fontconfig + lib{graphite2,harfbuzz,freetype}: runtime deps of the tectonic binary.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl ca-certificates gosu fontconfig \
        libgraphite2-3 libharfbuzz0b libfreetype6 libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*

# Tectonic: a self-contained modern TeX engine that fetches packages on demand
# (no multi-GB TeX Live install). We warm its cache at build time below.
RUN curl --proto '=https' --tlsv1.2 -fsSL https://drop-sh.fullyjustified.net | sh \
    && mv tectonic /usr/local/bin/tectonic

# Non-root user; UID 1000 matches the host so bind-mounted files stay writable.
ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd -g ${APP_GID} appuser \
    && useradd -m -u ${APP_UID} -g ${APP_GID} appuser

ENV HOME=/home/appuser
ENV PATH="/home/appuser/.local/bin:/home/appuser/.opencode/bin:${PATH}"

WORKDIR /app

# Python deps (just python-telegram-bot + python-dotenv).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install the OpenCode CLI as appuser (-> /home/appuser/.opencode/bin).
USER appuser
RUN curl -fsSL https://opencode.ai/install | bash
USER root

# App source.
COPY . .
RUN chown -R appuser:appuser /app

# Warm Tectonic's package cache (and verify the template compiles) as appuser,
# so the first real render is fast and works offline.
USER appuser
RUN python3 render.py --selftest
USER root

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python3", "bot.py"]
