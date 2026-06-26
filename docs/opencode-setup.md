# OpenCode setup

The Career Agent runs on the **[OpenCode](https://opencode.ai) CLI** pointed at
**OpenRouter** (per-token billing). Your `CLAUDE.md`, memory files, and resume
pipeline are unchanged — OpenCode just drives the session.

---

## 1. Install OpenCode

```bash
# either
npm install -g opencode-ai
# or
curl -fsSL https://opencode.ai/install | bash
```

Verify:
```bash
opencode --version
```

## 2. Get an OpenRouter key

Sign up or log in at <https://openrouter.ai/settings/keys> and create an API key.
Add it to `.env`:

```ini
OPENROUTER_API_KEY=your-key-here
```

The `docker compose` setup passes it into the container via `env_file` — no
`auth.json` file or extra mounts needed.

## 3. Set your model (optional)

The default model is `openrouter/google/gemini-2.5-flash-lite` — cheap and
produces clean, parseable output. To use a different model, set its slug, e.g.:

```ini
OPENCODE_MODEL=openrouter/meta-llama/llama-3.3-70b-instruct
```

> **Avoid "thinking"-variant models.** Models that expose chain-of-thought
> (e.g. `*-thinking`, `*-r1`) leak reasoning tokens into the reply, which
> breaks the bot's parser.

Browse available model slugs at <https://openrouter.ai/models>.

## 3b. Different models per task (optional)

By default every task uses `OPENCODE_MODEL`. You can override per task — each
falls back to `OPENCODE_MODEL` when unset:

```ini
SCAN_MODEL=openrouter/...        # job-discovery scans
RESUME_MODEL=openrouter/...      # resume generation
CRITIQUE_MODEL=openrouter/...    # resume critique / scoring
CLASSIFIER_MODEL=openrouter/...  # the per-message classifier (defaults to OPENCODE_MODEL)
```

Scans and the **Apply**-button resume have their own code paths, so they use
`SCAN_MODEL` / `RESUME_MODEL` directly. But a message you **type** (e.g. "critique
it" or "write me a resume") all comes through one path — the bot can't tell what
it is by code. So when you set a distinct `CRITIQUE_MODEL` or `RESUME_MODEL`, the
bot makes one cheap OpenRouter call per typed message to classify it
(critique / resume / default) and pick the model. If you set neither, that
classifier never runs and there's no extra cost.

## 4. Smoke-test before running the bot

```bash
opencode run "say hello" --model openrouter/google/gemini-2.5-flash-lite
```

You should get a short, plain text reply with no tool noise.

## 5. Start the bot

```bash
docker compose up -d --build
```

The image installs OpenCode. Your `OPENROUTER_API_KEY` flows in via `env_file`.

---

## Tool lockdown (`opencode.json`)

The repo ships an `opencode.json` at the root that restricts what tools the
agent can call: **bash is denied**; Read, Edit, Write, WebFetch, and WebSearch
are allowed. The compose file mounts it read-only into the container so the
same policy applies there.

You can inspect or tighten it by editing `opencode.json` — no rebuild needed,
just restart the container.

---

## Verify it works (live-validation checklist)

After starting, send these messages to your Telegram bot in order:

1. **Auth** — send any message; the bot should reply (not time out or throw an
   auth error). If you get a 401, check `OPENROUTER_API_KEY` in `.env`.

2. **Clean reply** — send "say hello". The reply should be a plain sentence with
   no `<antml_thinking>` blocks or raw JSON. If you see leaked reasoning, switch
   to a non-thinking model variant.

3. **CLAUDE.md rules** — send "make something up about my experience". It should
   decline and explain the no-fabrication rule. If it invents content, the
   `CLAUDE.md` rules file isn't loading.

4. **Web search** — send a link to a job posting and ask for a fit assessment.
   It should fetch the page. If WebFetch fails, check that `opencode.json` is
   being mounted and the `webfetch` tool is listed as allowed.

---

## Sources

- OpenCode CLI: <https://opencode.ai/docs/cli/>
- Providers: <https://opencode.ai/docs/providers/>
- Rules (CLAUDE.md auto-load): <https://opencode.ai/docs/rules/>
- OpenRouter models: <https://openrouter.ai/models>
