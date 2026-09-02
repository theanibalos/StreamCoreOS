# StreamCoreOS — Multiplatform Events Contract

This is the internal event contract for platform adapters (Twitch, YouTube, future Kick/TikTok/etc.). No legacy compatibility is required: consumers should read these shapes directly.

## Common identity

`user.id` is globally unique and prefixed:

- `twitch:<twitch_user_id>`
- `youtube:<youtube_channel_id>`

`user.platform_id` is the raw platform ID.

## `chat.message.received`

Published by platform chat adapters when a visible chat message arrives.

```py
{
  "platform": "twitch" | "youtube",
  "channel_id": str,
  "channel_name": str,
  "message_id": str,
  "message": str,
  "fragments": list[dict],
  "user": {
    "id": str,
    "platform_id": str,
    "login": str | None,
    "display_name": str,
    "avatar_url": str | None,
  },
  "roles": {
    "broadcaster": bool,
    "moderator": bool,
    "subscriber": bool,
    "vip": bool,
    "verified": bool,
  },
  "badges": list[dict],
  "raw": dict,
  "timestamp": str,
}
```

## `chat.command.received`

Same payload as `chat.message.received`, plus:

```py
{
  "command": "!name",
  "args": "rest of message"
}
```

## `chat.message.deleted`

```py
{
  "platform": str,
  "channel_id": str,
  "message_id": str,
  "raw": dict,
  "timestamp": str,
}
```

## `chat.message.send`

Request to send a message to a platform/channel. A platform router should be the only consumer that calls concrete tools.

```py
{
  "platform": str,
  "channel_id": str,
  "channel_name": str | None,
  "message": str,
}
```

## `monetization.event.received`

```py
{
  "platform": str,
  "channel_id": str,
  "type": "superchat" | "supersticker" | "member" | "sub" | "bits",
  "user": {...},
  "amount_micros": int | None,
  "currency": str | None,
  "display_amount": str | None,
  "message": str,
  "raw": dict,
  "timestamp": str,
}
```

## `moderation.action.requested`

```py
{
  "platform": str,
  "channel_id": str,
  "message_id": str | None,
  "user": {
    "id": str,
    "platform_id": str,
    "display_name": str,
  },
  "action": "delete" | "timeout" | "ban" | "unban",
  "duration_s": int | None,
  "reason": str,
  "rule_id": int | None,
}
```

## `moderation.action.taken`

Same as `moderation.action.requested`, plus execution status fields when needed.

## `platform.connection.updated`

```py
{
  "id": int,
  "platform": "twitch" | "youtube",
  "channel_id": str,
  "channel_name": str,
  "enabled": bool,
  "chat_read_enabled": bool,
  "chat_write_enabled": bool,
  "moderation_enabled": bool,
  "capabilities": dict,
}
```
