# StreamCoreOS

A complete Twitch streaming platform — chatbot, AI moderation, TTS with per-user voices, overlay builder with AI generation, real-time dashboard, loyalty points, and more. All as isolated, single-file plugins on a self-contained async kernel.

**License:** AGPL-3.0 — free to self-host, modifications must be open-sourced if used as a network service.

> **⚠️ SECURITY WARNING — LOCAL USE ONLY**
> StreamCoreOS has **no authentication layer** on the dashboard or API. It is designed to run exclusively on your local machine (localhost). **Do NOT expose it directly on a VPS or any public network.** Anyone with access to the port would have full control over your stream, chatbot, TTS, moderation, and overlays. If you need remote access, put it behind a VPN (Tailscale, WireGuard) or a reverse proxy with authentication (Authelia, Cloudflare Access). Running it raw on a public IP is a critical security risk.

---

## Table of Contents

- [Quick Start](#quick-start)
- [Deploy](#deploy)
- [Architecture](#architecture)
- [Setup](#setup)
- [Domains](#domains)
- [Event Catalog](#event-catalog)
- [How to Write a New Feature](#how-to-write-a-new-feature)
- [Available Tools Reference](#available-tools-reference)
- [Developing with AI](#developing-with-ai)
- [API Reference](#api-reference)

---

## Quick Start

> **Local use only.** Runs on `localhost`. Do not expose ports 80 or 8000 on a public network without a VPN or authenticated reverse proxy in front.

You only need Docker. No Node, no Python, no cloning.

### Step 1 — Register a Twitch app

1. Go to [dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps) → **Register Your Application**
2. Fill in:
   - **Name:** anything (e.g. `StreamCoreOS`)
   - **OAuth Redirect URLs:** `http://localhost/api/auth/twitch/callback`
   - **Category:** Application Integration
   - **Client Type:** Confidential
3. Click **Create**, then **Manage** → copy your **Client ID** and generate a **Client Secret**

### Step 2 — Download and configure

```bash
curl -O https://raw.githubusercontent.com/theanibalos/StreamCoreOS/main/docker-compose.prod.yml
curl -O https://raw.githubusercontent.com/theanibalos/StreamCoreOS/main/.env.example
mv .env.example .env
```

Open `.env` and fill in these fields:

```env
TWITCH_CLIENT_ID=        # from Twitch Developer Console
TWITCH_CLIENT_SECRET=    # from Twitch Developer Console
TWITCH_REDIRECT_URI=http://localhost/api/auth/twitch/callback

FRONTEND_URL=http://localhost
HTTP_HOST=0.0.0.0
```

### Step 3 — Run

```bash
docker compose -f docker-compose.prod.yml up -d
```

Docker pulls the images automatically. Open `http://localhost`, click **Connect with Twitch** and authorize. Done.

**Data persists automatically.** `docker-compose.prod.yml` mounts a `./data` folder next to your
`.env` into the container — that's where `database.db` (tokens, viewers, points, overlays, chat
config, everything) lives. `docker compose down` / `up` again and nothing is lost. To back up,
just copy the `data/` folder; to reset, delete it and restart.

---

## Other Deploy Options

> All options run on `localhost`. Same security warning applies.

### Build from source (no pre-built images)

```bash
git clone https://github.com/theanibalos/StreamCoreOS
cd StreamCoreOS
cp .env.example .env   # fill in the same fields as above
docker compose -f docker-compose.selfhost.yml up -d --build
```

### Backend only

```bash
docker compose up -d --build
```

Exposes the backend API on port 8000. Frontend not included. Useful for development.

### Full stack dev, hot-reload (Docker)

Backend and frontend live in separate repos but nothing works without both, so this is the
recommended way to develop day-to-day: one `up`, both sides live-reload.

```bash
git clone https://github.com/theanibalos/StreamCoreOS
git clone https://github.com/theanibalos/StreamCoreOS-Front
# both repos must sit side by side — StreamCoreOS reads
# ../StreamCoreOS-Front as the build context
cd StreamCoreOS
cp .env.example .env.dev
```

Edit `.env.dev` (**not** `.env` — see [Why two env files](#why-two-env-files) below):

```env
TWITCH_CLIENT_ID=
TWITCH_CLIENT_SECRET=
TWITCH_REDIRECT_URI=http://localhost:8000/api/auth/twitch/callback
FRONTEND_URL=http://localhost:5173
```

Register `http://localhost:8000/api/auth/twitch/callback` as an OAuth Redirect URL in the
Twitch console (in addition to the prod one — Twitch lets you register several at once).

```bash
docker compose -f docker-compose.dev.yml up
```

- Frontend (Vite + HMR) → `http://localhost:5173`
- Backend (auto-restart on `.py` changes via `cli.py`/watchfiles) → `http://localhost:8000`

Edit a `.svelte` or `.py` file and the change picks up automatically — no rebuild, no restart.

#### Why two env files

`docker-compose.prod.yml`, `docker-compose.local.yml`, and `docker-compose.selfhost.yml` all
proxy through nginx on port 80, so `.env` has `FRONTEND_URL=http://localhost` and
`TWITCH_REDIRECT_URI=http://localhost/api/auth/twitch/callback`. `docker-compose.dev.yml`
publishes the frontend and backend on their own ports directly (`:5173` / `:8000`) with no nginx
in front, so it needs different values for those two variables. Keeping them in a separate
`.env.dev` means running one compose file never breaks OAuth on the other — copy the same
`TWITCH_CLIENT_ID`/`TWITCH_CLIENT_SECRET` into both files, only `FRONTEND_URL` and
`TWITCH_REDIRECT_URI` differ.

`.env.dev` is gitignored, same as `.env`.

### Development (no Docker)

```bash
git clone https://github.com/theanibalos/StreamCoreOS
cd StreamCoreOS
cp .env.example .env
# Set TWITCH_REDIRECT_URI=http://localhost:5173/api/auth/twitch/callback
# Set HTTP_HOST=127.0.0.1
uv run main.py
```

Then, in the sibling `StreamCoreOS-Front` repo, run the frontend separately:

```bash
cd ../StreamCoreOS-Front
pnpm install
pnpm run dev
```

`vite.config.ts`'s `/api` proxy defaults to `http://localhost:8000`, so no extra config needed
outside Docker. API docs at `http://localhost:8000/docs`.

---

## Architecture

```
StreamCoreOS/
├── core/                        # Kernel: IoC container, DI, auto-discovery (~340 lines)
├── tools/
│   ├── twitch/                  # Twitch platform wrapper (OAuth + EventSub + IRC)
│   ├── sqlite/                  # Default DB — swap to PostgreSQL with zero plugin changes
│   ├── tts/                     # TTS router (edge_tts + Voicebox providers)
│   ├── ai/                      # AI completions (OpenAI-compatible, local or cloud)
│   ├── event_bus/               # Pub/Sub + async RPC
│   ├── http_server/             # FastAPI gateway (REST + SSE + WebSocket)
│   ├── scheduler/               # Cron jobs (APScheduler)
│   ├── state/                   # In-memory key-value store
│   └── logger/                  # Structured logging with sinks
└── domains/
    ├── twitch_auth/             # OAuth flow + token storage + session restore
    ├── stream_state/            # Online/offline tracking + history
    ├── chat_bot/                # Chat dispatch + commands + variables + TTS + SSE
    ├── viewers/                 # Viewer profiles + points + regulars
    ├── moderation/              # AI mod + word/link/caps/spam filters + manual controls
    ├── timers/                  # Recurring scheduled chat messages
    ├── dashboard/               # Stats + real-time alerts SSE
    ├── overlays/                # Overlay builder + AI generation + live SSE
    ├── subscribers/             # Sub/bits/gifter tracking + leaderboards
    ├── tts_chat/                # TTS listener + per-user voice assignment
    ├── ai_config/               # AI provider configuration
    ├── system/                  # Observability — traces, events, health, SSE logs
    ├── twitch_redemptions/      # Channel point redemption handlers
    └── ping/                    # Health check
```

**One rule:** 1 file = 1 feature. Every plugin lives in `domains/{domain}/plugins/` and is auto-discovered. Never touch `main.py`.

---

## Setup

### Authentication Flow

1. Visit `http://localhost` → click **Connect with Twitch**
2. Authorize on Twitch
3. Twitch redirects back to `/api/auth/twitch/callback`
4. Token is saved to DB, EventSub WebSocket connects, IRC chat connects

On every subsequent restart the session is restored automatically from the DB — no need to re-authenticate.

---

## Domains

### `twitch_auth`
OAuth flow + token persistence + automatic session restore on boot.

### `stream_state`
Tracks stream online/offline. Publishes `stream.session.started` / `stream.session.ended`.

### `chat_bot`
IRC bridge, command system, stream variables, reminders, AI chat (`!ia`), real-time SSE stream.

**Command response variables:** `{user}`, `{touser}`, `{count}`, `{random X-Y}`, `{uptime}`, `{game}`, `{viewers}`, `{followage}`, `{var:name}`

**Stream variables** — manage from API or chat:
```
!setvar deaths +1       # increment
!setvar boss "Margit"   # set text
!deletevar deaths       # delete
```

### `viewers`
Viewer profiles, points, regulars tier. Auto-created on first chat message.

### `moderation`
Rule types: `word_filter`, `link_filter`, `caps_filter`, `spam_filter`, AI-powered filter.
Actions: `ban`, `timeout`, `delete`.

### `timers`
Recurring messages posted to chat on a cron schedule.

### `dashboard`
Aggregated stream stats + real-time SSE alert stream for all Twitch events.

### `overlays`
Overlay builder with widgets (alert, stat, progress bar, chat highlight, banner).
AI generation endpoint — describe the layout in text, get a configured overlay back.
Live SSE endpoint for real-time widget updates.

### `subscribers`
Subscription, bits, and gifter tracking with leaderboards.

### `tts_chat`
TTS listener with per-viewer voice assignment. Supports edge_tts (always available) and Voicebox.

### `ai_config`
Configure the AI provider (Ollama, OpenAI, Groq, OpenRouter, etc.) via API.

### `system`
Full observability: tool/plugin health, event bus traces, live log stream, metrics.

---

## Event Catalog

| Event | Published by | Payload keys |
|---|---|---|
| `stream.session.started` | `stream_status_plugin` | session_id, twitch_stream_id, started_at, broadcaster_login |
| `stream.session.ended` | `stream_status_plugin` | session_id, ended_at |
| `chat.message.received` | `chat_message_dispatcher_plugin` | channel, user_id, display_name, message, is_mod, is_sub, is_broadcaster, badges, timestamp |
| `chat.command.received` | `chat_message_dispatcher_plugin` | (above) + command, args |
| `chat.command.executed` | `chat_command_handler_plugin` | command, user_id, display_name |
| `moderation.action.taken` | mod plugins | twitch_id, display_name, action, reason, rule_id |
| `moderation.rules.updated` | rule CRUD plugins | rule_id |
| `timer.created/updated/deleted` | timer plugins | id, name |
| `dashboard.stats.updated` | `channel_stats_collector_plugin` | viewer_count, follower_count |
| `tts.audio.ready` | `tts_listener_plugin` | audio_b64, text, username, voice_id |
| `viewer.points.awarded` | points plugins | twitch_id, display_name, delta |
| `viewer.regular.added/removed` | regulars plugins | twitch_id, display_name |
| `subscriber.new/resub/gift/expired` | subscription plugins | twitch_id, display_name, tier |
| `event.delivery.failed` | `event_delivery_monitor_plugin` | event, event_id, subscriber, error |

---

## How to Write a New Feature

### 1. HTTP Endpoint

```python
# domains/my_domain/plugins/my_feature_plugin.py
from typing import Optional
from pydantic import BaseModel, Field
from core.base_plugin import BasePlugin

class MyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)

class MyResponse(BaseModel):
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None

class MyFeaturePlugin(BasePlugin):
    def __init__(self, http, db, event_bus, logger):
        self.http = http
        self.db = db
        self.bus = event_bus
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/my-domain/things", "POST", self.execute,
            tags=["MyDomain"],
            request_model=MyRequest,
            response_model=MyResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            req = MyRequest(**data)
            row_id = await self.db.execute(
                "INSERT INTO things (name) VALUES ($1) RETURNING id", [req.name]
            )
            await self.bus.publish("my_domain.thing.created", {"id": row_id, "name": req.name})
            return {"success": True, "data": {"id": row_id}}
        except Exception as e:
            self.logger.error(f"[MyFeature] {e}")
            return {"success": False, "error": str(e)}
```

Drop it in `domains/my_domain/plugins/` and restart. No other changes needed.

### 2. Twitch EventSub Listener

```python
class OnFollowPlugin(BasePlugin):
    def __init__(self, twitch, logger):
        self.twitch = twitch
        self.logger = logger

    async def on_boot(self):
        self.twitch.register(
            "channel.follow", "2",
            scopes=["moderator:read:followers"],
            condition={"broadcaster_user_id": "{broadcaster_id}", "moderator_user_id": "{broadcaster_id}"},
        )
        self.twitch.on_event("channel.follow", self._on_follow)

    async def _on_follow(self, event: dict):
        self.logger.info(f"New follower: {event.get('user_name')}")
```

### 3. Chat Listener

```python
class ChatReactionPlugin(BasePlugin):
    def __init__(self, event_bus, twitch, logger):
        self.bus = event_bus
        self.twitch = twitch
        self.logger = logger

    async def on_boot(self):
        await self.bus.subscribe("chat.message.received", self._on_message)

    async def _on_message(self, msg: dict):
        if "!hello" in msg["message"].lower():
            session = self.twitch.get_session()
            if session:
                await self.twitch.send_message(session["login"], f"Hello, {msg['display_name']}!")
```

### 4. Scheduled Job

```python
class HourlyCleanupPlugin(BasePlugin):
    def __init__(self, scheduler, db, logger):
        self.scheduler = scheduler
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.scheduler.add_job("0 * * * *", self._cleanup, job_id="hourly_cleanup")

    async def _cleanup(self):
        await self.db.execute("DELETE FROM my_table WHERE created_at < datetime('now', '-7 days')")
```

### 5. Domain with DB Migration

```sql
-- domains/my_domain/migrations/001_create_my_table.sql
CREATE TABLE IF NOT EXISTS my_table (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
```

The kernel auto-discovers and runs migrations on boot. No registration needed.

---

## Available Tools Reference

Tools are injected by parameter name in `__init__`. See `AI_CONTEXT.md` for the full API reference.

| Tool | Inject as | Purpose |
|---|---|---|
| TwitchTool | `twitch` | OAuth, EventSub, IRC, Helix API |
| SQLiteTool | `db` | Async SQLite with PostgreSQL-compatible placeholders |
| EventBusTool | `event_bus` | Pub/Sub + async RPC |
| HttpServerTool | `http` | FastAPI REST + SSE + WebSocket |
| SchedulerTool | `scheduler` | Cron + one-shot jobs |
| StateTool | `state` | In-memory key-value store |
| LoggerTool | `logger` | Structured logging |
| AITool | `ai` | AI completions (OpenAI-compatible) |
| TTSTool | `tts` | Text-to-speech with provider routing |
| AuthTool | `auth` | JWT + bcrypt |
| ConfigTool | `config` | Validated env var access |
| TelemetryTool | `telemetry` | OpenTelemetry (optional) |

---

## Developing with AI

Every plugin follows the same pattern. Give the AI the right files and it will generate a working plugin with no additional context.

**Reading path:**

> Read `AI_CONTEXT.md` and `domains/{domain}/models/{model}.py`, then write the plugin.

**Workflow:**

1. Describe the feature, ask for a plan first
2. Review the plan
3. Say "go ahead"

**Example:**

> Read AI_CONTEXT.md. I want an endpoint to manually add or remove points from a viewer.
> Propose a plan: what file you'll create, what the endpoint looks like, what DB operation it does.
> Don't write code yet.

---

## API Reference

Full interactive docs at `http://localhost:8000/docs`.

All endpoints are prefixed with `/api/`.

| Method | Path | Description |
|---|---|---|
| GET | `/api/ping` | Health check |
| GET | `/api/auth/twitch` | Start OAuth flow |
| GET | `/api/auth/twitch/callback` | OAuth callback |
| GET | `/api/auth/twitch/status` | Session status |
| POST | `/api/auth/twitch/logout` | Logout |
| GET | `/api/stream/status` | Current stream state |
| GET | `/api/stream/sessions` | Session history |
| GET | `/api/chat/stream` | SSE — live chat messages |
| GET | `/api/chat/commands` | List commands |
| POST | `/api/chat/commands` | Create command |
| PUT | `/api/chat/commands/{id}` | Update command |
| DELETE | `/api/chat/commands/{id}` | Delete command |
| GET | `/api/chat/vars` | List stream variables |
| POST | `/api/chat/vars` | Create variable |
| PUT | `/api/chat/vars/{id}` | Update variable |
| DELETE | `/api/chat/vars/{id}` | Delete variable |
| GET | `/api/chat/reminders` | List reminders |
| GET | `/api/timers` | List timers |
| POST | `/api/timers` | Create timer |
| PUT | `/api/timers/{id}` | Update timer |
| DELETE | `/api/timers/{id}` | Delete timer |
| GET | `/api/viewers` | List viewers |
| GET | `/api/viewers/{login}` | Get viewer |
| GET | `/api/viewers/leaderboard` | Points leaderboard |
| GET | `/api/viewers/regulars` | List regulars |
| POST | `/api/viewers/regulars` | Add regular |
| DELETE | `/api/viewers/regulars/{twitch_id}` | Remove regular |
| POST | `/api/viewers/{twitch_id}/points` | Adjust points |
| GET | `/api/moderation/rules` | List rules |
| POST | `/api/moderation/rules` | Create rule |
| PUT | `/api/moderation/rules/{id}` | Update rule |
| DELETE | `/api/moderation/rules/{id}` | Delete rule |
| GET | `/api/moderation/log` | Mod action log |
| POST | `/api/moderation/ban` | Manual ban |
| POST | `/api/moderation/timeout` | Manual timeout |
| POST | `/api/moderation/unban` | Manual unban |
| GET | `/api/overlays` | List overlays |
| POST | `/api/overlays` | Create overlay |
| GET | `/api/overlays/{id}` | Get overlay |
| PUT | `/api/overlays/{id}` | Update overlay |
| DELETE | `/api/overlays/{id}` | Delete overlay |
| GET | `/api/overlays/{id}/config` | Get config (OBS use) |
| POST | `/api/overlays/generate` | AI overlay generation |
| GET | `/api/overlays/data` | Live stat values |
| GET | `/api/overlays/stats` | SSE — real-time stat updates |
| GET | `/api/subscribers/leaderboard` | Subscribers leaderboard |
| GET | `/api/gifters/leaderboard` | Gifters leaderboard |
| GET | `/api/bits/leaderboard` | Bits leaderboard |
| POST | `/api/subscribers/sync` | Sync subscribers from Twitch |
| POST | `/api/bits/sync` | Sync bits from Twitch |
| GET | `/api/tts/voices` | List available voices |
| GET | `/api/tts/settings` | TTS settings |
| PUT | `/api/tts/settings` | Update TTS settings |
| GET | `/api/tts/user-voices` | All user voice assignments |
| GET | `/api/tts/user-voices/{login}` | Get user voice |
| PUT | `/api/tts/user-voices` | Assign user voice |
| DELETE | `/api/tts/user-voices/{login}` | Remove user voice |
| GET | `/api/tts/overlay/stream` | SSE — TTS audio stream |
| GET | `/api/ai/config` | AI provider config |
| PUT | `/api/ai/config` | Update AI config |
| POST | `/api/ai/test` | Test AI connection |
| GET | `/api/dashboard/stats` | Stream stats |
| GET | `/api/dashboard/stats/history` | Stats history |
| GET | `/api/dashboard/alerts` | SSE — real-time events |
| POST | `/api/dashboard/alerts/test` | Send test alert |
| GET | `/api/system/status` | Tools + plugins health |
| GET | `/api/system/events` | Last 500 event bus records |
| GET | `/api/system/events/stream` | SSE — live event bus |
| GET | `/api/system/logs/stream` | SSE — live logs |
| GET | `/api/system/traces/flat` | Causality traces (flat) |
| GET | `/api/system/traces/tree` | Causality traces (tree) |
| GET | `/api/system/traces/stream` | SSE — live traces |

---

## Commands

```bash
uv run main.py                                              # Run
uv run pytest                                               # All tests
uv run pytest tests/test_file.py                            # Single test
docker compose -f dev_infra/docker-compose.yml up -d        # Dev infra
docker compose -f docker-compose.selfhost.yml up -d --build # Full stack from source
docker compose -f docker-compose.prod.yml up -d             # Full stack from images
docker compose -f docker-compose.dev.yml up                 # Full stack, hot-reload dev
```

---

**Built by [Anibal Fernandez](https://github.com/theanibalos) — licensed under AGPL-3.0**
