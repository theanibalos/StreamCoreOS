# Contributing to StreamCoreOS

Thanks for your interest in contributing! This project has a strict architectural pattern — reading this document first will save you from writing a PR that can't be merged.

---

## Before You Start

**Open an issue before writing code.**

If you have a feature idea or found a bug, open an issue first. This project has specific roadmap decisions that may affect whether a contribution fits — the i18n discussion is a good example of catching that early. A quick discussion prevents wasted effort on both sides.

---

## Dev Environment

```bash
git clone https://github.com/theanibalos/StreamCoreOS
cd StreamCoreOS
cp .env.example .env   # fill in TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET
uv run main.py         # start the app
uv run pytest          # run all tests
```

SQLite is the default — no Docker needed to get started. Optional PostgreSQL:

```bash
docker compose -f dev_infra/docker-compose.yml up -d
```

Full interactive API docs at `http://localhost:8000/docs` while the server is running.

---

## How the Architecture Works

Everything is a plugin. The kernel auto-discovers every file in `domains/{domain}/plugins/` — you never touch `main.py` or any registration list.

```
domains/{domain}/
  models/{entity}.py          # DB mirror only (Pydantic BaseModel)
  migrations/001_xxx.sql      # Raw SQL, auto-executed on boot
  plugins/{feature}_plugin.py # 1 file = 1 feature
```

Tools are injected by parameter name in `__init__`:

```python
class MyPlugin(BasePlugin):
    def __init__(self, db, twitch, event_bus, logger):
        ...
```

Read `AI_CONTEXT.md` for the full plugin contract and available tools before writing any code.

## How to add an Overlay Widget

Overlay widgets are the core of the builder. Adding one involves four parts:

### 1. The Component (Frontend)
Create your widget in `StreamCoreOS-Front/src/lib/features/overlays/components/`. 
- **Fidelity**: You can use standard pixel values (e.g., `font-size: 24px`) for a 1080p target resolution. The builder handles scaling automatically using CSS transforms, so your component will look identical in the builder and in OBS without any extra code.

### 2. Registry (Frontend)
Register your component in `StreamCoreOS-Front/src/lib/features/overlays/index.ts`. Add it to the `WIDGET_REGISTRY` object so the system knows which type maps to which Svelte file.

### 3. Builder UI (Frontend)
- Add a button for your widget in `Toolbar.svelte` to allow manual placement.
- Add configuration fields (like Meta targets or Data Sources) in `PropertyEditor.svelte`.

### 4. Data Sources (Backend/Optional)
If your widget needs live data (like subscriber counts), add the logic in `StreamCoreOS/domains/overlays/plugins/overlay_data_plugin.py` and ensure the key matches what you use in the frontend selector.

### 5. AI Awareness (Optional)
Update the `SYSTEM_PROMPT` in `GenerateOverlayPlugin.py` so the AI assistant knows how to generate your new widget type when a user asks for it.

---

## Rules (Non-Negotiable)

These are enforced during review:

1. **1 file = 1 feature** — one plugin file, one responsibility.
2. **Never modify `main.py`** — the kernel discovers everything automatically.
3. **No cross-domain imports** — use `event_bus` to communicate between domains.
4. **Schemas inline** — request and response models go inside the plugin file, not in `models/`.
5. **DB models are DB mirrors only** — `models/{entity}.py` reflects only the table columns.
6. **Return format** — always `{"success": bool, "data": ..., "error": ...}`.
7. **SQL placeholders** — always `$1, $2, $3...` (compatible with both SQLite and PostgreSQL).
8. **No hardcoded strings in bot messages** — any message sent to Twitch chat must come from the DB or be user-configurable. See the note below.

### On hardcoded strings

Bot messages (auto-responses, command replies, etc.) must not have hardcoded text. The pattern is DB-backed: the user configures their own messages in their own language. `ChatCommandHandlerPlugin` is the reference implementation.

i18n file systems belong to the frontend, not the core.

---

## What Makes a Good PR

- Solves one clearly defined problem.
- Follows the plugin pattern — the reviewer should be able to drop the file into `plugins/` and see it work.
- Includes tests if the logic is non-trivial. See `tests/` for existing examples.
- Does not add dependencies without prior discussion in the issue.
- Does not touch framework internals unless the issue explicitly calls for it.

---

## What Will Not Be Merged

- Plugins that import from another domain directly.
- New tools added to `tools/` without a prior architectural discussion.
- Changes to `main.py` without prior discussion.
- Hardcoded strings in chat messages or bot responses.

---

## Submitting a PR

1. Fork the repo and create a branch from `main`.
2. Make your changes following the rules above.
3. Run `uv run pytest` — all tests must pass.
4. Open the PR with a description that references the issue it closes.

---

## Questions?

Open an issue. That's the right place for architectural questions, feedback on the roadmap, or proposals like the i18n discussion that led to this file existing.
