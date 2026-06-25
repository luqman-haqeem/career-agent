# Career Agent

A private Telegram bot that remembers your career, turns your real experiences
into structured CV points, judges how well jobs fit you, and writes tailored
resumes — **never inventing experience you don't have.**

It runs on the **[OpenCode](https://opencode.ai) CLI** pointed at **[OpenRouter](https://openrouter.ai)** — per-token billing, no subscription required. You bring your own `OPENROUTER_API_KEY`.

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
- **Learns your taste** — every job card's Apply / Skip (with a quick, job-specific
  reason or your own note) teaches it; it keeps an inferred-preferences file and uses
  it to rank future matches and sharpen fit advice. It's preferences, never invented
  experience.
- **No fabrication** — a hard rule in `CLAUDE.md`; gaps are flagged, not faked.
- Auto-apply is intentionally **not** included.

## How it works
The bot is a thin bridge: each Telegram message is handed to the OpenCode CLI in
headless mode (`agent.py`). OpenCode uses Read/Write/Edit and WebFetch tools to
manage the markdown memory in this folder, following the rules in `CLAUDE.md`
(auto-loaded by OpenCode). Conversation continuity is kept per chat via OpenCode's
session id. Tool access is locked down by `opencode.json` (bash denied; read,
edit, write, webfetch, websearch allowed).

## Setup (one time)

1. **Install OpenCode** and verify it runs:
   ```bash
   npm install -g opencode-ai   # or: curl -fsSL https://opencode.ai/install | bash
   opencode --version
   ```

2. **Get an OpenRouter API key** at <https://openrouter.ai/settings/keys>.

3. **Smoke-test** (confirms auth + model before starting the bot). The `opencode`
   CLI doesn't read `.env`, so pass the key in the shell for this check:
   ```bash
   OPENROUTER_API_KEY=sk-or-... opencode run "say hello" --model openrouter/google/gemini-2.5-flash-lite
   ```
   (The bot itself loads `OPENROUTER_API_KEY` from `.env` automatically — this
   manual export is only for the one-off smoke test.)

4. **Install the bot's dependencies**
   ```bash
   cd ~/career-agent
   python3 -m pip install -r requirements.txt
   ```

5. **Create a Telegram bot**: message **@BotFather**, send `/newbot`, copy the token.

6. **Configure**
   ```bash
   cp .env.example .env
   ```
   Put your `TELEGRAM_BOT_TOKEN` and `OPENROUTER_API_KEY` in `.env`.

7. **Run it**
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

Handy commands: `/status` shows whether a conversation is active with a one-tap
**Reset context** button; `/reset` clears it; `/help` lists everything.

## Run in Docker (isolated)

Runs the bot in a container with OpenCode installed. Your `OPENROUTER_API_KEY`
flows in via `.env` — no credential files to mount.

Requirements: Docker + Compose.

```bash
cd ~/career-agent
cp .env.example .env          # put your TELEGRAM_BOT_TOKEN + OPENROUTER_API_KEY in .env
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
- `./opencode.json` (read-only) → tool-lockdown policy (bash denied).
- a named volume `opencode_state` → the container's own OpenCode session state.

Notes:
- It's single-user — keep `ALLOWED_USER_IDS` set in `.env`.
- Full OpenCode setup details: [`docs/opencode-setup.md`](docs/opencode-setup.md).

## Where things live
| Path | What |
|------|------|
| `CLAUDE.md` | the agent's rules — edit to tune its behavior |
| `memory/profile.md` | who you are, skills, vision |
| `memory/goals.md` | short/long-term goals, targets |
| `memory/experiences/` | one structured CV point per file |
| `memory/projects/` | personal projects |
| `memory/corrections.md` | facts you've corrected — never repeated |
| `memory/preferences.md` | inferred Apply/Skip preferences — ranking signal, not facts |
| `resumes/` | generated, tailored resumes |
| `data/sessions/` | per-chat OpenCode session id |

## Tuning
- Edit `CLAUDE.md` to change tone or rules (re-read on every message).
- `OPENCODE_MODEL` in `.env` picks the model (any `openrouter/<provider>/<model>`
  slug from <https://openrouter.ai/models>). Avoid "thinking"-variant models —
  they leak chain-of-thought into replies.

## Notes & limits
- Some job sites (e.g. LinkedIn) block bots, so link fetching may fail — just
  paste the JD text and it works the same.
- Keep `.env` and `memory/` private (already git-ignored).
- To add WhatsApp later, only `bot.py` would change (Twilio/Meta Business API,
  which needs paid approval); `agent.py` and `CLAUDE.md` stay the same.
