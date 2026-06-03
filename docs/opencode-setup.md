# Using the OpenCode backend

The Career Agent can run on **either** brain:

| `AI_BACKEND` | What it uses | Cost |
|--------------|--------------|------|
| `claude_cli` (default) | local `claude` CLI on your Claude subscription | included in subscription |
| `opencode` | the [OpenCode](https://opencode.ai) CLI agent, pointed at any model | depends on the provider you pick |

OpenCode is model-agnostic — you point it at OpenRouter, the Anthropic API, a
local model, or (via a community plugin) your Claude subscription. Your
`CLAUDE.md`, memory files, and resume pipeline all stay the same; only the brain
changes.

> Default stays `claude_cli`. Nothing changes until you set `AI_BACKEND=opencode`.

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

## 2. Connect a provider (pick ONE)

Auth is stored in `~/.local/share/opencode/auth.json`. The headless-friendly way:

```bash
opencode auth login
```

…then choose your provider. The main options for this bot:

### a) OpenRouter (simplest paid option — one key, many models)
- Get a key at <https://openrouter.ai/settings/keys>.
- `opencode auth login` → **OpenRouter** → paste the key.
- Or set the env var: `OPENROUTER_API_KEY=...`

### b) Anthropic API key (pay-as-you-go Claude, NOT the subscription)
- `opencode auth login` → **Anthropic** → "Manually enter API Key".
- Or set: `ANTHROPIC_API_KEY=...`

### c) Local model (Ollama / LM Studio — free, runs on your hardware)
Add a provider block to `~/.config/opencode/opencode.json`:
```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Ollama (local)",
      "options": { "baseURL": "http://localhost:11434/v1" },
      "models": { "qwen2.5:14b": { "name": "Qwen 2.5 14B" } }
    }
  }
}
```
Then your model string is `ollama/qwen2.5:14b`.

### d) Your Claude Pro/Max subscription (community plugin — ToS-gray)
There are community OpenCode plugins that reuse your existing Claude Code login
(e.g. [opencode-claude-auth](https://github.com/griffinmartin/opencode-claude-auth)).
⚠️ Anthropic's ToS says subscription tokens are for official clients only; these
are unofficial workarounds and may break. The built-in `claude_cli` backend
already uses your subscription the supported way — only reach for this if you
specifically want OpenCode's harness on the subscription.

## 3. Pick your model string

OpenCode model strings are `provider/model`:
- Anthropic: `anthropic/claude-sonnet-4.5`
- OpenRouter: `openrouter/<id-from-openrouter>`, e.g. `openrouter/anthropic/claude-sonnet-4.5`
- Local: `ollama/<model>` (per your config above)

Not sure of the exact slug? Run `opencode` once (TUI), pick the model
interactively, or browse <https://models.dev>.

Smoke-test it outside the bot first:
```bash
opencode run "say hello" --model openrouter/anthropic/claude-sonnet-4.5
```

## 4. Point the Career Agent at OpenCode

Edit `.env`:
```ini
AI_BACKEND=opencode
OPENCODE_MODEL=openrouter/anthropic/claude-sonnet-4.5
# OPENCODE_BIN=/custom/path/to/opencode   # only if not on PATH
```

Restart:
```bash
docker compose up -d
```

To switch back, set `AI_BACKEND=claude_cli` (or remove the line) and restart.

---

## Running it inside Docker (important)

The current image installs the `claude` CLI, **not** OpenCode. To use the
OpenCode backend in the containerized bot you must:

1. **Install OpenCode in the image** — add to the `Dockerfile` (as `appuser`):
   ```dockerfile
   RUN curl -fsSL https://opencode.ai/install | bash
   ```
   (or `npm install -g opencode-ai` if Node is present).

2. **Get its auth into the container.** OpenCode reads
   `~/.local/share/opencode/auth.json`. Mirror how the Claude credentials are
   seeded: either
   - mount your host `~/.local/share/opencode/auth.json` read-only and have the
     entrypoint copy it into the container user's home (like the existing
     credential seed), **or**
   - set provider env vars directly in `.env` (e.g. `OPENROUTER_API_KEY=...`),
     which `docker-compose`'s `env_file` already passes through — no auth.json
     needed for env-var providers.

   The env-var route is the least fuss: add `OPENROUTER_API_KEY` to `.env`, set
   `OPENCODE_MODEL=openrouter/...`, and OpenCode picks it up.

3. Rebuild: `docker compose up -d --build`.

---

## Notes & caveats

- **Tool surface is wider.** Unlike the locked-down `claude_cli` path (no shell),
  OpenCode runs with its default toolset, which can include bash. That's a
  conscious choice for this setup; it can be restricted later via an
  `opencode.json` permission deny-list if you want parity.
- **Cost.** OpenRouter / Anthropic API bill per token. The local route is free
  but quality depends on your model + hardware. The subscription route stays
  free but is ToS-gray under OpenCode.
- **Untested live here.** OpenCode wasn't installed when this was built; validate
  with the `opencode run "say hello"` smoke-test above before relying on it.

## Sources
- OpenCode CLI: <https://opencode.ai/docs/cli/>
- Providers: <https://opencode.ai/docs/providers/>
- Rules (CLAUDE.md auto-load): <https://opencode.ai/docs/rules/>
