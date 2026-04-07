# StreamCoreOS

A personal Twitch streaming backend built on [MicroCoreOS](https://github.com/theanibalos/MicroCoreOS). Handles OAuth, EventSub WebSocket, IRC chat, loyalty points, auto-moderation, and a real-time dashboard — all as isolated, single-file plugins.

---

## Table of Contents

- [StreamCoreOS](#streamcoreos)
  - [Table of Contents](#table-of-contents)
  - [Quick Start](#quick-start)
  - [Architecture](#architecture)
  - [Setup](#setup)
    - [Environment Variables](#environment-variables)
    - [Authentication Flow](#authentication-flow)
  - [How the Twitch Tool Works](#how-the-twitch-tool-works)
    - [Lifecycle](#lifecycle)
    - [Subscription Deduplication](#subscription-deduplication)
    - [Session Access](#session-access)
  - [Existing Domains](#existing-domains)
    - [`twitch_auth`](#twitch_auth)
    - [`stream_state`](#stream_state)
    - [`chat_bot`](#chat_bot)
    - [`loyalty`](#loyalty)
    - [`moderation`](#moderation)
    - [`dashboard`](#dashboard)
  - [Event Catalog](#event-catalog)
  - [How to Write a New Feature](#how-to-write-a-new-feature)
    - [1. HTTP Endpoint](#1-http-endpoint)
    - [2. Twitch EventSub Listener](#2-twitch-eventsub-listener)
    - [3. Chat Listener](#3-chat-listener)
    - [4. Scheduled Job](#4-scheduled-job)
    - [5. Event Bus Consumer](#5-event-bus-consumer)
    - [6. Domain with DB Migration](#6-domain-with-db-migration)
  - [Available Tools Reference](#available-tools-reference)
    - [`twitch`](#twitch)
    - [`db`](#db)
    - [`event_bus`](#event_bus)
    - [`http`](#http)
    - [`scheduler`](#scheduler)
    - [`state`](#state)
    - [`logger`](#logger)
  - [Developing with AI](#developing-with-ai)
    - [Reading path for the AI](#reading-path-for-the-ai)
    - [The two-step workflow](#the-two-step-workflow)
    - [Example prompts](#example-prompts)
    - [Tips for better results](#tips-for-better-results)
  - [API Reference](#api-reference)
  - [Commands](#commands)

---

## Quick Start

```bash
git clone https://github.com/theanibalos/StreamCoreOS
cd StreamCoreOS
cp .env.example .env          # fill in TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET
uv run main.py
# Visit http://localhost:8000/docs
# Visit http://localhost:8000/auth/twitch to authenticate
```

Dev infrastructure (SQLite is default, no setup needed):

```bash
docker compose -f dev_infra/docker-compose.yml up -d   # optional PostgreSQL
```

---

## Architecture

```
StreamCoreOS/
├── core/                        # MicroCoreOS kernel (~340 lines, zero external deps)
├── tools/
│   ├── twitch/                  # Twitch platform wrapper (OAuth + EventSub + IRC)
│   │   ├── twitch_tool.py       # Main facade — inject as 'twitch'
│   │   ├── _api.py              # Helix REST + OAuth client
│   │   ├── _eventsub.py         # EventSub WebSocket client
│   │   └── _chat.py             # IRC WebSocket client
│   ├── sqlite/                  # Default DB (inject as 'db')
│   ├── event_bus/               # Pub/Sub + async RPC (inject as 'event_bus')
│   ├── http_server/             # FastAPI gateway (inject as 'http')
│   ├── scheduler/               # Cron jobs (inject as 'scheduler')
│   ├── state/                   # In-memory key-value store (inject as 'state')
│   └── logger/                  # Structured logging (inject as 'logger')
└── domains/
    ├── twitch_auth/             # OAuth flow + token storage + session restore
    ├── stream_state/            # Online/offline tracking + history
    ├── chat_bot/                # IRC dispatch + commands + chat stream SSE
    ├── loyalty/                 # Points system + leaderboard + rewards
    ├── moderation/              # Auto-mod + manual ban/timeout/unban
    └── dashboard/               # Stats endpoint + real-time alerts SSE
```

**One rule:** 1 file = 1 feature. Every plugin lives in `domains/{domain}/plugins/` and is auto-discovered. Never touch `main.py`.

---

## Setup

### Environment Variables

```env
TWITCH_CLIENT_ID=your_client_id
TWITCH_CLIENT_SECRET=your_client_secret
TWITCH_REDIRECT_URI=http://localhost:8000/auth/twitch/callback
```

In your Twitch Developer Console, add `http://localhost:8000/auth/twitch/callback` as an OAuth redirect URI.

### Authentication Flow

1. `GET /auth/twitch` — generates the OAuth URL with all accumulated scopes
2. Click the link, authorize on Twitch
3. Twitch redirects to `/auth/twitch/callback` with a code
4. The callback plugin exchanges the code, saves the token to DB, and calls `twitch.connect()`
5. EventSub WebSocket connects, all registered subscriptions are created, IRC chat connects

On restart, `restore_session_plugin.py` reads the token from DB and reconnects automatically.

---

## How the Twitch Tool Works

The `twitch` tool is the single entry point for everything Twitch-related. Internally it manages three connections:

- **Helix API** — REST calls to `api.twitch.tv/helix`
- **EventSub WebSocket** — persistent connection to `wss://eventsub.wss.twitch.tv/ws`, receives real-time events
- **IRC WebSocket** — connection to `wss://irc-ws.chat.twitch.tv`, reads/writes chat

### Lifecycle

```
on_boot()  → plugins call register() and on_event()   (declares what they need)
             ↓
User visits /auth/twitch → authenticates on Twitch
             ↓
/auth/twitch/callback → calls twitch.connect(access_token, broadcaster_id, login)
             ↓
EventSub WS connects → session_welcome → subscriptions created via Helix
IRC WS connects → joins broadcaster channel
             ↓
Events arrive → dispatched to registered callbacks
```

### Subscription Deduplication

Multiple plugins can register the same event type. Only **one** Twitch subscription is created per event type — `TwitchTool` deduplicates them internally. You don't need to coordinate across plugins.

```python
# Plugin A registers channel.follow
self.twitch.register("channel.follow", "2", scopes=["moderator:read:followers"], condition={...})

# Plugin B also registers channel.follow — no duplicate subscription created
self.twitch.register("channel.follow", "2", scopes=["moderator:read:followers"], condition={...})
```

### Session Access

Plugins that need the broadcaster's credentials call `get_session()`:

```python
session = self.twitch.get_session()
if not session:
    return {"success": False, "error": "Twitch session not active"}

broadcaster_id = session["broadcaster_id"]
access_token = session["access_token"]
login = session["login"]
```

---

## Existing Domains

### `twitch_auth`

Handles OAuth and token persistence.

| Plugin | What it does |
|---|---|
| `twitch_oauth_start_plugin` | `GET /auth/twitch` — generates OAuth URL |
| `twitch_oauth_callback_plugin` | `GET /auth/twitch/callback` — exchanges code, saves token, connects |
| `twitch_token_refresh_plugin` | Cron `*/30 * * * *` — refreshes expiring tokens |
| `restore_session_plugin` | `on_boot` — restores the saved session from DB on restart |

DB table: `twitch_tokens` (twitch_id, login, access_token, refresh_token, scopes, expires_at)

---

### `stream_state`

Tracks whether the stream is online and its history.

| Plugin | What it does |
|---|---|
| `stream_status_plugin` | Listens to `stream.online` / `stream.offline` EventSub events |
| `get_stream_status_plugin` | `GET /stream/status` — returns current state from state tool |
| `stream_history_plugin` | `GET /stream/sessions?limit=20&offset=0` — paginated session history |
| `stream_state_rpc_plugin` | RPC `stream.status.requested` — returns state to internal callers |

State namespace `stream_state`: `online`, `session_id`, `started_at`, `broadcaster_login`

Publishes: `stream.session.started`, `stream.session.ended`

---

### `chat_bot`

IRC chat bridge, command system, and real-time chat stream.

| Plugin | What it does |
|---|---|
| `chat_message_dispatcher_plugin` | Receives all IRC messages, logs to DB, publishes events |
| `chat_command_handler_plugin` | Listens to `chat.command.received`, looks up command in DB |
| `chat_auto_response_plugin` | Sends auto-messages on follow, sub, resub, gift, raid |
| `chat_stream_plugin` | `GET /chat/stream` — SSE stream of all chat messages |
| `create_command_plugin` | `POST /chat/commands` |
| `list_commands_plugin` | `GET /chat/commands` |
| `update_command_plugin` | `PUT /chat/commands/{name}` |
| `delete_command_plugin` | `DELETE /chat/commands/{name}` |

Publishes: `chat.message.received`, `chat.command.received`, `chat.command.executed`

---

### `loyalty`

Points system for viewer engagement.

| Plugin | What it does |
|---|---|
| `award_points_plugin` | Awards points on follow/sub/resub/gift/cheer/raid |
| `chat_activity_points_plugin` | Awards 5pts per chat message (60s cooldown per user) |
| `get_viewer_points_plugin` | `GET /loyalty/points/{twitch_id}` |
| `leaderboard_plugin` | `GET /loyalty/leaderboard?limit=10` |
| `points_history_plugin` | `GET /loyalty/history/{twitch_id}` |
| `create_reward_plugin` | `POST /loyalty/rewards` |
| `list_rewards_plugin` | `GET /loyalty/rewards` |
| `redeem_reward_plugin` | `POST /loyalty/redeem` — atomic: check balance, deduct, record |

Points table: follow=100, subscribe=500, resub=300, gift=200×count, cheer=1×bits, raid=10×viewers

Publishes: `loyalty.points.awarded`, `loyalty.reward.redeemed`

---

### `moderation`

Auto-moderation and manual controls.

| Plugin | What it does |
|---|---|
| `auto_mod_plugin` | Listens to `chat.message.received`, evaluates rules, bans/timeouts |
| `manual_ban_plugin` | `POST /moderation/ban` |
| `manual_timeout_plugin` | `POST /moderation/timeout` |
| `manual_unban_plugin` | `POST /moderation/unban` |
| `create_mod_rule_plugin` | `POST /moderation/rules` |
| `list_mod_rules_plugin` | `GET /moderation/rules` |
| `update_mod_rule_plugin` | `PUT /moderation/rules/{id}` |
| `delete_mod_rule_plugin` | `DELETE /moderation/rules/{id}` |
| `mod_log_plugin` | `GET /moderation/log` |

Rule types: `word_filter`, `link_filter`, `caps_filter`, `spam_filter`
Actions: `ban`, `timeout` (with `duration_s`), `delete`

Rules are cached in state (namespace `moderation_rules`). Cache invalidated via `moderation.rules.updated`.

Publishes: `moderation.action.taken`, `moderation.rules.updated`

---

### `dashboard`

Aggregated stats and real-time alert stream.

| Plugin | What it does |
|---|---|
| `dashboard_stats_plugin` | `GET /dashboard/stats` — stream info, top viewers, recent mod actions |
| `dashboard_alerts_plugin` | `GET /dashboard/alerts` — SSE stream of all Twitch events + internal events |
| `channel_stats_collector_plugin` | Cron `*/5 * * * *` — snapshots viewer/follower count to DB |
| `channel_stats_history_plugin` | `GET /dashboard/stats/history` |

`dashboard_alerts_plugin` uses the wildcard `twitch.on_event("*", ...)` — receives all EventSub events enriched with `_event_type`. Also subscribes to `stream.session.started/ended`, `loyalty.reward.redeemed`, `moderation.action.taken`.

---

## Event Catalog

All events published on the internal event bus:

| Event | Published by | Payload keys |
|---|---|---|
| `stream.session.started` | `stream_status_plugin` | session_id, twitch_stream_id, started_at, broadcaster_login |
| `stream.session.ended` | `stream_status_plugin` | session_id, ended_at |
| `chat.message.received` | `chat_message_dispatcher_plugin` | channel, user_id, display_name, message, is_mod, is_sub, is_broadcaster, badges, timestamp |
| `chat.command.received` | `chat_message_dispatcher_plugin` | (all of above) + command, args |
| `chat.command.executed` | `chat_command_handler_plugin` | command, user_id, display_name |
| `loyalty.points.awarded` | `award_points_plugin`, `chat_activity_points_plugin` | twitch_id, display_name, amount, reason |
| `loyalty.reward.redeemed` | `redeem_reward_plugin` | twitch_id, display_name, reward_id, reward_name, cost |
| `moderation.action.taken` | `auto_mod_plugin` | twitch_id, display_name, action, reason, rule_id |
| `moderation.rules.updated` | rule CRUD plugins | rule_id, action |

---

## How to Write a New Feature

### 1. HTTP Endpoint

Pattern for any REST endpoint. One file, one endpoint.

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
    """POST /my-domain/things — creates a thing."""

    def __init__(self, http, db, event_bus, logger):
        self.http = http
        self.db = db
        self.bus = event_bus
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/my-domain/things", "POST", self.execute,
            tags=["MyDomain"],
            request_model=MyRequest,
            response_model=MyResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            req = MyRequest(**data)
            row_id = await self.db.execute(
                "INSERT INTO things (name) VALUES ($1) RETURNING id",
                [req.name],
            )
            await self.bus.publish("my_domain.thing.created", {"id": row_id, "name": req.name})
            return {"success": True, "data": {"id": row_id}}
        except Exception as e:
            self.logger.error(f"[MyFeature] {e}")
            return {"success": False, "error": str(e)}
```

Drop it in `domains/my_domain/plugins/` and restart. No other changes needed.

---

### 2. Twitch EventSub Listener

For reacting to Twitch platform events (follows, subs, raids, etc).

```python
# domains/my_domain/plugins/on_follow_plugin.py

from core.base_plugin import BasePlugin


class OnFollowPlugin(BasePlugin):
    """Reacts to new Twitch followers."""

    def __init__(self, twitch, logger):
        self.twitch = twitch
        self.logger = logger

    async def on_boot(self):
        # Declare the subscription and its required OAuth scope
        self.twitch.register(
            "channel.follow", "2",
            scopes=["moderator:read:followers"],
            condition={
                "broadcaster_user_id": "{broadcaster_id}",
                "moderator_user_id": "{broadcaster_id}",
            },
        )
        # Register your callback — deduplication is automatic
        self.twitch.on_event("channel.follow", self._on_follow)

    async def _on_follow(self, event: dict):
        user = event.get("user_name", "someone")
        self.logger.info(f"New follower: {user}")
```

**Key points:**
- `{broadcaster_id}` in the condition is replaced automatically when `connect()` is called
- If another plugin already registered `channel.follow`, only one Twitch subscription is created
- The OAuth scope is accumulated globally across all plugins for the auth URL

**Common EventSub event types:**

| Event type | Version | Scopes required |
|---|---|---|
| `stream.online` | `1` | none |
| `stream.offline` | `1` | none |
| `channel.follow` | `2` | `moderator:read:followers` (needs moderator_user_id in condition) |
| `channel.subscribe` | `1` | `channel:read:subscriptions` |
| `channel.subscription.message` | `1` | `channel:read:subscriptions` |
| `channel.subscription.gift` | `1` | `channel:read:subscriptions` |
| `channel.cheer` | `1` | `bits:read` |
| `channel.raid` | `1` | none (use `to_broadcaster_user_id` in condition) |
| `channel.channel_points_custom_reward_redemption.add` | `1` | `channel:read:redemptions` |

For the full list: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types/

---

### 3. Chat Listener

Don't hook into the IRC tool directly. Subscribe to `chat.message.received` on the event bus — `ChatMessageDispatcherPlugin` already bridges IRC to the bus.

```python
# domains/my_domain/plugins/chat_reaction_plugin.py

from core.base_plugin import BasePlugin


class ChatReactionPlugin(BasePlugin):
    """Reacts to chat messages."""

    def __init__(self, event_bus, twitch, logger):
        self.bus = event_bus
        self.twitch = twitch
        self.logger = logger

    async def on_boot(self):
        await self.bus.subscribe("chat.message.received", self._on_message)

    async def _on_message(self, msg: dict):
        # msg keys: channel, user_id, display_name, message,
        #           is_mod, is_sub, is_broadcaster, badges, timestamp
        if "!hello" in msg["message"].lower():
            session = self.twitch.get_session()
            if session:
                await self.twitch.send_message(session["login"], f"Hello, {msg['display_name']}!")
```

For commands specifically, subscribe to `chat.command.received` — it includes `command` and `args` keys.

---

### 4. Scheduled Job

```python
# domains/my_domain/plugins/hourly_cleanup_plugin.py

from core.base_plugin import BasePlugin


class HourlyCleanupPlugin(BasePlugin):
    """Deletes old records every hour."""

    def __init__(self, scheduler, db, logger):
        self.scheduler = scheduler
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.scheduler.add_job(
            "0 * * * *",          # cron expression
            self._cleanup,
            job_id="hourly_cleanup",
        )

    async def _cleanup(self):
        try:
            await self.db.execute(
                "DELETE FROM my_table WHERE created_at < datetime('now', '-7 days')"
            )
        except Exception as e:
            self.logger.error(f"[HourlyCleanup] {e}")
```

---

### 5. Event Bus Consumer

```python
# domains/my_domain/plugins/on_stream_start_plugin.py

from core.base_plugin import BasePlugin


class OnStreamStartPlugin(BasePlugin):
    """Does something when the stream goes online."""

    def __init__(self, event_bus, twitch, logger):
        self.bus = event_bus
        self.twitch = twitch
        self.logger = logger

    async def on_boot(self):
        await self.bus.subscribe("stream.session.started", self._on_start)

    async def _on_start(self, data: dict):
        session = self.twitch.get_session()
        if session:
            await self.twitch.send_message(session["login"], "Stream is live! PogChamp")
```

See the [Event Catalog](#event-catalog) for all available events.

---

### 6. Domain with DB Migration

To add a new domain that needs its own table:

**1. Create the migration:**

```sql
-- domains/my_domain/migrations/001_create_my_table.sql
CREATE TABLE IF NOT EXISTS my_table (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
```

**2. Create the model (DB mirror only):**

```python
# domains/my_domain/models/my_entity.py
from pydantic import BaseModel

class MyEntity(BaseModel):
    id: int
    name: str
    created_at: str
```

**3. Create plugins in `domains/my_domain/plugins/`**

The kernel auto-discovers migrations and runs them in order on boot. No registration needed.

---

## Available Tools Reference

Tools are injected by parameter name in `__init__`.

### `twitch`

```python
# Registration (call in on_boot)
twitch.register(event_type, version, scopes, condition=None)
twitch.require_scopes(scopes)           # for IRC scopes without EventSub
twitch.on_event(event_type, callback)   # use '*' for all events
twitch.on_chat_message(callback)
twitch.on_chat_connect(callback)

# OAuth
url, state = twitch.get_auth_url()
tokens = await twitch.exchange_code(code)
tokens = await twitch.refresh_user_token(refresh_token)
user   = await twitch.get_user_info(access_token)

# Connection
await twitch.connect(access_token, broadcaster_id, twitch_login)
session = twitch.get_session()          # {access_token, broadcaster_id, login} or None

# Chat
await twitch.send_message(channel, message)

# Helix API
data = await twitch.get(endpoint, params=None, user_token=None)
data = await twitch.post(endpoint, body=None, user_token=None)
data = await twitch.delete(endpoint, params=None, user_token=None)
```

**Wildcard events:** When using `on_event("*", callback)`, the event data is enriched with `_event_type` key so you can distinguish the source:

```python
async def _on_any_event(self, event: dict):
    event_type = event.pop("_event_type")   # e.g. "channel.follow"
    # rest of event is the normal Twitch payload
```

---

### `db`

```python
rows    = await db.query("SELECT * FROM table WHERE x=$1", [val])
row     = await db.query_one("SELECT * FROM table WHERE id=$1", [id])
row_id  = await db.execute("INSERT INTO table (col) VALUES ($1) RETURNING id", [val])

async with db.transaction() as tx:
    await tx.execute(...)
    await tx.query_one(...)
```

Always use `$1, $2` placeholders (works for both SQLite and PostgreSQL).

---

### `event_bus`

```python
await event_bus.publish("domain.thing.happened", {"key": "value"})
await event_bus.subscribe("domain.thing.happened", async_callback)
result = await event_bus.request("some.rpc.topic", {"key": "value"}, timeout=5.0)
```

---

### `http`

```python
http.add_endpoint(
    path,
    method,            # "GET", "POST", "PUT", "DELETE"
    handler,
    tags=[],
    request_model=None,
    response_model=None,
)
```

Handler signature: `async def execute(self, data: dict, context=None)`

---

### `scheduler`

```python
scheduler.add_job(
    "*/5 * * * *",    # cron expression
    async_callable,
    job_id="unique_id",
)
```

---

### `state`

In-memory key-value store. Use for volatile, non-persistent data (caches, flags, cooldowns).

```python
state.set("key", value, namespace="my_plugin")
value = state.get("key", default=None, namespace="my_plugin")
state.delete("key", namespace="my_plugin")
```

---

### `logger`

```python
logger.info("message")
logger.error("message")
logger.warning("message")
logger.debug("message")
```

---

## Developing with AI

Every plugin in this project follows the same pattern. Because the architecture is strict and predictable, you can describe a feature in plain language and an AI assistant will generate a working plugin with no additional context — as long as you give it the right files to read first.

### Reading path for the AI

Always start your prompt with:

> Read `AI_CONTEXT.md` and `domains/{domain}/models/{model}.py`, then write the plugin.

That's all the context needed. `AI_CONTEXT.md` contains the full tool reference and the plugin contract. The model file shows the DB schema.

---

### The two-step workflow

Every feature starts with a plan, not code. This keeps you in control and avoids surprises.

**Step 1 — Ask for a plan**

Describe what you want in plain language. The AI reads the codebase and proposes exactly what it will create before touching any file.

**Step 2 — Approve and execute**

Review the plan. If it looks right, say "go ahead". If not, correct it before any code is written.

---

### Example prompts

**New endpoint (e.g. adjust points manually)**

> Step 1 — Plan:
> ```
> Read AI_CONTEXT.md and domains/loyalty/models/viewer_points.py.
> I want an endpoint to manually add or remove points from a viewer.
> Propose a plan: what file you'll create, what the endpoint looks like,
> what DB operation it does, and what event it publishes. Don't write code yet.
> ```

> Step 2 — Execute (after approving the plan):
> ```
> The plan looks good. Go ahead and implement it.
> ```

---

**React to a Twitch event (e.g. announce stream start in chat)**

> Step 1 — Plan:
> ```
> Read AI_CONTEXT.md.
> When the stream goes online I want the bot to send a welcome message in chat.
> Propose a plan: what plugin you'll create, where it lives, what event it listens to,
> and what message it sends. Don't write code yet.
> ```

> Step 2 — Execute:
> ```
> Perfect. Implement it.
> ```

---

**New auto-moderation rule type**

> Step 1 — Plan:
> ```
> Read AI_CONTEXT.md and domains/moderation/plugins/auto_mod_plugin.py.
> I want a new rule type that detects messages with too many emotes.
> Propose a plan: how you'll detect it, where in the code you'll add it,
> and what the rule value field would contain. Don't write code yet.
> ```

> Step 2 — Execute:
> ```
> Looks good. Go ahead.
> ```

---

**Scheduled job (e.g. log stream title changes)**

> Step 1 — Plan:
> ```
> Read AI_CONTEXT.md.
> I want a job that runs every 10 minutes and logs whenever the stream title changes.
> Propose a plan: what plugin you'll create, how it detects the change,
> what tool it uses to store the last seen title, and what it logs.
> Don't write code yet.
> ```

> Step 2 — Execute:
> ```
> Go ahead.
> ```

---

**New domain from scratch (e.g. quotes system)**

> Step 1 — Plan:
> ```
> Read AI_CONTEXT.md.
> I want a quotes system: viewers can add quotes in chat with !addquote,
> and !quote shows a random one. Also a REST API to manage quotes.
> Propose a complete plan: migration, model, and each plugin with its
> responsibility. Don't write any files yet.
> ```

> Step 2 — Execute:
> ```
> The plan looks good, go ahead and create everything.
> ```

---

**Add real-time SSE output to an existing feature**

> Step 1 — Plan:
> ```
> Read AI_CONTEXT.md and domains/dashboard/plugins/dashboard_alerts_plugin.py.
> I want an SSE endpoint that streams loyalty events in real time
> (points awarded and rewards redeemed).
> Propose a plan following the same pattern as dashboard_alerts_plugin.
> Don't write code yet.
> ```

> Step 2 — Execute:
> ```
> Perfect. Implement it.
> ```

---

### Tips for better results

- **One feature per prompt** — the architecture enforces 1 file = 1 feature; prompts work best the same way.
- **Always plan first** — reviewing a plan takes 10 seconds; reviewing broken code takes much longer.
- **Reference existing plugins by name** — `"following the same pattern as dashboard_alerts_plugin"` is enough context.
- **Correct the plan, not the code** — if the plan is wrong, fix it in the plan step before any file is touched.

---

## API Reference

Full interactive docs at `http://localhost:8000/docs` when the server is running.

| Method | Path | Description |
|---|---|---|
| GET | `/auth/twitch` | Start OAuth flow |
| GET | `/auth/twitch/callback` | OAuth callback (Twitch redirects here) |
| GET | `/stream/status` | Current stream state |
| GET | `/stream/sessions` | Session history |
| GET | `/chat/stream` | SSE — live chat messages |
| GET | `/chat/commands` | List chat commands |
| POST | `/chat/commands` | Create chat command |
| PUT | `/chat/commands/{name}` | Update chat command |
| DELETE | `/chat/commands/{name}` | Delete chat command |
| GET | `/loyalty/leaderboard` | Top viewers by points |
| GET | `/loyalty/points/{twitch_id}` | Points for a viewer |
| GET | `/loyalty/history/{twitch_id}` | Points transaction history |
| GET | `/loyalty/rewards` | List rewards |
| POST | `/loyalty/rewards` | Create reward |
| POST | `/loyalty/redeem` | Redeem a reward |
| GET | `/moderation/rules` | List mod rules |
| POST | `/moderation/rules` | Create mod rule |
| PUT | `/moderation/rules/{id}` | Update mod rule |
| DELETE | `/moderation/rules/{id}` | Delete mod rule |
| GET | `/moderation/log` | Mod action log |
| POST | `/moderation/ban` | Manually ban a user |
| POST | `/moderation/timeout` | Manually timeout a user |
| POST | `/moderation/unban` | Manually unban a user |
| GET | `/dashboard/stats` | Aggregated stream stats |
| GET | `/dashboard/alerts` | SSE — real-time event stream |
| GET | `/dashboard/stats/history` | Viewer/follower count history |

---

## Commands

```bash
uv run main.py                                              # Run
uv run pytest                                               # All tests
uv run pytest tests/test_file.py                            # Single test
docker compose -f dev_infra/docker-compose.yml up -d        # Dev infra
```

---

**Built by Anibal Fernandez on [MicroCoreOS](https://github.com/theanibalos/MicroCoreOS)**
