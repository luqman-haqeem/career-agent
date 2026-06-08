# Career Agent

A private Telegram bot that remembers your career, turns your real experiences
into structured CV points, judges how well jobs fit you, and writes tailored
resumes — **never inventing experience you don't have.**

It runs on your **Claude Code subscription** (the local `claude` CLI) — **no
Anthropic API key and no per-token bill.** Usage counts against your subscription.

> Single-user by design. Anthropic's terms don't allow offering a
> subscription-backed product to others, so keep it locked to your own Telegram
> ID and don't redistribute it as a service.

## What it does
- **Remembers you** — profile, skills, goals, vision, and projects, stored as
  editable markdown files under `memory/`.
- **Experience → CV point** — send any task/project you worked on; it structures
  it (Situation / Task / Action / Result / metrics + skills) and saves it.
- **JD fit advice** — paste a job description (or a link); it rates your fit and
  names the real gaps.
- **Tailored resume** — builds a resume for a JD strictly from what it knows about
  you, and sends it back as a file.
- **Resume critique** — ask it to score a generated resume against a JD; you get a
  /100 with how five reader types (ATS bot → technical reviewer) see it and the
  top fixes.
- **Stays honest over time** — a corrections log remembers any fact you've fixed,
  and metrics carry provenance (verified / self-reported / estimate) so nothing
  gets inflated.
- **No fabrication** — a hard rule in `CLAUDE.md`; gaps are flagged, not faked.
- Auto-apply is intentionally **not** included.

## How it works
The bot is a thin bridge: each Telegram message is handed to the `claude` CLI in
headless mode (`agent.py`). Claude uses its own Read/Write/Edit and WebFetch tools
to manage the markdown memory in this folder, following the rules in `CLAUDE.md`.
Conversation continuity is kept per chat via Claude's `--resume` session id.

## Setup (one time)

1. **Make sure Claude Code is installed and logged in** (you already use it):
   ```bash
   claude --version        # should print a version
   claude -p "hi"          # should reply without asking for an API key
   ```

2. **Install the bot's two dependencies**
   ```bash
   cd ~/career-agent
   python3 -m pip install -r requirements.txt
   ```

3. **Create a Telegram bot**: message **@BotFather**, send `/newbot`, copy the token.

4. **Configure**
   ```bash
   cp .env.example .env
   ```
   Put your `TELEGRAM_BOT_TOKEN` in `.env`.

5. **Run it**
   ```bash
   python3 bot.py
   ```
   Open your bot in Telegram, send `/start` — it replies with **your user ID**.
   Paste that into `ALLOWED_USER_IDS` in `.env` and restart so only you can use it.

## Using it
Just chat naturally:
- "I'm a backend engineer at Acme, 4 years' experience, moving toward platform work."
- "Last quarter I migrated billing to Kafka and cut latency from 800ms to 120ms."
- "Here's a job: <link> — is it a good fit?"
- "Write me a resume for that role."

Resumes come back as a polished **PDF** (rendered locally from a JSON Resume file
via `render.py` + Tectonic) — the bot sends you both the PDF and the `.json`.

Handy commands: `/status` shows your current conversation context size with a
one-tap **Reset context** button; `/reset` clears it; `/help` lists everything.

## Optional: run on OpenCode instead of the subscription
By default the bot runs on your Claude Code subscription. You can instead point it
at [OpenCode](https://opencode.ai) to use any model/provider you configure
(OpenRouter, Anthropic API, a local model, etc.) by setting `AI_BACKEND=opencode`
in `.env`. The default is unchanged. Full guide: [`docs/opencode-setup.md`](docs/opencode-setup.md).

## Run in Docker (isolated)
Runs the bot in a container with its own `claude` CLI. Your subscription login is
mounted **read-only** (never baked into the image), and your career data stays on
the host via bind mounts.

Requirements: Docker + Compose, and a working `claude` login on the host
(`~/.claude/.credentials.json` must exist).

```bash
cd ~/career-agent
cp .env.example .env          # then put your TELEGRAM_BOT_TOKEN in .env
docker compose up -d --build  # build + run in the background
docker compose logs -f        # watch logs (send /start in Telegram)
```

Manage it:
```bash
docker compose restart        # restart
docker compose down           # stop & remove the container
docker compose up -d --build  # rebuild after changing code (memory/CLAUDE.md
                              # update live without a rebuild)
```

What's mounted (see `docker-compose.yml`):
- `./memory`, `./resumes`, `./data` → your data, editable on the host.
- `./CLAUDE.md` (read-only) → tune behavior without rebuilding.
- `~/.claude/.credentials.json` (read-only) → your subscription auth.
- a named volume `claude_state` → the container's own Claude session state.

Notes:
- **Auto-reseed:** the container mirrors your host Claude login (`~/.claude` is
  mounted read-only) every few minutes, so its token never goes stale — no manual
  re-seeding after you log in or use `claude` on the host. It still self-refreshes
  on its own when the host is idle/off. Tune the interval with `RESEED_INTERVAL`
  (seconds, default 300) in `.env`.
- It's still single-user — keep `ALLOWED_USER_IDS` set in `.env`.

## Where things live
| Path | What |
|------|------|
| `CLAUDE.md` | the agent's rules — edit to tune its behavior |
| `memory/profile.md` | who you are, skills, vision |
| `memory/goals.md` | short/long-term goals, targets |
| `memory/experiences/` | one structured CV point per file |
| `memory/projects/` | personal projects |
| `memory/corrections.md` | facts you've corrected — never repeated |
| `resumes/` | generated, tailored resumes |
| `data/sessions/` | per-chat Claude session id |

## Tuning
- Edit `CLAUDE.md` to change tone or rules (re-read on every message).
- `CAREER_AGENT_MODEL` in `.env` picks the model (Opus for quality, a Sonnet id
  to use fewer credits).

## Notes & limits
- Some job sites (e.g. LinkedIn) block bots, so link fetching may fail — just
  paste the JD text and it works the same.
- Keep `.env` and `memory/` private (already git-ignored).
- To add WhatsApp later, only `bot.py` would change (Twilio/Meta Business API,
  which needs paid approval); `agent.py` and `CLAUDE.md` stay the same.
